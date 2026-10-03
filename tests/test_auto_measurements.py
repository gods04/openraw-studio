import itertools
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from openraw_studio.decision.auto_adjust import _any_rgb, _clipping, _pixels, _RenderGuard
from openraw_studio.raw.native.tone import PreviewRgbImage


def reference_pixels(preview):
    source = preview.pixels if isinstance(preview, PreviewRgbImage) else preview
    return np.clip(np.asarray(source, np.float32).reshape(-1, 3) / 255, 0, 1)


class AutoMeasurementTests(unittest.TestCase):
    def test_every_rgb8_value_normalizes_exactly(self):
        values = np.arange(256, dtype=np.uint8)
        pixels = np.stack((values, values[::-1], np.roll(values, 73)), axis=1)
        actual = _pixels(pixels)
        np.testing.assert_array_equal(actual, reference_pixels(pixels))
        self.assertEqual(actual.dtype, np.float32)
        self.assertFalse(np.shares_memory(pixels, actual))

    def test_rgb8_strided_readonly_and_image_inputs(self):
        pixels = np.random.default_rng(12).integers(0, 256, (30, 40, 3), dtype=np.uint8)
        saved = pixels.copy()
        pixels.flags.writeable = False
        for image in (pixels, pixels[::-2, ::3], pixels.transpose(1, 0, 2), Image.fromarray(pixels)):
            with self.subTest(kind=type(image)):
                actual = _pixels(image)
                np.testing.assert_array_equal(actual, reference_pixels(image))
                actual[0] = 0
        np.testing.assert_array_equal(pixels, saved)

    def test_legacy_preview_shape_and_pixel_contract(self):
        pixels = ((0, 250, 255), (254, 128, 4))
        preview = PreviewRgbImage(2, 1, pixels, 'srgb')
        np.testing.assert_array_equal(_pixels(preview), reference_pixels(preview))

    def test_other_numeric_types_retain_clamped_float32_semantics(self):
        for dtype in (np.float16, np.float32, np.float64, np.int16, np.uint16, np.bool_):
            with self.subTest(dtype=dtype):
                source = np.array([[0, 1, 256], [500, 128, 250]], dtype=dtype)
                if np.issubdtype(dtype, np.signedinteger) or np.issubdtype(dtype, np.floating):
                    source[0, 0] = -10
                saved = source.copy()
                actual = _pixels(source)
                np.testing.assert_array_equal(actual, reference_pixels(source))
                np.testing.assert_array_equal(source, saved)

    def test_invalid_shapes_and_nonfinite_values_are_rejected(self):
        for source in (np.zeros((0, 3), np.uint8), np.zeros((4, 4), np.uint8),
                       np.zeros(3, np.uint8), np.array(1, np.uint8),
                       [[np.nan, 0, 0]], [[0, np.inf, 0]], [[0, 0, -np.inf]]):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, 'finite RGB'):
                    _pixels(source)

    def test_three_channel_or_matches_reduction(self):
        channels = np.array(list(itertools.product((False, True), repeat=3)))
        for values in (channels, channels[::-1], channels[:, ::-1]):
            np.testing.assert_array_equal(_any_rgb(values), np.any(values, axis=1))

    def test_clipping_preserves_boundary_and_channel_semantics(self):
        threshold = np.float32(254 / 255)
        levels = (0, np.nextafter(threshold, np.float32(0)), threshold,
                  np.nextafter(threshold, np.float32(1)), 1)
        pixels = np.array(list(itertools.product(levels, repeat=3)), dtype=np.float32)
        for source in (pixels, pixels[::-2], pixels[:, ::-1]):
            self.assertEqual(_clipping(source), float(np.mean(source.max(axis=1) >= 254 / 255)))

    def test_guard_metrics_match_reference_for_random_and_extreme_pixels(self):
        rng = np.random.default_rng(19)
        original = rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)
        original[:10] = 180
        for dtype in (np.uint8, np.float32):
            for candidate in (original, np.zeros_like(original), np.full_like(original, 255),
                              rng.integers(0, 256, original.shape, dtype=np.uint8)):
                candidate = candidate.astype(dtype)
                if dtype == np.float32:
                    candidate[0, :2] = (-10, 253.9, 300)
                callback = Mock(return_value=candidate)
                guard = _RenderGuard(original, callback, preserve_midtones=True)
                values = {'exposure': .3, 'highlights': -.5}
                actual = guard.measure(values)
                pixels = reference_pixels(candidate)
                luma = pixels @ guard.weights
                clipped = pixels >= 254 / 255
                self.assertEqual(actual.clipping, float(np.mean(pixels.max(axis=1) >= 254 / 255)))
                self.assertEqual(actual.median, float(np.median(luma)))
                self.assertEqual(actual.crushed_shadows, float(np.mean(guard.usable_shadows & (luma <= 2 / 255))))
                self.assertEqual(actual.new_clipping, float(np.mean(np.any(guard.headroom & clipped, axis=1))))
                self.assertEqual(actual.lost_highlight_channels, np.count_nonzero(guard.highlight_detail & clipped))
                self.assertEqual(actual.shadow_midtone_mean, float(np.mean(luma[guard.shadow_midtones])))
                self.assertIsNone(actual.neutral_bias)
                self.assertIs(actual, guard.measure(dict(reversed(list(values.items())))))
                callback.assert_called_once_with(values)

    def test_neutral_bias_matches_reference_and_input_is_immutable(self):
        original = np.full((40, 40, 3), (130, 120, 115), dtype=np.uint8)
        candidate = np.full_like(original, (140, 128, 125))
        saved = candidate.copy()
        guard = _RenderGuard(original, lambda _: candidate, preserve_midtones=True, balance=True)
        self.assertIsNotNone(guard.neutral.mask)
        actual = guard.measure({'warmth': -.2})
        np.testing.assert_array_equal(actual.neutral_bias, guard.neutral.measure(reference_pixels(candidate)))
        np.testing.assert_array_equal(candidate, saved)


if __name__ == '__main__':
    unittest.main()
