import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.raw.native.luminance import reduce_luminance_noise


class LuminanceNoiseTests(unittest.TestCase):
    def test_zero_bypass_and_input_immutability(self):
        pixels = np.random.default_rng(42).integers(0, 256, (13, 17, 3), dtype=np.uint8)
        original = pixels.copy()
        with patch('openraw_studio.raw.native.acceleration.get_gpu', side_effect=AssertionError('zero')):
            self.assertIs(reduce_luminance_noise(pixels, 0), pixels)
        reduce_luminance_noise(pixels, .7, use_gpu=False)
        np.testing.assert_array_equal(pixels, original)

    def test_cpu_reference_agrees_at_boundaries_strengths_and_chunks(self):
        for shape in ((1, 1, 3), (1, 23, 3), (17, 1, 3), (27, 33, 3)):
            pixels = np.random.default_rng(23).integers(0, 256, shape, dtype=np.uint8)
            pixels.flags.writeable = False
            for strength in (.01, .5, 1):
                expected = reduce_luminance_noise(pixels, strength, use_gpu=False, use_compiled=False)
                for rows in (1, 7, 128):
                    actual = reduce_luminance_noise(pixels, strength, use_gpu=False, chunk_rows=rows)
                    np.testing.assert_array_equal(actual, expected)
                    np.testing.assert_array_equal(expected, reduce_luminance_noise(pixels, strength, use_gpu=False, use_compiled=False, chunk_rows=rows))

    def test_flat_grain_reduced_without_changing_color_differences(self):
        rng = np.random.default_rng(721)
        grain = np.rint(rng.normal(0, 8, (80, 100, 1))).astype(int)
        pixels = (np.array([110, 125, 96]) + grain).astype(np.uint8)
        after = reduce_luminance_noise(pixels, 1, use_gpu=False)
        self.assertLess(np.std(after[..., 0]), np.std(pixels[..., 0]) * .65)
        delta = after.astype(int) - pixels.astype(int)
        np.testing.assert_array_equal(delta[..., 0], delta[..., 1])
        np.testing.assert_array_equal(delta[..., 0], delta[..., 2])
        self.assertLess(np.abs(after.mean((0, 1)) - pixels.mean((0, 1))).max(), .5)

    def test_constant_colors_and_strong_edges_preserved(self):
        for color in ((0, 0, 0), (255, 255, 255), (0, 255, 10), (135, 100, 74)):
            pixels = np.full((19, 21, 3), color, np.uint8)
            np.testing.assert_array_equal(reduce_luminance_noise(pixels, 1, use_gpu=False), pixels)
        pixels[:, :10] = (20, 35, 25)
        pixels[:, 10:] = (210, 225, 215)
        np.testing.assert_array_equal(reduce_luminance_noise(pixels, 1, use_gpu=False), pixels)

    def test_shift_is_bounded_before_application_and_never_wraps(self):
        pixels = np.random.default_rng(982).integers(0, 256, (40, 50, 3), dtype=np.uint8)
        after = reduce_luminance_noise(pixels, 1, use_gpu=False)
        delta = after.astype(int) - pixels.astype(int)
        np.testing.assert_array_equal(delta[..., 0], delta[..., 1])
        np.testing.assert_array_equal(delta[..., 0], delta[..., 2])
        self.assertLessEqual(np.abs(delta).max(), 20)

    def test_invalid_inputs_rejected(self):
        pixels = np.zeros((3, 4, 3), np.uint8)
        for strength in (-1, 1.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                reduce_luminance_noise(pixels, strength)
        for invalid in (pixels.astype(float), pixels[:0], pixels[..., :2], pixels[..., 0]):
            with self.assertRaises(ValueError):
                reduce_luminance_noise(invalid, 1)
        with self.assertRaises(ValueError):
            reduce_luminance_noise(pixels, .1, chunk_rows=0)

    def test_gpu_agrees_without_cpu_fallback_and_preserves_color_differences(self):
        from openraw_studio.raw.native.acceleration import get_gpu

        if get_gpu() is None:
            self.skipTest('OpenCL GPU unavailable')
        for shape in ((1, 1, 3), (1, 19, 3), (21, 1, 3), (27, 33, 3)):
            pixels = np.random.default_rng(29).integers(0, 256, shape, dtype=np.uint8)
            for strength in (.01, .5, 1):
                expected = reduce_luminance_noise(pixels, strength, use_gpu=False, use_compiled=False)
                with patch('openraw_studio.raw.native.compiled_luminance.render_chunk', side_effect=AssertionError('fallback')):
                    actual = reduce_luminance_noise(pixels, strength)
                np.testing.assert_array_equal(actual, expected)
                delta = actual.astype(int) - pixels.astype(int)
                np.testing.assert_array_equal(delta[..., 0], delta[..., 1])
                np.testing.assert_array_equal(delta[..., 0], delta[..., 2])

    def test_driver_and_compiler_failure_keep_reference_available(self):
        from unittest.mock import Mock
        from openraw_studio.raw.native import compiled_luminance

        pixels = np.random.default_rng(24).integers(0, 256, (13, 17, 3), dtype=np.uint8)
        expected = reduce_luminance_noise(pixels, .7, use_gpu=False, use_compiled=False)
        gpu = Mock()
        gpu.luminance.side_effect = RuntimeError('Driver failure')
        with patch('openraw_studio.raw.native.acceleration.get_gpu', return_value=gpu), patch('openraw_studio.raw.native.acceleration.disable_gpu') as disable, patch.object(compiled_luminance, 'luminance', side_effect=RuntimeError('Compiler unavailable')) as kernel, patch.object(compiled_luminance, 'last_error', None):
            actual = reduce_luminance_noise(pixels, .7, chunk_rows=3)
            disable.assert_called_once()
            kernel.assert_called_once()
            self.assertIn('unavailable', compiled_luminance.last_error)
        np.testing.assert_array_equal(actual, expected)

    def test_disk_cache_failure_retries_in_memory(self):
        from unittest.mock import Mock
        from openraw_studio.raw.native import compiled_luminance

        if compiled_luminance.njit is None:
            self.skipTest('Numba unavailable')
        pixels = np.zeros((4, 5, 3), np.uint8)
        retry = Mock(return_value=pixels.copy())
        with patch.object(compiled_luminance, 'luminance', side_effect=OSError('cache denied')), patch.object(compiled_luminance, 'njit', return_value=lambda _: retry), patch.object(compiled_luminance, 'cache_disabled_reason', None):
            np.testing.assert_array_equal(reduce_luminance_noise(pixels, 1, use_gpu=False), pixels)
            retry.assert_called_once()
            self.assertIn('cache denied', compiled_luminance.cache_disabled_reason)

    def test_cpu_scratch_stays_bounded_by_chunk_height(self):
        from openraw_studio.raw.native import compiled_luminance
        from openraw_studio.raw.native.luminance import WEIGHTS

        pixels = np.zeros((99, 18, 3), np.uint8)
        with patch.object(np, 'empty', wraps=np.empty) as allocate:
            compiled_luminance._filter(pixels, 20, 23, round(.75 * 65536), WEIGHTS)
        self.assertEqual([call.args[0] for call in allocate.call_args_list], [(3,18,3), (7,22), (7,22)])
        self.assertEqual(allocate.call_args_list[-1].args[1], np.uint8)

    def test_sixteen_bit_guide_rounding_covers_every_gray_value(self):
        values = np.random.default_rng(833).permutation(65536).astype(np.uint16).reshape(256, 256)
        pixels = np.repeat(values[..., None], 3, axis=2)
        pixels.flags.writeable = False
        for strength in (.01, .6, 1):
            expected = reduce_luminance_noise(pixels, strength, use_gpu=False, use_compiled=False)
            for rows in (1, 17, 128):
                with self.subTest(strength=strength, rows=rows):
                    actual = reduce_luminance_noise(pixels, strength, use_gpu=False, chunk_rows=rows)
                    np.testing.assert_array_equal(actual, expected)

    def test_guide_extremes_and_signed_distances_preserve_reference(self):
        for dtype, maximum in ((np.uint8, 255), (np.uint16, 65535)):
            colors = np.array([(0,0,0), (maximum,maximum,maximum), (maximum,0,0),
                               (0,maximum,0), (0,0,maximum), (1,2,3),
                               (maximum-1,maximum-2,maximum-3)], dtype)
            pixels = np.tile(colors, (13, 5, 1))
            for strength in (.5, 1):
                with self.subTest(dtype=dtype, strength=strength):
                    expected = reduce_luminance_noise(pixels, strength, use_gpu=False, use_compiled=False)
                    actual = reduce_luminance_noise(pixels, strength, use_gpu=False, chunk_rows=3)
                    np.testing.assert_array_equal(actual, expected)

    def test_rgb16_strided_readonly_input_and_narrow_boundaries(self):
        base = np.random.default_rng(834).integers(0, 65536, (19, 29, 3), dtype=np.uint16)
        for pixels in (base[::-2, ::-2], base[:1], base[:, :1], base[:1, :1]):
            pixels.flags.writeable = False
            before = pixels.copy()
            for rows in (1, 7, 128):
                with self.subTest(shape=pixels.shape, rows=rows):
                    actual = reduce_luminance_noise(pixels, .73, use_gpu=False, chunk_rows=rows)
                    expected = reduce_luminance_noise(pixels, .73, use_gpu=False, use_compiled=False)
                    np.testing.assert_array_equal(actual, expected)
                    np.testing.assert_array_equal(pixels, before)

    def test_guide_kernel_compiles_for_both_depths_without_reference_fallback(self):
        from openraw_studio.raw.native import compiled_luminance
        from openraw_studio.raw.native.luminance import WEIGHTS, _reference_chunk

        if compiled_luminance.njit is None:
            self.skipTest('Numba unavailable')
        for dtype, maximum in ((np.uint8, 256), (np.uint16, 65536)):
            pixels = np.random.default_rng(835).integers(0, maximum, (33, 41, 3), dtype=dtype)
            for start, end in ((0, 1), (7, 17), (32, 33)):
                with self.subTest(dtype=dtype, bounds=(start, end)):
                    actual = compiled_luminance.render_chunk(pixels, start, end, .6, WEIGHTS)
                    self.assertIsNotNone(actual, compiled_luminance.last_error)
                    np.testing.assert_array_equal(actual, _reference_chunk(pixels, start, end, .6))

    def test_disabled_jit_keeps_vectorized_reference(self):
        import os
        import subprocess
        import sys

        script = '''
import numpy as np
from openraw_studio.raw.native import compiled_luminance
from openraw_studio.raw.native.luminance import reduce_luminance_noise
assert compiled_luminance.luminance is None
p = np.random.default_rng(1).integers(0,256,(11,17,3),dtype=np.uint8)
np.testing.assert_array_equal(reduce_luminance_noise(p,.7,use_gpu=False),reduce_luminance_noise(p,.7,use_gpu=False,use_compiled=False))
'''
        result = subprocess.run([sys.executable, '-c', script], env={**os.environ, 'NUMBA_DISABLE_JIT':'1', 'OPENRAW_GPU':'off'}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
