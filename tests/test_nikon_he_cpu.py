import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
import test_nikon_he_acceleration
from test_nikon_he_entropy import encode_precinct, frame_stream, synthetic_header

from openraw_studio.raw.native import compiled_he, compiled_he_transform, he_cpu, he_transform
from openraw_studio.raw.native.he import HeEntropyDecoder, HeFormatError


@unittest.skipIf(he_cpu.extension is None, "Optional HE CPU extension unavailable")
class HeCpuTests(unittest.TestCase):
    def test_entire_frame_never_enters_jit_and_matches_reference(self):
        blocks, prior = [], None
        for index in range(18):
            if index % 16 == 0:
                prior = None
            block, prior, _ = encode_precinct(synthetic_header(72), prior, seed=index, quantization=index % 5)
            blocks.append(block)
        stream = frame_stream(blocks, width=72)
        expected = he_transform.decode_component_planes(stream, accelerated=False)
        with patch.object(compiled_he, "decode", side_effect=AssertionError("JIT called")), patch.object(
            compiled_he_transform, "_run", side_effect=AssertionError("JIT called")
        ):
            actual = he_transform.decode_component_planes(stream)
            np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(
                he_transform.reconstruct_zf_he_bayer(actual),
                he_transform.reconstruct_zf_he_bayer(expected, accelerated=False),
            )

    def test_horizontal_matches_reference_readonly_inputs_and_padded_samples(self):
        for width in (*range(64, 145, 8), 248, 264, 5600, 6064, 65528):
            block = test_nikon_he_acceleration.HeAccelerationTests.random_precinct(width, width)
            originals = [v.copy() for v in (*block.coefficients, *block.gcli)]
            for values in (*block.coefficients, *block.gcli):
                values.flags.writeable = False
            with self.subTest(width=width):
                np.testing.assert_array_equal(he_cpu.horizontal_rows(block, width), he_transform.horizontal_rows(block, width))
                for before, after in zip(originals, (*block.coefficients, *block.gcli)):
                    np.testing.assert_array_equal(before, after)

    def test_color_handles_strided_tiles_extremes_and_boundaries(self):
        rng = np.random.default_rng(34)
        curve = he_transform._zf_linearization_curve()
        for height, width in ((1, 1), (2, 2), (3, 4), (63, 7), (64, 7), (65, 7), (129, 9), (4, 3032)):
            planes = rng.integers(-2**31, 2**31, (4, height + 2, width), dtype=np.int32)[:, 1:-1]
            planes.flags.writeable = False
            with self.subTest(shape=planes.shape):
                expected = he_transform.linearize_zf_he_bayer(he_transform._color_lift_chunk(planes))
                np.testing.assert_array_equal(he_cpu.linear_color(planes, curve), expected)

    def test_concurrent_calls_are_independent(self):
        rng = np.random.default_rng(52)
        planes = [rng.integers(-60000, 60000, (4, 68, 32), dtype=np.int32) for _ in range(8)]
        curve = he_transform._zf_linearization_curve()
        expected = [he_transform.linearize_zf_he_bayer(he_transform._color_lift_chunk(p)) for p in planes]
        with ThreadPoolExecutor(max_workers=4) as pool:
            actual = list(pool.map(lambda p: he_cpu.linear_color(p, curve), planes))
        for before, after in zip(expected, actual):
            np.testing.assert_array_equal(before, after)
        self.assertFalse(np.shares_memory(actual[0], actual[1]))

    @staticmethod
    def packet_arguments():
        return [b"\x80", b"", b"", b"", np.array([1], np.int64), np.array([0], np.int64),
                np.zeros(1, np.uint8), np.empty(1, np.uint8), np.empty(4, np.int32)]

    def test_packet_rejects_truncation_padding_unary_and_invalid_metadata(self):
        cases = [(0, b""), (0, b"\x81"), (1, b"\x00"), (2, b"\x00"), (3, b"\x00"),
                 (4, np.array([-1], np.int64)), (4, np.array([2**62], np.int64)),
                 (5, np.array([16], np.int64)), (6, np.array([16], np.uint8)),
                 (7, np.empty(0, np.uint8)), (8, np.empty(3, np.int32))]
        for index, replacement in cases:
            args = self.packet_arguments()
            args[index] = replacement
            with self.subTest(index=index), self.assertRaises(ValueError):
                he_cpu.extension.decode_packet(*args)
        for code in (b"\xff" * 5, b"\xff\xff\x00", b"\xff"):
            args = self.packet_arguments()
            args[:2] = [b"\x00", code]
            with self.assertRaises(ValueError):
                he_cpu.extension.decode_packet(*args)
        args = self.packet_arguments()
        args[7] = args[6]
        with self.assertRaisesRegex(ValueError, "aliases"):
            he_cpu.extension.decode_packet(*args)

    def test_color_rejects_unsafe_buffers_and_releases_exports_on_error(self):
        planes = np.zeros((4, 2, 3), np.int32)
        curve = he_transform._zf_linearization_curve()
        output = np.empty((4, 6), np.uint16)
        unaligned = np.ndarray(planes.shape, np.int32, buffer=bytearray(planes.nbytes + 1), offset=1)
        readonly = output.copy()
        readonly.flags.writeable = False
        cases = [(planes.astype(np.float32), curve, output), (planes.astype(">i4"), curve, output),
                 (planes[:, :, ::-1], curve, output), (unaligned, curve, output),
                 (planes, curve[:-1], output), (planes, curve, output[:-1]),
                 (planes, curve, readonly), (planes[:0], curve, output),
                 (planes, curve, planes.view(np.uint16).reshape(8, 6)[:4]),
                 (planes, curve, curve[:24].reshape(4, 6))]
        # The last case rejects readonly output before checking overlap.
        for inputs in cases:
            with self.subTest(shape=inputs[0].shape), self.assertRaises((ValueError, BufferError)):
                he_cpu.extension.linear_color(*inputs)
        backing = bytearray(4 * 2 * 3 * 4)
        bad = np.ndarray((4, 2, 3), np.float32, buffer=backing)
        with self.assertRaises(ValueError):
            he_cpu.extension.linear_color(bad, curve, output)
        del bad
        backing.extend(b"x")

    def test_horizontal_rejects_mismatched_lengths_groups_and_real_coefficients(self):
        block = test_nikon_he_acceleration.HeAccelerationTests.random_precinct(72, 5)
        base = [np.concatenate(block.coefficients), np.concatenate(block.gcli), np.array(block.thresholds, np.int64),
                np.array([len(v) for v in block.gcli], np.int64), np.empty((8, 36), np.int32)]
        for index, value in ((0, base[0][:-1]), (1, base[1][:-1]), (2, base[2][:-1]),
                             (3, np.full(26, 2**62, np.int64)), (4, np.empty((8, 31), np.int32))):
            args = base.copy()
            args[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                he_cpu.extension.horizontal(*args)
        values = base[0].copy()
        values[0] = 2**31 - 1
        with self.assertRaisesRegex(ValueError, "coefficient"):
            he_cpu.extension.horizontal(values, *base[1:])
        groups = base[3].copy()
        groups[0] += 1
        groups[-1] -= 1
        with self.assertRaises(ValueError):
            he_cpu.extension.horizontal(*base[:3], groups, base[4])

    def test_native_corrupt_data_does_not_fall_back_to_jit(self):
        with patch.object(compiled_he, "decode", side_effect=AssertionError("JIT called")):
            with self.assertRaises(ValueError):
                compiled_he.decode_packet((b"", b"", b"", b""), (1,), (0,), (np.zeros(1, np.uint8),))
        block, _, _ = encode_precinct(synthetic_header())
        decoder = HeEntropyDecoder(synthetic_header())
        with self.assertRaises(HeFormatError):
            decoder.decode(block[:-1])
        self.assertEqual(decoder.index, 0)


class HeCpuFallbackTests(unittest.TestCase):
    def test_environment_switch_disables_extension_in_fresh_process(self):
        process = subprocess.run(
            [sys.executable, "-c", "from openraw_studio.raw.native import he_cpu; assert he_cpu.extension is None; print(he_cpu.import_error)"],
            env={**os.environ, "OPENRAW_HE_AOT": "off"}, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("OPENRAW_HE_AOT=off", process.stdout)


if __name__ == "__main__":
    unittest.main()
