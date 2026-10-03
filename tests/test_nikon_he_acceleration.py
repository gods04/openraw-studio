import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import numpy as np
from test_nikon_he_entropy import encode_precinct, frame_stream, synthetic_header

from openraw_studio.raw.native import compiled_he_transform, he_cpu, he_transform
from openraw_studio.raw.native.he import (
    EntropyPrecinct,
    HeEntropyDecoder,
    band_groups,
    dequantize,
)
from openraw_studio.raw.native.he_transform import (
    _color_lift_chunk,
    _horizontal_batch,
    decode_component_planes,
    horizontal_rows,
    linearize_zf_he_bayer,
    reconstruct_nonlinear_bayer,
    reconstruct_zf_he_bayer,
)


class HeAccelerationTests(unittest.TestCase):
    @staticmethod
    def random_precinct(width, seed):
        rng = np.random.default_rng(seed)
        groups = [count for packet in band_groups(width) for count in packet]
        thresholds = tuple(map(int, rng.integers(0, 16, len(groups))))
        band_sizes = []
        for levels in (5, 5, 0, 5, 1, 1, 0, 1):
            low, highs = width // 2, []
            for _ in range(levels):
                highs.append(low // 2)
                low -= low // 2
            band_sizes.extend((low, *reversed(highs)))
        coefficients, depths = [], []
        for count, size, threshold in zip(groups, band_sizes, thresholds):
            depth = rng.integers(threshold, 16, count, dtype=np.uint8)
            limits = (1 << depth.astype(np.int32)) - 1
            values = rng.integers(0, 32768, (count, 4), dtype=np.int32) & limits[:, None]
            values = ((values >> threshold) << threshold) * rng.choice(np.array([-1, 1], np.int32), (count, 4))
            values = values.ravel()
            # Group padding must never become a synthesized wavelet sample.
            values[size:] = 0x3FFFFFFF
            coefficients.append(values)
            depths.append(depth)
        return EntropyPrecinct(tuple(depths), tuple(coefficients), thresholds, ())

    def test_scratch_rows_match_numpy_with_padding_extremes_and_odd_band_sizes(self):
        if compiled_he_transform.kernels["horizontal"] is None:
            self.skipTest("Numba unavailable")
        for width in (*range(64, 145, 8), 248, 256, 264, 5600, 6064):
            block = self.random_precinct(width, seed=width)
            expected = horizontal_rows(block, width)
            originals = [v.copy() for v in (*block.coefficients, *block.gcli)]
            for values in (*block.coefficients, *block.gcli):
                values.flags.writeable = False
            actual = compiled_he_transform.horizontal_rows(block, width)
            with self.subTest(width=width):
                self.assertIsNotNone(actual)
                self.assertEqual(actual.dtype, np.int32)
                np.testing.assert_array_equal(actual, expected)
                for values, original in zip((*block.coefficients, *block.gcli), originals):
                    np.testing.assert_array_equal(values, original)
                    self.assertFalse(values.flags.writeable)

    def test_python_kernel_allocates_only_bounded_scratch_and_ignores_old_contents(self):
        empty = np.empty
        for width in (72, 6064):
            block = self.random_precinct(width, seed=1)
            expected = horizontal_rows(block, width)
            allocations = []

            def poisoned(shape, dtype, allocations=allocations):
                result = empty(shape, dtype)
                result.fill(-999999)
                allocations.append(result)
                return result

            with patch.object(compiled_he_transform.np, "empty", side_effect=poisoned):
                actual = compiled_he_transform._horizontal(
                    np.concatenate(block.coefficients), np.concatenate(block.gcli),
                    np.array(block.thresholds, np.int64),
                    np.array([len(v) for v in block.gcli], np.int64), width,
                )
            np.testing.assert_array_equal(actual, expected)
            count = width // 2
            self.assertEqual([v.shape for v in allocations], [(8, count), (count,), (count,), (count // 2,), (5,)])
            self.assertEqual(sum(v.nbytes for v in allocations), 52 * count + 40)

    def test_concurrent_horizontal_calls_have_independent_work_buffers(self):
        if compiled_he_transform.kernels["horizontal"] is None:
            self.skipTest("Numba unavailable")
        blocks = [self.random_precinct(128, seed) for seed in range(8)]
        expected = [horizontal_rows(block, 128) for block in blocks]
        compiled_he_transform.horizontal_rows(blocks[0], 128)
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual = list(pool.map(lambda block: compiled_he_transform.horizontal_rows(block, 128), blocks))
        for image, reference in zip(actual, expected):
            np.testing.assert_array_equal(image, reference)
        self.assertFalse(np.shares_memory(actual[0], actual[1]))

    def test_horizontal_cache_failure_and_compiler_failure_keep_reference_output(self):
        if compiled_he_transform.njit is None:
            self.skipTest("Numba unavailable")
        block = self.random_precinct(72, 5)
        expected = horizontal_rows(block, 72)
        with patch.object(he_cpu, "extension", None), patch.dict(compiled_he_transform.kernels), patch.dict(
            compiled_he_transform.last_errors
        ), patch.dict(compiled_he_transform.cache_disabled_reasons):
            compiled_he_transform.kernels["horizontal"] = Mock(side_effect=OSError("cache unavailable"))
            with patch.object(compiled_he_transform, "njit", return_value=lambda function: function):
                np.testing.assert_array_equal(compiled_he_transform.horizontal_rows(block, 72), expected)
            self.assertIn("cache unavailable", compiled_he_transform.cache_disabled_reasons["horizontal"])
            compiled_he_transform.kernels["horizontal"] = Mock(side_effect=RuntimeError("compiler unavailable"))
            np.testing.assert_array_equal(_horizontal_batch([block], 72)[:, 0], expected)
            self.assertIn("compiler unavailable", compiled_he_transform.last_errors["horizontal"])

    def test_batch_dequantization_preserves_per_row_thresholds(self):
        rng = np.random.default_rng(186)
        values = rng.integers(-32767, 32768, (7, 40), dtype=np.int32)
        depths = rng.integers(1, 16, (7, 10), dtype=np.uint8)
        thresholds = np.arange(7)
        expected = np.stack([dequantize(v, g, t) for v, g, t in zip(values, depths, thresholds)])
        np.testing.assert_array_equal(dequantize(values, depths, thresholds[:, None]), expected)

    def test_horizontal_compiled_and_batched_match_individual_rows(self):
        for width in (64, 72, 128, 5600, 6064):
            header = synthetic_header(width=width)
            decoder = HeEntropyDecoder(header)
            batch, previous = [], None
            for index in range(3):
                block, previous, _ = encode_precinct(header, previous, seed=index + 1, quantization=index)
                batch.append(decoder.decode(block))
            expected = np.stack([horizontal_rows(block, width) for block in batch], axis=1)
            for accelerated in (False, True):
                with self.subTest(width=width, accelerated=accelerated):
                    np.testing.assert_array_equal(_horizontal_batch(batch, width, accelerated=accelerated), expected)

    def test_batch_boundaries_do_not_reset_entropy_predictors(self):
        header = synthetic_header(width=72)
        blocks, previous = [], None
        for index in range(35):
            if index % 16 == 0:
                previous = None
            block, previous, _ = encode_precinct(header, previous, seed=index, quantization=index % 4)
            blocks.append(block)
        stream = frame_stream(blocks, width=72)
        with patch.object(he_transform, "_HORIZONTAL_BATCH", 1):
            expected = decode_component_planes(stream, accelerated=False)
        for batch_size in (7, 16, 32, 64):
            with patch.object(he_transform, "_HORIZONTAL_BATCH", batch_size):
                np.testing.assert_array_equal(decode_component_planes(stream), expected)

    def test_color_tiles_match_whole_frame_at_every_boundary(self):
        rng = np.random.default_rng(956)
        for height, width in ((1, 1), (2, 2), (3, 4), (63, 7), (64, 7), (65, 7), (129, 9), (4, 3032)):
            components = rng.integers(-1_000_000, 1_000_000, (4, height, width), dtype=np.int32)
            original = components.copy()
            nonlinear = _color_lift_chunk(components)
            linear = linearize_zf_he_bayer(nonlinear)
            for batch_size in (1, 7, 64):
                with self.subTest(shape=(height, width), batch=batch_size), patch.object(
                    he_transform, "_COLOR_BATCH", batch_size
                ):
                    np.testing.assert_array_equal(reconstruct_nonlinear_bayer(components), nonlinear)
                    for accelerated in (False, True):
                        np.testing.assert_array_equal(
                            reconstruct_zf_he_bayer(components, accelerated=accelerated), linear
                        )
            np.testing.assert_array_equal(components, original)

    def test_unavailable_compiler_uses_batched_numpy_fallback(self):
        block, _, _ = encode_precinct(synthetic_header())
        batch = [HeEntropyDecoder(synthetic_header()).decode(block)]
        components = np.arange(4 * 130 * 8, dtype=np.int32).reshape(4, 130, 8) - 2000
        with patch.object(he_cpu, "extension", None), patch.dict(compiled_he_transform.kernels, horizontal=None, color=None):
            np.testing.assert_array_equal(_horizontal_batch(batch, 64)[:, 0], horizontal_rows(batch[0], 64))
            np.testing.assert_array_equal(
                reconstruct_zf_he_bayer(components), linearize_zf_he_bayer(_color_lift_chunk(components))
            )

    def test_cache_error_retries_in_memory_and_compiler_failure_falls_back(self):
        if compiled_he_transform.njit is None:
            self.skipTest("Numba unavailable")
        values = np.zeros((4, 2, 2), np.int32)
        curve = he_transform._zf_linearization_curve()
        expected = linearize_zf_he_bayer(_color_lift_chunk(values))
        with patch.object(he_cpu, "extension", None), patch.dict(compiled_he_transform.kernels), patch.dict(
            compiled_he_transform.last_errors
        ), patch.dict(compiled_he_transform.cache_disabled_reasons):
            compiled_he_transform.kernels["color"] = Mock(side_effect=OSError("cache unavailable"))
            with patch.object(compiled_he_transform, "njit", return_value=lambda function: function):
                np.testing.assert_array_equal(compiled_he_transform.linear_color(values, curve), expected)
            self.assertIn("cache unavailable", compiled_he_transform.cache_disabled_reasons["color"])
            compiled_he_transform.kernels["color"] = Mock(side_effect=RuntimeError("compiler unavailable"))
            self.assertIsNone(compiled_he_transform.linear_color(values, curve))
            self.assertIn("compiler unavailable", compiled_he_transform.last_errors["color"])
            compiled_he_transform.kernels["color"] = Mock(side_effect=ValueError("bad math input"))
            with self.assertRaisesRegex(ValueError, "bad math"):
                compiled_he_transform.linear_color(values, curve)


if __name__ == "__main__":
    unittest.main()
