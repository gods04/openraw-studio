import hashlib
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.raw.native import (
    chroma,
    compiled_chroma,
    compiled_luminance,
    luminance,
)


FILTERS = (
    (luminance, luminance.reduce_luminance_noise, compiled_luminance, "luminance"),
    (chroma, chroma.reduce_color_noise, compiled_chroma, "chroma"),
)
HAS_COMPILERS = all(getattr(compiled, name) is not None for _, _, compiled, name in FILTERS)


class Uint16CpuNoiseTests(unittest.TestCase):
    def test_uint8_outputs_match_pre_uint16_fingerprints(self):
        pixels = np.random.default_rng(84016).integers(70, 151, (9, 13, 3), dtype=np.uint8)
        pixels[0, :4] = ((0, 0, 0), (255, 255, 255), (0, 255, 19), (254, 1, 129))
        pixels[-1] = (29, 47, 61)
        # Captured from both original CPU implementations before adding RGB16.
        expected = {
            "luminance": "9e8dc1865be38492d433d431d048fb3496617d5ed4d8b832143facb3bdbcee62",
            "chroma": "787fbea4e2bda99cd03b25b8bd9c5a0387e4901e00e8a5b0f1e0f8c3d576e314",
        }
        for _, reduce, _, name in FILTERS:
            for use_compiled in (False, True):
                with self.subTest(filter=name, compiled=use_compiled):
                    digest = hashlib.sha256()
                    for strength in (0.01, 0.5, 1.0):
                        result = reduce(
                            pixels, strength, use_gpu=False,
                            use_compiled=use_compiled, chunk_rows=3,
                        )
                        self.assertEqual(result.dtype, np.uint8)
                        digest.update(result.tobytes())
                    self.assertEqual(digest.hexdigest(), expected[name])

    def test_zero_returns_same_readonly_strided_input_without_filtering(self):
        pixels = np.arange(8 * 12 * 3, dtype=np.uint16).reshape(8, 12, 3)[::-2, ::2]
        pixels.flags.writeable = False
        for module, reduce, compiled, name in FILTERS:
            with (
                self.subTest(filter=name),
                patch.object(module, "_reference_chunk", side_effect=AssertionError("zero")),
                patch.object(compiled, "render_chunk", side_effect=AssertionError("zero")),
            ):
                self.assertIs(reduce(pixels, 0), pixels)
                self.assertFalse(pixels.flags.writeable)

    def test_filtering_retains_more_than_256_levels_and_sub_257_precision(self):
        rng = np.random.default_rng(1602)
        gradient = np.linspace(2500, 61000, 17 * 23).reshape(17, 23, 1)
        pixels = np.rint(gradient + rng.integers(-500, 501, (17, 23, 3))).astype(np.uint16)
        original = pixels.copy()
        for _, reduce, _, name in FILTERS:
            for use_compiled in (False, True):
                with self.subTest(filter=name, compiled=use_compiled):
                    actual = reduce(
                        pixels, 1, use_gpu=False, use_compiled=use_compiled, chunk_rows=3,
                    )
                    self.assertEqual(actual.dtype, pixels.dtype)
                    self.assertGreater(np.unique(actual[..., 0]).size, 256)
                    self.assertTrue(np.any(actual % 257))
                    self.assertFalse(np.array_equal(actual, pixels))
                    np.testing.assert_array_equal(pixels, original)

    @unittest.skipUnless(HAS_COMPILERS, "Numba unavailable")
    def test_compiled_and_reference_agree_at_edges_strengths_and_chunks(self):
        rng = np.random.default_rng(1613)
        for height, width in ((1, 1), (1, 11), (13, 1), (9, 13)):
            pixels = rng.integers(0, 65536, (2 * height, 2 * width, 3), dtype=np.uint16)
            pixels = pixels[::-2, ::2]
            pixels[0, 0] = (65535, 0, 65535)
            pixels[-1, -1] = (0, 65535, 0)
            original = pixels.copy()
            pixels.flags.writeable = False
            for module, reduce, compiled, name in FILTERS:
                for strength in (0.01, 0.5, 1):
                    expected = reduce(pixels, strength, use_gpu=False, use_compiled=False)
                    for rows in (1, 3, 128):
                        with self.subTest(filter=name, shape=pixels.shape, strength=strength, rows=rows):
                            reference = reduce(
                                pixels, strength, use_gpu=False,
                                use_compiled=False, chunk_rows=rows,
                            )
                            np.testing.assert_array_equal(reference, expected)
                            with patch.object(module, "_reference_chunk", side_effect=AssertionError("fallback")):
                                actual = reduce(pixels, strength, use_gpu=False, chunk_rows=rows)
                            self.assertIsNone(compiled.last_error)
                            self.assertEqual(actual.dtype, np.uint16)
                            difference = np.abs(actual.astype(np.int32) - expected.astype(np.int32))
                            self.assertLessEqual(difference.max(), 0 if name == "luminance" else 1)
            np.testing.assert_array_equal(pixels, original)
            self.assertFalse(pixels.flags.writeable)

    def test_constant_colors_and_black_white_are_exact(self):
        for color in ((0, 0, 0), (65535, 65535, 65535), (0, 65535, 19), (32769, 45678, 54321)):
            pixels = np.full((5, 7, 3), color, np.uint16)
            for _, reduce, _, name in FILTERS:
                for use_compiled in (False, True):
                    with self.subTest(filter=name, color=color, compiled=use_compiled):
                        np.testing.assert_array_equal(
                            reduce(pixels, 1, use_gpu=False, use_compiled=use_compiled, chunk_rows=2),
                            pixels,
                        )

    def test_strong_edges_and_gray_texture_are_preserved(self):
        light_edge = np.empty((7, 11, 3), np.uint16)
        light_edge[:, :5] = (401, 1001, 2039)
        light_edge[:, 5:] = (53011, 60013, 64123)
        color_edge = np.empty_like(light_edge)
        color_edge[:, :5] = (61001, 12013, 4001)
        color_edge[:, 5:] = (3001, 17011, 59001)
        gray = np.random.default_rng(1617).integers(0, 65536, (7, 11, 1), dtype=np.uint16).repeat(3, axis=2)
        for use_compiled in (False, True):
            options = dict(use_gpu=False, use_compiled=use_compiled, chunk_rows=2)
            np.testing.assert_array_equal(luminance.reduce_luminance_noise(light_edge, 1, **options), light_edge)
            np.testing.assert_array_equal(chroma.reduce_color_noise(gray, 1, **options), gray)
            after = chroma.reduce_color_noise(color_edge, 1, **options)
            self.assertLessEqual(np.abs(after.astype(np.int32) - color_edge.astype(np.int32)).max(), 1)

    def test_luminance_reduces_grain_with_equal_bounded_channel_shifts(self):
        rng = np.random.default_rng(1619)
        grain = rng.integers(-2200, 2201, (15, 17, 1))
        pixels = (np.array([31001, 35013, 29111]) + grain).astype(np.uint16)
        boundary = rng.integers(0, 65536, pixels.shape, dtype=np.uint16)
        boundary[::2, :, 0] = 0
        boundary[1::2, :, 2] = 65535
        for use_compiled in (False, True):
            for source in (pixels, boundary):
                after = luminance.reduce_luminance_noise(source, 1, use_gpu=False, use_compiled=use_compiled)
                delta = after.astype(np.int32) - source.astype(np.int32)
                np.testing.assert_array_equal(delta[..., 0], delta[..., 1])
                np.testing.assert_array_equal(delta[..., 0], delta[..., 2])
            after = luminance.reduce_luminance_noise(pixels, 1, use_gpu=False, use_compiled=use_compiled)
            self.assertLess(after[..., 0].std(), pixels[..., 0].std() * 0.7)

    def test_chroma_reduces_color_noise_without_changing_full_precision_luma(self):
        rng = np.random.default_rng(1621)
        pixels = (33001 + rng.integers(-1800, 1801, (15, 17, 3))).astype(np.uint16)
        weights = np.array([54, 183, 19]) / 256.0
        before_luma = pixels @ weights
        before_chroma = pixels.astype(float) - before_luma[..., None]
        for use_compiled in (False, True):
            after = chroma.reduce_color_noise(pixels, 1, use_gpu=False, use_compiled=use_compiled)
            after_luma = after @ weights
            self.assertLessEqual(np.abs(after_luma - before_luma).max(), 0.5)
            self.assertLess((after - after_luma[..., None]).std(), before_chroma.std() * 0.65)

    def test_luminance_delta_rounds_positive_and_negative_ties_to_even(self):
        # Replicated 1x2 edges give a neighbor fraction of 2/5; strength 5/8
        # makes channel differences 2, 6, 10, 14 produce shifts 0.5, 1.5, 2.5, 3.5.
        weights = np.ones_like(luminance.WEIGHTS)
        for dtype, base in ((np.uint8, 100), (np.uint16, 40001)):
            for difference, shift in ((2, 0), (6, 2), (10, 2), (14, 4)):
                pixels = np.array([[[base] * 3, [base + difference] * 3]], dtype=dtype)
                expected = np.array([[[base + shift] * 3, [base + difference - shift] * 3]], dtype=dtype)
                for use_compiled in (False, True):
                    with patch.object(luminance, "WEIGHTS", weights):
                        actual = luminance.reduce_luminance_noise(
                            pixels, 5 / 8, use_gpu=False, use_compiled=use_compiled,
                        )
                    np.testing.assert_array_equal(actual, expected)

    def test_direct_filter_calls_keep_optional_unit_compatible_and_scratch_bounded(self):
        for dtype, maximum in ((np.uint8, 255), (np.uint16, 65535)):
            pixels = np.full((19, 7, 3), (maximum, 0, maximum), dtype=dtype)
            for module, _, compiled, name in FILTERS:
                tables = (luminance.WEIGHTS,) if name == "luminance" else (chroma.SPATIAL, chroma.LIGHT, chroma.COLOR)
                amount = round(0.75 * 65536) if name == "luminance" else 0.75
                with patch.object(np, "empty", wraps=np.empty) as allocate:
                    actual = compiled._filter(pixels, 5, 8, amount, *tables)
                np.testing.assert_array_equal(actual, module._reference_chunk(pixels, 5, 8, 0.75))
                scratch = (7, 11) if name == "luminance" else (7, 11, 3)
                self.assertEqual([call.args[0] for call in allocate.call_args_list], [(3, 7, 3), scratch])
                self.assertEqual(allocate.call_args_list[1].args[1], np.int32)

    def test_compiler_failure_and_unavailable_kernel_keep_uint16_reference(self):
        pixels = np.random.default_rng(1623).integers(0, 65536, (7, 9, 3), dtype=np.uint16)
        for _, reduce, compiled, name in FILTERS:
            expected = reduce(pixels, 0.7, use_gpu=False, use_compiled=False)
            for kernel in (None, Mock(side_effect=RuntimeError("Compiler unavailable"))):
                with patch.object(compiled, name, kernel), patch.object(compiled, "last_error", None):
                    actual = reduce(pixels, 0.7, use_gpu=False, chunk_rows=2)
                    if kernel is not None:
                        kernel.assert_called_once()
                        self.assertIn("unavailable", compiled.last_error)
                np.testing.assert_array_equal(actual, expected)

    @unittest.skipUnless(HAS_COMPILERS, "Numba unavailable")
    def test_explicit_unit_compiled_calls_match_dtype_specialization(self):
        rng = np.random.default_rng(1624)
        for dtype, unit in ((np.uint8, 1), (np.uint16, 257)):
            pixels = rng.integers(0, 255 * unit + 1, (5, 7, 3), dtype=dtype)
            pixels.flags.writeable = False
            for _, _, compiled, name in FILTERS:
                with self.subTest(dtype=dtype, filter=name):
                    tables = (luminance.WEIGHTS,) if name == "luminance" else (chroma.SPATIAL, chroma.LIGHT, chroma.COLOR)
                    amount = round(0.73 * 65536) if name == "luminance" else 0.73
                    kernel = getattr(compiled, name)
                    expected = compiled.render_chunk(pixels, 1, 4, 0.73, *tables)
                    self.assertIsNotNone(expected, compiled.last_error)
                    np.testing.assert_array_equal(kernel(pixels, 1, 4, amount, *tables, unit), expected)

    @unittest.skipUnless(HAS_COMPILERS, "Numba unavailable")
    def test_cache_retry_preserves_uint16_scale_and_dtype(self):
        pixels = np.random.default_rng(1625).integers(0, 65536, (3, 5, 3), dtype=np.uint16)
        for _, reduce, compiled, name in FILTERS:
            expected = reduce(pixels, 0.7, use_gpu=False, use_compiled=False)
            retry = Mock(wraps=compiled._filter)
            with (
                patch.object(compiled, name, side_effect=OSError("cache denied")),
                patch.object(compiled, "njit", return_value=lambda _: retry),
                patch.object(compiled, "cache_disabled_reason", None),
                patch.object(compiled, "last_error", None),
            ):
                actual = reduce(pixels, 0.7, use_gpu=False)
                retry.assert_called_once()
                self.assertEqual(retry.call_args.args[0].dtype, np.uint16)
                self.assertIn("cache denied", compiled.cache_disabled_reason)
                self.assertIsNone(compiled.last_error)
            self.assertEqual(actual.dtype, np.uint16)
            self.assertLessEqual(np.abs(actual.astype(np.int32) - expected.astype(np.int32)).max(), 0 if name == "luminance" else 1)
