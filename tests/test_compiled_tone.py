import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.raw.native import compiled_tone
from openraw_studio.raw.native.acceleration import (
    color_parameters,
    render_tone,
    tone_cpu,
)


class CompiledToneTests(unittest.TestCase):
    def setUp(self):
        self.pixels = np.random.default_rng(53).uniform(-.1, 1.4, (24, 32, 3)).astype(np.float32)
        self.matrix = ((1.3, -.2, -.1), (-.1, 1.2, -.1), (.1, -.2, 1.1))
        self.params = color_parameters(self.matrix, (2.1, 1, 1.6), highlights=-.3, shadows=.2)

    def require_compiler(self):
        if compiled_tone.njit is None:
            self.skipTest("Numba unavailable")

    def assert_render_matches(self, pixels, params):
        actual = compiled_tone.render(pixels, params)
        self.assertIsNotNone(actual, compiled_tone.last_error)
        self.assertEqual(actual.shape, pixels.shape)
        self.assertEqual(actual.dtype, np.uint8)
        expected = tone_cpu(np.asarray(pixels, dtype=np.float32), params)
        self.assertLessEqual(np.abs(actual.astype(int) - expected.astype(int)).max(initial=0), 1)
        return actual

    def test_compiled_matches_reference_across_modes_and_adjustment_extremes(self):
        self.require_compiler()
        for linear in (False, True):
            for exposure in (-4, 0, 4):
                for highlights in (-1, -.3, 0, 1):
                    for ceiling in (None, 2.0 ** exposure):
                        with self.subTest(linear=linear, exposure=exposure, highlights=highlights, ceiling=ceiling):
                            params = color_parameters(
                                self.matrix, np.array([2.1, 1, 1.6]) * 2.0 ** exposure,
                                contrast=highlights, highlights=highlights, shadows=-highlights,
                                saturation=highlights, linear_saturation=linear, highlight_ceiling=ceiling,
                            )
                            self.assert_render_matches(self.pixels, params)

    def test_black_white_join_and_over_range_are_not_flattened(self):
        self.require_compiler()
        values = np.array([0, 1e-8, .9999, 1, 1.0001, 1.4, 2, 4, 16], np.float32)
        pixels = np.repeat(values, 3).reshape(1, -1, 3)
        for highlights in (-1, -.3, -1e-7, 0, 1):
            params = color_parameters(np.eye(3), (1, 1, 1), highlights=highlights)
            actual = self.assert_render_matches(pixels, params)
            np.testing.assert_array_equal(actual[0, 0], [0, 0, 0])
            self.assertTrue(np.all(np.diff(actual[0, :, 0].astype(int)) >= 0))
            if highlights == -1:
                self.assertEqual(len(set(actual[0, 5:8, 0])), 3)

    def test_strided_readonly_empty_and_float64_inputs(self):
        self.require_compiler()
        original = self.pixels.copy()
        params_before = self.params.copy()
        self.assert_render_matches(self.pixels, self.params)
        self.assertTrue(self.pixels.flags.writeable)
        self.assertTrue(self.params.flags.writeable)
        self.pixels.flags.writeable = False
        for pixels in (self.pixels, self.pixels[::-1, ::2], self.pixels.astype(np.float64), self.pixels[:0]):
            self.assert_render_matches(pixels, self.params)
        np.testing.assert_array_equal(self.pixels, original)
        np.testing.assert_array_equal(self.params, params_before)
        self.assertFalse(self.pixels.flags.writeable)
        self.assertEqual(len(compiled_tone.tone.signatures), 1)

    def test_no_input_copy_or_flag_changes_for_contiguous_proxies(self):
        def kernel(pixels, params):
            self.assertTrue(np.shares_memory(pixels, self.pixels))
            self.assertTrue(np.shares_memory(params, self.params))
            self.assertFalse(pixels.flags.writeable)
            self.assertFalse(params.flags.writeable)
            return np.zeros(pixels.shape, np.uint8)

        with patch.object(compiled_tone, "tone", side_effect=kernel):
            compiled_tone.render(self.pixels, self.params)
        self.assertTrue(self.pixels.flags.writeable)
        self.assertTrue(self.params.flags.writeable)

    def test_shape_errors_do_not_enter_unchecked_kernel(self):
        with patch.object(compiled_tone, "tone") as kernel:
            for pixels, params in ((self.pixels[..., :2], self.params), (self.pixels[0], self.params), (self.pixels, self.params[:17])):
                with self.assertRaisesRegex(ValueError, "H x W x 3"):
                    compiled_tone.render(pixels, params)
            kernel.assert_not_called()

    def test_compiler_failure_falls_back_and_does_not_retry_each_frame(self):
        broken = Mock(side_effect=RuntimeError("compiler unavailable"))
        with (
            patch.object(compiled_tone, "tone", broken),
            patch.object(compiled_tone, "last_error", None),
            patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=None),
        ):
            for _ in range(2):
                actual, backend = render_tone(self.pixels, self.params)
                self.assertEqual(backend, "CPU")
                np.testing.assert_array_equal(actual, tone_cpu(self.pixels, self.params))
            broken.assert_called_once()
            self.assertIn("compiler unavailable", compiled_tone.last_error)

    def test_unwritable_cache_uses_uncached_compilation(self):
        self.require_compiler()
        with (
            patch.object(compiled_tone, "tone", side_effect=OSError("cache unavailable")),
            patch.object(compiled_tone, "cache_disabled_reason", None),
            patch.object(compiled_tone, "last_error", None),
        ):
            self.assert_render_matches(self.pixels, self.params)
            self.assertTrue(compiled_tone.tone.signatures)
            self.assertIn("cache unavailable", compiled_tone.cache_disabled_reason)
            self.assertIsNone(compiled_tone.last_error)

    def test_programming_errors_are_not_silently_hidden(self):
        with (
            patch.object(compiled_tone, "tone", side_effect=ValueError("bad input")),
            self.assertRaisesRegex(ValueError, "bad input"),
        ):
            compiled_tone.render(self.pixels, self.params)

    def test_gpu_success_does_not_compile_cpu_tones(self):
        expected = tone_cpu(self.pixels, self.params)
        gpu = Mock(name="gpu")
        gpu.name = "test device"
        gpu.tone.return_value = expected
        with (
            patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=gpu),
            patch.object(compiled_tone, "render") as cpu,
        ):
            actual, backend = render_tone(self.pixels, self.params)
            self.assertIs(actual, expected)
            self.assertEqual(backend, "GPU: test device")
            cpu.assert_not_called()

    def test_explicitly_disabled_jit_uses_numpy_not_python_pixel_loops(self):
        check = """
import numpy as np
from openraw_studio.raw.native import compiled_tone
from openraw_studio.raw.native.acceleration import color_parameters, render_tone, tone_cpu
assert compiled_tone.tone is None
assert 'NUMBA_DISABLE_JIT' in compiled_tone.last_error
pixels = np.full((2, 3, 3), .2, dtype=np.float32)
params = color_parameters(np.eye(3), (1, 1, 1), highlights=-.3)
actual, backend = render_tone(pixels, params)
assert backend == 'CPU'
np.testing.assert_array_equal(actual, tone_cpu(pixels, params))
"""
        result = subprocess.run(
            [sys.executable, "-c", check], capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "NUMBA_DISABLE_JIT": "1", "OPENRAW_GPU": "off"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_concurrent_workers_keep_independent_frames(self):
        self.require_compiler()
        alternate = color_parameters(self.matrix, (1, 1, 1), highlights=-1)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(compiled_tone.render, self.pixels, params) for params in (self.params, alternate)]
            results = [future.result() for future in futures]
        self.assertFalse(np.shares_memory(*results))
        for actual, params in zip(results, (self.params, alternate)):
            self.assertLessEqual(np.abs(actual.astype(int) - tone_cpu(self.pixels, params).astype(int)).max(), 1)


if __name__ == "__main__":
    unittest.main()
