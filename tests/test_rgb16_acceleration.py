import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.raw.native import compiled_tone
from openraw_studio.raw.native.acceleration import color_parameters, get_gpu, tone_cpu
from openraw_studio.raw.native.fullres import render_bayer_full_resolution


class Rgb16AccelerationTests(unittest.TestCase):
    def test_failed_sixteen_bit_compilation_does_not_disable_eight_bit(self):
        from unittest.mock import Mock

        pixels = np.full((2, 3, 3), .3, np.float32)
        params = color_parameters(np.eye(3), (1, 1, 1))
        legacy = compiled_tone.tone
        broken = Mock(side_effect=RuntimeError("unavailable"))
        with patch.object(compiled_tone, "tone16", broken), patch.object(compiled_tone, "last_error", None):
            self.assertIsNone(compiled_tone.render(pixels, params, bit_depth=16))
            self.assertIsNone(compiled_tone.render(pixels, params, bit_depth=16))
            broken.assert_called_once()
            self.assertIs(compiled_tone.tone, legacy)

    def test_sixteen_bit_gpu_failure_keeps_cpu_precision(self):
        from unittest.mock import Mock

        raw = np.arange(192, dtype=np.uint16).reshape(12, 16) * 71
        options = dict(raw_bytes=raw.tobytes(), source_width=16, source_height=12,
                       crop=(0, 0, 16, 12), cfa_pattern=(0, 1, 1, 2),
                       black_levels=(0, 0, 0, 0), white_level=16383,
                       channel_gains=(1, 1, 1), camera_to_linear_srgb=None,
                       bit_depth=16, demosaic="malvar")
        expected = render_bayer_full_resolution(**options, use_gpu=False)
        broken = Mock()
        broken.bayer.side_effect = RuntimeError("device lost")
        with patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=broken), patch(
            "openraw_studio.raw.native.acceleration.disable_gpu"
        ) as disable:
            actual = render_bayer_full_resolution(**options)
        self.assertEqual(actual, expected)
        disable.assert_called_once()
        self.assertGreater(np.unique(np.frombuffer(actual.rgb_bytes, "<u2")).size, 256)

    def test_tone16_quantizes_float_directly_and_preserves_rgb8(self):
        pixels = np.linspace(0, 1.4, 8190, dtype=np.float32).reshape(30, 91, 3)
        params = color_parameters(np.eye(3), (1.2, 1, .9), highlights=-.3, shadows=.2)
        actual = compiled_tone.render(pixels, params, bit_depth=16)
        if compiled_tone.njit is None:
            self.skipTest("Numba unavailable")
        self.assertIsNotNone(actual, compiled_tone.last_error)
        expected = tone_cpu(pixels, params, bit_depth=16)
        self.assertLessEqual(np.abs(actual.astype(int) - expected).max(), 1)
        self.assertGreater(len(np.unique(actual)), 256)
        self.assertTrue(np.any(actual % 257 != 0))
        before8 = compiled_tone.render(pixels, params)
        self.assertEqual(before8.dtype, np.uint8)
        self.assertLessEqual(np.abs(before8.astype(int) - np.rint(actual / 257)).max(), 1)

    def test_bayer16_cpu_reference_chunks_and_gpu(self):
        raw = np.random.default_rng(411).integers(0, 16384, (34, 46), dtype=np.uint16)
        gpu = get_gpu()
        for method in ("bilinear", "malvar"):
            for pattern in ((0, 1, 1, 2), (1, 0, 2, 1), (1, 2, 0, 1), (2, 1, 1, 0)):
                with self.subTest(method=method, pattern=pattern):
                    options = dict(raw_bytes=raw.tobytes(), source_width=46, source_height=34,
                                   crop=(1, 1, 43, 31), cfa_pattern=pattern,
                                   black_levels=(16, 32, 48, 64), white_level=16383,
                                   channel_gains=(1.8, 1, 1.4),
                                   camera_to_linear_srgb=((1.3, -.2, -.1), (-.1, 1.2, -.1), (.1, -.2, 1.1)),
                                   highlights=-.3, shadows=.4, highlight_ceiling=1,
                                   demosaic=method, bit_depth=16)

                    def render(**extra):
                        result = render_bayer_full_resolution(**options, **extra)
                        self.assertEqual(result.bit_depth, 16)
                        return np.frombuffer(result.rgb_bytes, "<u2").astype(int)

                    reference = render(use_gpu=False, use_compiled=False, chunk_rows=5)
                    cpu = render(use_gpu=False, chunk_rows=7)
                    self.assertLessEqual(np.abs(cpu - reference).max(), 4)
                    self.assertGreater(len(np.unique(cpu)), 256)
                    self.assertTrue(np.any(cpu % 257 != 0))
                    np.testing.assert_array_equal(cpu, render(use_gpu=False, chunk_rows=31))
                    if gpu is not None:
                        with patch("openraw_studio.raw.native.acceleration.disable_gpu", side_effect=AssertionError("GPU fallback")):
                            actual = render()
                        self.assertLessEqual(np.abs(actual - reference).max(), 4)

    def test_rgb16_gpu_noise_validates_and_does_not_fall_back(self):
        from openraw_studio.raw.native.luminance import reduce_luminance_noise
        from openraw_studio.raw.native.chroma import reduce_color_noise

        gpu = get_gpu()
        if gpu is None:
            self.skipTest("No usable OpenCL GPU")
        pixels = np.random.default_rng(420).integers(0, 65536, (23, 31, 3), dtype=np.uint16)
        for function, tolerance in ((reduce_luminance_noise, 0), (reduce_color_noise, 1)):
            with self.subTest(filter=function.__name__):
                expected = function(pixels, .73, use_gpu=False, use_compiled=False)
                with patch("openraw_studio.raw.native.acceleration.disable_gpu", side_effect=AssertionError("GPU fallback")):
                    actual = function(pixels, .73)
                self.assertEqual(actual.dtype, np.uint16)
                self.assertLessEqual(np.abs(actual.astype(int) - expected.astype(int)).max(), tolerance)
        self.assertTrue({"luminance", "chroma"} <= gpu._validated16)
