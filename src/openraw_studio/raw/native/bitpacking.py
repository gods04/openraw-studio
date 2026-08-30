"""Bit packing helpers for uncompressed Bayer sensor payloads."""

from __future__ import annotations

import struct


SUPPORTED_SENSOR_BIT_DEPTHS = frozenset({12, 14, 16})
PACKED_SENSOR_BIT_DEPTHS = frozenset({12, 14})


class SensorBitPackingError(ValueError):
    """Raised when packed sensor bytes cannot be decoded."""


def expected_row_aligned_byte_count(
    *,
    width: int,
    height: int,
    samples_per_pixel: int,
    bits_per_sample: int,
) -> int:
    """Return the expected byte count for row-aligned unpacked TIFF strips."""

    if width <= 0 or height <= 0:
        raise SensorBitPackingError("sensor dimensions must be greater than zero")
    return height * packed_row_byte_count(
        width=width,
        samples_per_pixel=samples_per_pixel,
        bits_per_sample=bits_per_sample,
    )


def packed_row_byte_count(*, width: int, samples_per_pixel: int, bits_per_sample: int) -> int:
    """Return bytes needed for one row when samples are packed bit-by-bit."""

    if width <= 0:
        raise SensorBitPackingError("sensor width must be greater than zero")
    if samples_per_pixel <= 0:
        raise SensorBitPackingError("SamplesPerPixel must be greater than zero")
    if bits_per_sample <= 0:
        raise SensorBitPackingError("BitsPerSample must be greater than zero")
    row_bits = width * samples_per_pixel * bits_per_sample
    return (row_bits + 7) // 8


def unpack_row_aligned_samples(
    raw_bytes: bytes,
    *,
    width: int,
    height: int,
    samples_per_pixel: int,
    bits_per_sample: int,
    byte_order: str,
) -> tuple[int, ...]:
    """Decode row-aligned 12/14/16-bit integer sensor samples."""

    if bits_per_sample not in SUPPORTED_SENSOR_BIT_DEPTHS:
        raise SensorBitPackingError(
            f"OpenRAW currently supports only 12-bit, 14-bit, or 16-bit sensor data, not {bits_per_sample}-bit"
        )

    expected_bytes = expected_row_aligned_byte_count(
        width=width,
        height=height,
        samples_per_pixel=samples_per_pixel,
        bits_per_sample=bits_per_sample,
    )
    if len(raw_bytes) < expected_bytes:
        raise SensorBitPackingError(f"sensor payload is shorter than expected: {len(raw_bytes)} < {expected_bytes}")

    sample_count = width * height * samples_per_pixel
    if bits_per_sample == 16:
        endian = _struct_endian(byte_order)
        return struct.unpack(endian + f"{sample_count}H", raw_bytes[:expected_bytes])

    samples_per_row = width * samples_per_pixel
    row_bytes = packed_row_byte_count(
        width=width,
        samples_per_pixel=samples_per_pixel,
        bits_per_sample=bits_per_sample,
    )
    values: list[int] = []
    for row in range(height):
        start = row * row_bytes
        values.extend(_unpack_msb_bitstream(raw_bytes[start : start + row_bytes], bits_per_sample, samples_per_row))
    return tuple(values)


def pack_row_aligned_samples(
    values: tuple[int, ...],
    *,
    width: int,
    height: int,
    samples_per_pixel: int,
    bits_per_sample: int,
    byte_order: str,
) -> bytes:
    """Pack row-aligned 12/14/16-bit integer sensor samples."""

    if bits_per_sample not in SUPPORTED_SENSOR_BIT_DEPTHS:
        raise SensorBitPackingError(
            f"OpenRAW currently supports only 12-bit, 14-bit, or 16-bit sensor data, not {bits_per_sample}-bit"
        )
    expected_row_aligned_byte_count(
        width=width,
        height=height,
        samples_per_pixel=samples_per_pixel,
        bits_per_sample=bits_per_sample,
    )

    sample_count = width * height * samples_per_pixel
    if len(values) != sample_count:
        raise SensorBitPackingError(f"sensor sample count is {len(values)}; expected {sample_count}")

    maximum = (1 << bits_per_sample) - 1
    for value in values:
        if value < 0 or value > maximum:
            raise SensorBitPackingError(f"sample value {value} is outside {bits_per_sample}-bit range")

    if bits_per_sample == 16:
        endian = _struct_endian(byte_order)
        return struct.pack(endian + f"{sample_count}H", *values)

    samples_per_row = width * samples_per_pixel
    rows = []
    for row in range(height):
        start = row * samples_per_row
        rows.append(_pack_msb_bitstream(values[start : start + samples_per_row], bits_per_sample))
    return b"".join(rows)


def _unpack_msb_bitstream(raw_bytes: bytes, bits_per_sample: int, sample_count: int) -> tuple[int, ...]:
    values: list[int] = []
    mask = (1 << bits_per_sample) - 1
    accumulator = 0
    available_bits = 0

    for byte in raw_bytes:
        accumulator = (accumulator << 8) | byte
        available_bits += 8
        while available_bits >= bits_per_sample and len(values) < sample_count:
            shift = available_bits - bits_per_sample
            values.append((accumulator >> shift) & mask)
            accumulator &= (1 << shift) - 1
            available_bits = shift

    if len(values) != sample_count:
        raise SensorBitPackingError(f"packed sensor row has {len(values)} samples; expected {sample_count}")
    return tuple(values)


def _pack_msb_bitstream(values: tuple[int, ...], bits_per_sample: int) -> bytes:
    output = bytearray()
    accumulator = 0
    available_bits = 0

    for value in values:
        accumulator = (accumulator << bits_per_sample) | value
        available_bits += bits_per_sample
        while available_bits >= 8:
            shift = available_bits - 8
            output.append((accumulator >> shift) & 0xFF)
            accumulator &= (1 << shift) - 1
            available_bits = shift

    if available_bits:
        output.append((accumulator << (8 - available_bits)) & 0xFF)
    return bytes(output)


def _struct_endian(byte_order: str) -> str:
    if byte_order == "little":
        return "<"
    if byte_order == "big":
        return ">"
    raise SensorBitPackingError(f"unsupported byte order: {byte_order}")
