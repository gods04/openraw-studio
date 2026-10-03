import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from openraw_studio.raw.native import compiled_he, he_cpu
from openraw_studio.raw.native.he import (
    BitReader,
    HeEntropyDecoder,
    HeFormatError,
    HeHeader,
    band_groups,
    decode_gcli,
    decode_magnitudes,
    dequantize,
    precincts,
    predict_gcli,
    read_header,
    truncation_levels,
)
from openraw_studio.raw.native.he_transform import (
    decode_component_planes,
    horizontal_rows,
    inverse_wavelet_53,
)


def pack_bits(bits):
    padding = (-len(bits)) % 8
    return int(bits + "0" * padding, 2).to_bytes((len(bits) + padding) // 8, "big") if bits else b""


def synthetic_header(width=64, height=72):
    return HeHeader(width, height, 0, tuple((0, priority) for priority in range(25)), b"", b"")


def encode_precinct(header, previous=None, seed=1, quantization=1, refinement=0, dc=None):
    """Small deterministic fixture encoder, no camera data or reference library."""
    rng = np.random.default_rng(seed)
    groups = band_groups(header.width)
    thresholds = truncation_levels(header.weights, quantization, refinement)
    if previous is None:
        previous = tuple(np.zeros(count, np.uint8) for packet in groups for count in packet)
    all_gcli, all_coefficients, packets = [], [], []
    for packet in groups:
        sig_bits = code_bits = data_bits = sign_bits = ""
        for count in packet:
            band = len(all_gcli)
            threshold = thresholds[band]
            prior = all_gcli[12] if band == 23 else previous[23] if band == 12 else previous[band]
            values = rng.integers(-127, 128, (count, 4), dtype=np.int32)
            if seed == 0:
                values[:] = 0
            if dc is not None:
                values[:] = {0: dc[0], 6: dc[1], 12: dc[2], 23: dc[2], 13: dc[3]}.get(band, 0)
            values = np.sign(values) * ((np.abs(values) >> threshold) << threshold)
            depths = np.array([max(threshold, int(np.max(np.abs(row))).bit_length()) for row in values], np.uint8)
            residuals = [next(r for r in range(32) if predict_gcli(threshold, int(p), r) == int(d)) for p, d in zip(prior, depths)]
            for start in range(0, count, 8):
                block = residuals[start:start + 8]
                all_zero = not any(block)
                sig_bits += "1" if all_zero else "0"
                if not all_zero:
                    code_bits += "".join("1" * r + "0" for r in block)
            for row, depth in zip(values, depths):
                for plane in range(int(depth) - 1, threshold - 1, -1):
                    data_bits += "".join(str((abs(int(value)) >> plane) & 1) for value in row)
                sign_bits += "".join("1" if value < 0 else "0" for value in row if value)
            all_gcli.append(depths)
            all_coefficients.append(values.ravel())
        sig, code, data, sign = map(pack_bits, (sig_bits, code_bits, data_bits, sign_bits))
        packet_header = (len(data) << 35) | (len(code) << 15) | len(sign)
        packets.append(packet_header.to_bytes(7, "big") + sig + code + data + sign)
    payload = b"".join(packets)
    prefix = len(payload).to_bytes(3, "big") + bytes((quantization, refinement)) + (((1 << 50) - 1) << 6).to_bytes(7, "big")
    return prefix + payload, tuple(all_gcli), tuple(all_coefficients)


def frame_stream(blocks, width=64):
    picture = bytearray(37)
    picture[8:12] = width.to_bytes(2, "big") + (len(blocks) * 4).to_bytes(2, "big")
    picture[12:20] = bytes((0, 0, 0, 16, 4, 4, 8, 18))
    components = bytes((14, 17, 81, 14, 17, 81, 14, 17, 0, 14, 17, 81))
    weights = bytes(value for pair in synthetic_header().weights for value in pair)
    body = bytearray()
    for index, block in enumerate(blocks):
        if index % 16 == 0:
            body.extend(b"\xff\x20\x00\x04" + (index // 16).to_bytes(2, "big"))
        body.extend(block)
    body.extend(b"\xff\x11")
    payloads = (b"OpenRAW synthetic", picture, components, weights)
    total = 2 + sum(4 + len(payload) for payload in payloads) + len(body)
    picture[:4] = total.to_bytes(4, "big")
    prefix = b"\xff\x10" + b"".join(marker.to_bytes(2, "big") + (len(payload) + 2).to_bytes(2, "big") + payload
        for marker, payload in zip((0xFF50, 0xFF12, 0xFF13, 0xFF14), payloads))
    return prefix + body


class HePrimitiveTests(unittest.TestCase):
    def test_bit_reader_cross_byte_zero_and_bounds(self):
        reader = BitReader(bytes((0xAC, 0xD0)))
        self.assertEqual(reader.read(3), 5)
        self.assertEqual(reader.read(0), 0)
        self.assertEqual(reader.read(6), 25)
        self.assertEqual(reader.read(3), 5)
        reader.finish()
        for count in (-1, 1):
            with self.assertRaises(HeFormatError):
                reader.read(count)

    def test_unary_and_strict_padding(self):
        reader = BitReader(pack_bits("01101110"))
        self.assertEqual([reader.unary() for _ in range(3)], [0, 2, 3])
        reader.finish()
        for data in (b"\xff", b"\xff" * 5):
            with self.assertRaises(HeFormatError):
                BitReader(data).unary()
        for data in (b"\x01", bytes(2)):
            reader = BitReader(data)
            reader.read(1)
            with self.assertRaises(HeFormatError):
                reader.finish()

    def test_bounded_zigzag_prediction(self):
        self.assertEqual([predict_gcli(2, 5, r) for r in range(10)], [5, 4, 6, 3, 7, 2, 8, 9, 10, 11])
        self.assertEqual([predict_gcli(4, 1, r) for r in range(4)], [4, 5, 6, 7])
        with self.assertRaises(HeFormatError):
            decode_gcli(BitReader(b"\x00"), BitReader(pack_bits("1" * 16 + "0")), 1, 0)

    def test_weight_expansion_and_refinement_boundary(self):
        weights = tuple((index % 4, index) for index in range(25))
        levels = truncation_levels(weights, 5, 13)
        self.assertEqual(levels[12], 4)
        self.assertEqual(levels[23], levels[12])
        self.assertEqual(levels[13], 4)
        self.assertEqual(levels[25], 5)
        for bp, br in ((-1, 0), (16, 0), (1, -1), (1, 26)):
            with self.assertRaises(HeFormatError):
                truncation_levels(weights, bp, br)

    def test_magnitude_bit_order_zero_has_no_sign_and_uniform_reconstruction(self):
        # Four coefficients: 6, -2, 0, -4; bitplanes are 1001 and 1100.
        result = decode_magnitudes(BitReader(b"\x9c"), BitReader(pack_bits("011")), np.array([3], np.uint8), 1)
        np.testing.assert_array_equal(result, [6, -2, 0, -4])
        np.testing.assert_array_equal(dequantize(result, np.array([3], np.uint8), 1), [96, -32, 0, -64])
        np.testing.assert_array_equal(dequantize(np.array([12, -8, 4, 0], np.int32), np.array([4], np.uint8), 2), [208, -144, 64, 0])


class HeEntropyTests(unittest.TestCase):
    def test_compiled_and_reference_match_synthetic_multislice(self):
        header = synthetic_header()
        slow, fast = HeEntropyDecoder(header, accelerated=False), HeEntropyDecoder(header)
        prior = None
        for index in range(34):
            if index % 16 == 0:
                prior = None
            block, expected_gcli, expected_coefficients = encode_precinct(header, prior, seed=index, quantization=index % 4)
            prior = expected_gcli
            for decoder in (slow, fast):
                result = decoder.decode(block)
                for actual, expected in zip(result.coefficients, expected_coefficients):
                    np.testing.assert_array_equal(actual, expected)
                for actual, expected in zip(result.gcli, expected_gcli):
                    np.testing.assert_array_equal(actual, expected)
        self.assertEqual((slow.index, fast.index), (34, 34))

    def test_fallback_when_compiler_unavailable(self):
        header = synthetic_header()
        block, _, coefficients = encode_precinct(header)
        with patch.object(he_cpu, "extension", None), patch.object(compiled_he, "decode", None):
            result = HeEntropyDecoder(header).decode(block)
        for actual, expected in zip(result.coefficients, coefficients):
            np.testing.assert_array_equal(actual, expected)

    def test_cache_failure_retries_uncached(self):
        if compiled_he.njit is None:
            self.skipTest("Numba unavailable")
        arrays = (b"\x80", b"", b"", b"")
        with patch.object(he_cpu, "extension", None), patch.object(compiled_he, "decode", side_effect=OSError("cache unavailable")), patch.object(
            compiled_he, "njit", return_value=lambda function: function
        ):
            lengths, values = compiled_he.decode_packet(arrays, (1,), (0,), (np.zeros(1, np.uint8),))
        np.testing.assert_array_equal(lengths, [0])
        np.testing.assert_array_equal(values, [0, 0, 0, 0])

    def test_truncation_invalid_profile_and_padding_do_not_mutate_state(self):
        header = synthetic_header()
        block, _, _ = encode_precinct(header)
        corruptions = [block[:length] for length in (0, 11, 67, len(block) - 1)]
        for position, value in ((3, 16), (4, 26), (5, 0), (12, 0x80), (13, 0xFF)):
            corrupt = bytearray(block)
            corrupt[position] = value
            corruptions.append(bytes(corrupt))
        corrupt = bytearray(block + b"\x01")
        corrupt[:3] = (len(corrupt) - 12).to_bytes(3, "big")
        corruptions.append(bytes(corrupt))
        for accelerated in (False, True):
            decoder = HeEntropyDecoder(header, accelerated=accelerated)
            for bad in corruptions:
                with self.subTest(accelerated=accelerated, length=len(bad)), self.assertRaises(HeFormatError):
                    decoder.decode(bad)
                self.assertEqual(decoder.index, 0)
                self.assertTrue(all(not np.any(previous) for previous in decoder.previous))
            decoder.decode(block)
            previous = tuple(band.copy() for band in decoder.previous)
            with self.assertRaises(HeFormatError):
                decoder.decode(corruptions[-1])
            self.assertEqual(decoder.index, 1)
            for before, after in zip(previous, decoder.previous):
                np.testing.assert_array_equal(before, after)

    def test_variable_width_and_zero_packets(self):
        for width in (64, 72, 128, 5600, 6064, 8280):
            header = synthetic_header(width)
            block, _, _ = encode_precinct(header, seed=0)
            result = HeEntropyDecoder(header).decode(block)
            self.assertTrue(all(not np.any(band) for band in result.coefficients))
            np.testing.assert_array_equal(horizontal_rows(result, width), np.zeros((8, width//2)))

    def test_component_planes_constant_signal_and_partial_slice(self):
        for count in (1, 16, 17, 18, 33):
            blocks, previous = [], None
            dc = (128, 256, 32, 512)
            for index in range(count):
                if index % 16 == 0:
                    previous = None
                block, previous, _ = encode_precinct(synthetic_header(), previous, quantization=0, dc=dc)
                blocks.append(block)
            stream = frame_stream(blocks)
            original = bytes(stream)
            planes = decode_component_planes(stream)
            for component, value in enumerate(dc):
                np.testing.assert_array_equal(planes[component], np.full((count * 2, 32), value * 16))
            self.assertEqual(stream, original)

    def test_compiled_and_reference_reject_same_damaged_packets(self):
        header = synthetic_header()
        block, _, _ = encode_precinct(header)
        rng = np.random.default_rng(103)
        for _ in range(60):
            damaged = bytearray(block)
            damaged[int(rng.integers(12, len(damaged)))] ^= 1 << int(rng.integers(8))
            outcomes = []
            for accelerated in (False, True):
                try:
                    result = HeEntropyDecoder(header, accelerated=accelerated).decode(damaged)
                    outcomes.append(np.concatenate(result.coefficients))
                except HeFormatError:
                    outcomes.append(None)
            self.assertEqual(outcomes[0] is None, outcomes[1] is None)
            if outcomes[0] is not None:
                np.testing.assert_array_equal(*outcomes)

    def test_stream_framing_and_corruption(self):
        block, _, _ = encode_precinct(synthetic_header(), seed=0)
        stream = frame_stream([block] * 18)
        header = read_header(stream)
        self.assertEqual((header.width, header.height), (64, 72))
        self.assertEqual(len(list(precincts(stream, header))), 18)
        for altered in (stream[:-1], stream + b"\x00", b"bad" + stream[3:]):
            with self.assertRaises(HeFormatError):
                read_header(altered)
        damaged = bytearray(stream)
        slice_offset = header.precinct_start + 16 * len(block)
        damaged[slice_offset + 5] = 2
        with self.assertRaises(HeFormatError):
            list(precincts(damaged, header))
        damaged = bytearray(stream)
        damaged[-1] = 0
        with self.assertRaises(HeFormatError):
            list(precincts(damaged, header))
        with self.assertRaises(HeFormatError):
            list(precincts(stream, replace(header, height=10**12)))

    def test_header_rejects_unknown_layout_and_excessive_allocation(self):
        block, _, _ = encode_precinct(synthetic_header(), seed=0)
        stream = frame_stream([block])
        picture = stream.index(b"\xff\x12") + 4
        cases = []
        for relative, value in ((13, 1), (15, 8), (16, 3), (18, 4)):
            data = bytearray(stream)
            data[picture + relative] = value
            cases.append(data)
        data = bytearray(stream)
        data[picture + 8:picture + 12] = (65528).to_bytes(2, "big") + (65532).to_bytes(2, "big")
        cases.append(data)
        for data in cases:
            with self.assertRaises(HeFormatError):
                decode_component_planes(data)


class HeWaveletTests(unittest.TestCase):
    def test_inverse_recovers_forward_lifting_even_odd_and_negative_values(self):
        rng = np.random.default_rng(742)
        for size in (1, 2, 3, 4, 17, 95, 189, 3032):
            original = rng.integers(-50000, 50000, (3, size), dtype=np.int64)
            even, odd = original[..., ::2], original[..., 1::2]
            if len(odd[0]):
                high = odd - ((even[..., :odd.shape[-1]] + even[..., np.minimum(np.arange(odd.shape[-1]) + 1, even.shape[-1]-1)]) >> 1)
                indices = np.arange(even.shape[-1])
                low = even + ((high[..., np.clip(indices-1, 0, high.shape[-1]-1)] + high[..., np.minimum(indices, high.shape[-1]-1)] + 2) >> 2)
            else:
                low, high = even, odd
            np.testing.assert_array_equal(inverse_wavelet_53(low, high), original)

    def test_rejects_bad_wavelet_shapes(self):
        for low, high in (([], []), ([1], [1, 2]), ([1, 2, 3], [1]), ([[1, 2]], [1])):
            with self.assertRaises(HeFormatError):
                inverse_wavelet_53(low, high)


if __name__ == "__main__":
    unittest.main()
