"""Bounded Nikon HE entropy primitives for the guarded native profile adapter.

Coefficients alone are not sensor samples. Original files are never writable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class HeFormatError(ValueError):
    pass


class BitReader:
    """Bounded MSB-first reads; padding cannot satisfy a truncated codeword."""

    def __init__(self, data):
        self.data = data
        self.position = 0

    def read(self, count):
        start = self.position
        end = start + count
        if count < 0 or end > len(self.data) * 8:
            raise HeFormatError("Truncated HE bitstream")
        self.position = end
        if not count:
            return 0
        stop_byte = (end + 7) // 8
        word = int.from_bytes(self.data[start // 8:stop_byte], "big")
        return (word >> (stop_byte * 8 - end)) & ((1 << count) - 1)

    def unary(self):
        value = 0
        while self.read(1):
            value += 1
            if value > 31:
                raise HeFormatError("HE unary code exceeds the supported range")
        return value

    def finish(self):
        remaining = len(self.data) * 8 - self.position
        if remaining > 7 or self.read(remaining):
            raise HeFormatError("Unexpected HE substream data or nonzero padding")


@dataclass(frozen=True)
class HeHeader:
    width: int
    height: int
    precinct_start: int
    weights: tuple[tuple[int, int], ...]
    picture_payload: bytes
    component_payload: bytes


def read_header(data):
    if bytes(data[:4]) != b"\xff\x10\xff\x50":
        raise HeFormatError("Missing HE signature")
    position = 2
    payloads = {}
    for marker in (0xFF50, 0xFF12, 0xFF13, 0xFF14, 0xFF20):
        if position + 4 > len(data):
            raise HeFormatError("Truncated HE marker")
        actual = int.from_bytes(data[position:position + 2], "big")
        size = int.from_bytes(data[position + 2:position + 4], "big")
        if actual != marker or size < 2 or position + 2 + size > len(data):
            raise HeFormatError("Unsupported HE header sequence or marker length")
        payloads[marker] = bytes(data[position + 4:position + 2 + size])
        position += size + 2
    picture = payloads[0xFF12]
    components = payloads[0xFF13]
    weights = payloads[0xFF14]
    if len(picture) != 37 or len(components) != 12 or len(weights) != 50:
        raise HeFormatError("Unsupported Nikon HE picture/component layout")
    width = int.from_bytes(picture[8:10], "big")
    height = int.from_bytes(picture[10:12], "big")
    if width < 64 or width % 8 or height <= 0 or height % 4 or width * height > 64_000_000:
        raise HeFormatError("Unsupported Nikon HE dimensions")
    if int.from_bytes(picture[:4], "big") != len(data):
        raise HeFormatError("HE declared codestream length does not match the strip")
    if picture[12:20] != bytes((0, 0, 0, 16, 4, 4, 8, 18)):
        raise HeFormatError("Unsupported Nikon HE precinct/group configuration")
    if components != bytes((14, 17, 81, 14, 17, 81, 14, 17, 0, 14, 17, 81)):
        raise HeFormatError("Unsupported Nikon HE component decomposition")
    if payloads[0xFF20] != b"\x00\x00":
        raise HeFormatError("Unexpected first HE slice")
    return HeHeader(width, height, position, tuple(zip(weights[::2], weights[1::2])), picture, components)


def precincts(data, header):
    position = header.precinct_start
    count = header.height // 4
    if count > (len(data) - position) // 12:
        raise HeFormatError("HE dimensions exceed available precinct data")
    for index in range(count):
        if position + 12 > len(data):
            raise HeFormatError("Truncated HE precinct header")
        size = int.from_bytes(data[position:position + 3], "big") + 12
        if size < 68 or position + size > len(data):
            raise HeFormatError("Invalid HE precinct size")
        yield index, memoryview(data)[position:position + size]
        position += size
        if (index + 1) % 16 == 0 and index + 1 < count:
            expected = b"\xff\x20\x00\x04" + ((index + 1) // 16).to_bytes(2, "big")
            if bytes(data[position:position + 6]) != expected:
                raise HeFormatError("Invalid HE slice sequence")
            position += 6
    if bytes(data[position:]) != b"\xff\x11":
        raise HeFormatError("HE stream does not end at its declared boundary")


def band_groups(width):
    # Five horizontal low/high splits in three component rows; the second
    # row of each component has one split. The fourth component is unfiltered.
    low = width // 2
    highs = []
    for _ in range(5):
        highs.append(low // 2)
        low -= low // 2
    deep = tuple((size + 3) // 4 for size in (low, *reversed(highs)))
    flat = (width // 8,)
    shallow = ((width + 15) // 16,) * 2
    return (deep, deep, flat, deep, shallow, shallow, flat, shallow)


def truncation_levels(weights, quantization, refinement):
    if len(weights) != 25 or not 0 <= quantization <= 15 or not 0 <= refinement <= 25:
        raise HeFormatError("Unsupported HE quantization configuration")
    # Both rows of the unfiltered component share the same weight entry.
    expanded = (*weights[:23], weights[12], *weights[23:])
    levels = tuple(max(0, quantization - gain - int(priority < refinement)) for gain, priority in expanded)
    if any(level > 15 for level in levels):
        raise HeFormatError("HE truncation level exceeds the supported range")
    return levels


def predict_gcli(threshold, previous, residual):
    baseline = max(threshold, previous)
    distance = baseline - threshold
    if residual <= 2 * distance:
        return baseline + residual // 2 if residual % 2 == 0 else baseline - (residual + 1) // 2
    return threshold + residual


def decode_gcli(significance, codes, groups, threshold, previous=None):
    result = np.empty(groups, dtype=np.uint8)
    for start in range(0, groups, 8):
        insignificant = significance.read(1)
        for index in range(start, min(start + 8, groups)):
            prior = int(previous[index]) if previous is not None else 0
            value = predict_gcli(threshold, prior, 0 if insignificant else codes.unary())
            if not 0 <= value <= 15:
                raise HeFormatError("HE GCLI value is outside the supported coefficient range")
            result[index] = value
    return result


def decode_magnitudes(data, signs, gcli, threshold):
    coefficients = np.zeros((len(gcli), 4), dtype=np.int32)
    for index, depth in enumerate(gcli):
        for plane in range(int(depth) - 1, threshold - 1, -1):
            nibble = data.read(4)
            for channel in range(4):
                coefficients[index, channel] |= ((nibble >> (3 - channel)) & 1) << plane
        for channel in range(4):
            if coefficients[index, channel] and signs.read(1):
                coefficients[index, channel] *= -1
    return coefficients.ravel()


def dequantize(coefficients, gcli, threshold):
    """Reconstruct magnitudes, optionally batching precincts on leading axes."""
    shape = (*coefficients.shape[:-1], -1, 4)
    magnitude = np.abs(coefficients.astype(np.int64)).reshape(shape)
    shift = np.maximum(1, gcli.astype(np.int64) - threshold + 1)[..., None]
    reconstructed = magnitude.copy()
    term = magnitude >> shift
    while np.any(term):
        reconstructed += term
        term >>= shift
    return (np.sign(coefficients).reshape(shape) * reconstructed * 16).astype(np.int32).reshape(coefficients.shape)


@dataclass(frozen=True)
class EntropyPrecinct:
    gcli: tuple[np.ndarray, ...]
    coefficients: tuple[np.ndarray, ...]
    thresholds: tuple[int, ...]
    packets: tuple[tuple[int, int, int, int], ...]


def packet_streams(precinct, packet_groups):
    """Yield bounded packet slices; exhaustion validates trailing padding."""
    if len(precinct) < 68 or int.from_bytes(precinct[:3], "big") + 12 != len(precinct):
        raise HeFormatError("Invalid HE precinct length")
    if int.from_bytes(precinct[5:12], "big") != ((1 << 50) - 1) << 6:
        raise HeFormatError("Unsupported HE depth-hint profile")
    position = 12
    for groups in packet_groups:
        sizes = BitReader(precinct[position:position + 7])
        if sizes.read(1):
            raise HeFormatError("HE raw packet coding is not implemented")
        data_size, gcli_size, signs_size = sizes.read(20), sizes.read(20), sizes.read(15)
        significance_size = (sum((count + 7) // 8 for count in groups) + 7) // 8
        lengths = (significance_size, gcli_size, data_size, signs_size)
        position += 7
        streams = []
        for count in lengths:
            if position + count > len(precinct):
                raise HeFormatError("HE packet exceeds its precinct")
            streams.append(memoryview(precinct)[position:position + count])
            position += count
        yield tuple(streams), lengths
    if any(precinct[position:]):
        raise HeFormatError("Nonzero HE precinct padding")


def validate_packet_profile(data, header):
    groups = band_groups(header.width)
    for _index, block in precincts(data, header):
        truncation_levels(header.weights, block[3], block[4])
        for _streams, _lengths in packet_streams(block, groups):
            pass


class HeEntropyDecoder:
    """Decode quantized wavelet coefficients; these are NOT Bayer samples."""

    def __init__(self, header, *, accelerated=True):
        self.header = header
        self.groups = band_groups(header.width)
        self.previous = tuple(np.zeros(count, dtype=np.uint8) for packet in self.groups for count in packet)
        self.index = 0
        self.accelerated = accelerated

    def decode(self, precinct):
        if len(precinct) < 68 or int.from_bytes(precinct[:3], "big") + 12 != len(precinct):
            raise HeFormatError("Invalid HE precinct length")
        thresholds = truncation_levels(self.header.weights, precinct[3], precinct[4])
        previous = self.previous if self.index % 16 else tuple(np.zeros_like(band) for band in self.previous)
        all_gcli, all_coefficients, packets = [], [], []
        for packet, (streams, lengths) in enumerate(packet_streams(precinct, self.groups)):
            groups = self.groups[packet]
            readers = [BitReader(stream) for stream in streams]
            sig, codes, magnitudes, signs = readers
            packets.append(lengths)
            if self.accelerated:
                from .compiled_he import decode_packet

                start = len(all_gcli)
                prediction = tuple(
                    all_gcli[12] if band == 23 else previous[23] if band == 12 else previous[band]
                    for band in range(start, start + len(groups))
                )
                try:
                    decoded = decode_packet(
                        tuple(reader.data for reader in readers), groups,
                        thresholds[start:start + len(groups)], prediction,
                    )
                except ValueError as error:
                    raise HeFormatError(f"Precinct {self.index}, packet {len(packets) - 1}: {error}") from error
                if decoded is not None:
                    lengths, coefficients = decoded
                    cursor = 0
                    for count in groups:
                        all_gcli.append(lengths[cursor:cursor + count])
                        all_coefficients.append(coefficients[cursor * 4:(cursor + count) * 4])
                        cursor += count
                    continue
            for count in groups:
                band = len(all_gcli)
                threshold = thresholds[band]
                prediction = all_gcli[12] if band == 23 else previous[23] if band == 12 else previous[band]
                lengths = decode_gcli(sig, codes, count, threshold, prediction)
                coefficients = decode_magnitudes(magnitudes, signs, lengths, threshold)
                all_gcli.append(lengths)
                all_coefficients.append(coefficients)
            for label, reader in zip(("significance", "GCLI", "magnitudes", "signs"), readers):
                try:
                    reader.finish()
                except HeFormatError as error:
                    raise HeFormatError(f"Precinct {self.index}, packet {len(packets) - 1}, {label}: {error}") from error
        # Failed packets must not poison the context for a retry.
        self.previous = tuple(band.copy() for band in all_gcli)
        self.index += 1
        return EntropyPrecinct(tuple(all_gcli), tuple(all_coefficients), thresholds, tuple(packets))
