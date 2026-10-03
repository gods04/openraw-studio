import unittest
from unittest.mock import Mock, patch

import numpy as np
from test_nikon_he_entropy import encode_precinct, frame_stream, synthetic_header

from openraw_studio.raw.native import compiled_he_transform, he_transform
from openraw_studio.raw.native.he import HeEntropyDecoder, dequantize
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
        with patch.dict(compiled_he_transform.kernels, horizontal=None, color=None):
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
        with patch.dict(compiled_he_transform.kernels), patch.dict(
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
