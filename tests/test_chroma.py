import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.raw.native.acceleration import get_gpu
from openraw_studio.raw.native.chroma import reduce_color_noise


class ChromaTests(unittest.TestCase):
    def test_zero_is_exact_bypass_and_inputs_are_unchanged(self):
        pixels = np.random.default_rng(2).integers(0, 256, (17, 23, 3), dtype=np.uint8)
        original = pixels.copy()
        with patch(
            "openraw_studio.raw.native.acceleration.get_gpu",
            side_effect=AssertionError("No GPU for zero"),
        ):
            self.assertIs(reduce_color_noise(pixels, 0), pixels)
        reduce_color_noise(pixels, 0.7, use_gpu=False)
        np.testing.assert_array_equal(pixels, original)
        self.assertTrue(pixels.flags.writeable)

    def test_reference_and_compiled_agree_at_chunks_edges_and_strengths(self):
        for shape in ((1, 1, 3), (1, 8, 3), (9, 1, 3), (11, 17, 3)):
            pixels = np.random.default_rng(71).integers(0, 256, shape, dtype=np.uint8)
            for strength in (0.01, 0.5, 1):
                reference = reduce_color_noise(
                    pixels, strength, use_gpu=False, use_compiled=False
                )
                for rows in (1, 3, 128):
                    actual = reduce_color_noise(
                        pixels, strength, use_gpu=False, chunk_rows=rows
                    )
                    self.assertLessEqual(
                        np.abs(reference.astype(int) - actual.astype(int)).max(), 1
                    )

    def test_gray_texture_black_white_and_constant_colors_stay_exact(self):
        gray = (
            np.random.default_rng(15)
            .integers(0, 256, (31, 17, 1), dtype=np.uint8)
            .repeat(3, axis=2)
        )
        for pixels in (
            gray,
            np.full((7, 9, 3), (0, 255, 24), dtype=np.uint8),
            np.zeros((7, 9, 3), np.uint8),
            np.full((7, 9, 3), 255, np.uint8),
        ):
            np.testing.assert_array_equal(
                reduce_color_noise(pixels, 1, use_gpu=False), pixels
            )

    def test_luma_is_preserved_and_colored_noise_reduced(self):
        random = np.random.default_rng(721)
        noise = random.normal(0, 8, (80, 100, 3))
        pixels = np.rint(np.clip(110 + noise, 0, 255)).astype(np.uint8)
        after = reduce_color_noise(pixels, 1, use_gpu=False)
        weights = np.array([54, 183, 19]) / 256
        self.assertLessEqual(np.abs(pixels @ weights - after @ weights).max(), 0.5)
        before_chroma = pixels.astype(float) - (pixels @ weights)[:, :, None]
        after_chroma = after.astype(float) - (after @ weights)[:, :, None]
        self.assertLess(np.std(after_chroma), np.std(before_chroma) * 0.55)
        self.assertLess(
            np.abs(after.mean(axis=(0, 1)) - pixels.mean(axis=(0, 1))).max(), 0.5
        )

    def test_saturated_color_boundaries_are_not_blended_to_gray(self):
        pixels = np.zeros((31, 31, 3), np.uint8)
        pixels[:, :15] = (210, 55, 20)
        pixels[:, 15:] = (20, 80, 210)
        after = reduce_color_noise(pixels, 1, use_gpu=False)
        self.assertLessEqual(np.abs(after.astype(int) - pixels.astype(int)).max(), 1)

    def test_gpu_matches_reference_without_cpu_fallback(self):
        gpu = get_gpu()
        if gpu is None:
            self.skipTest("OpenCL GPU unavailable")
        gpu.chroma(np.zeros((2, 2, 3), np.uint8), 0.5)
        for shape in ((1, 1, 3), (1, 17, 3), (19, 1, 3), (25, 41, 3)):
            pixels = np.random.default_rng(48).integers(0, 256, shape, dtype=np.uint8)
            for strength in (0.1, 0.7, 1):
                expected = reduce_color_noise(
                    pixels, strength, use_gpu=False, use_compiled=False
                )
                with (
                    patch(
                        "openraw_studio.raw.native.chroma._reference_chunk",
                        side_effect=AssertionError("NumPy fallback"),
                    ),
                    patch(
                        "openraw_studio.raw.native.compiled_chroma.render_chunk",
                        side_effect=AssertionError("CPU fallback"),
                    ),
                ):
                    actual = reduce_color_noise(pixels, strength)
                self.assertLessEqual(
                    np.abs(expected.astype(int) - actual.astype(int)).max(), 1
                )

    def test_compiler_failure_uses_same_filter_reference(self):
        from openraw_studio.raw.native import compiled_chroma

        pixels = np.random.default_rng(99).integers(0, 256, (17, 31, 3), dtype=np.uint8)
        expected = reduce_color_noise(pixels, 0.7, use_gpu=False, use_compiled=False)
        with (
            patch.object(
                compiled_chroma, "chroma", side_effect=RuntimeError("Unavailable")
            ) as kernel,
            patch.object(compiled_chroma, "last_error", None),
        ):
            actual = reduce_color_noise(pixels, 0.7, use_gpu=False, chunk_rows=3)
            kernel.assert_called_once()
            self.assertIn("Unavailable", compiled_chroma.last_error)
        np.testing.assert_array_equal(actual, expected)

    def test_invalid_inputs_rejected(self):
        pixels = np.zeros((2, 3, 3), np.uint8)
        for strength in (-1, 1.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                reduce_color_noise(pixels, strength)
        for invalid in (
            pixels.astype(float),
            pixels[:, :, 0],
            pixels[:0],
            pixels[:, :, :2],
        ):
            with self.assertRaises(ValueError):
                reduce_color_noise(invalid, 1)

    def test_gpu_failure_falls_back_to_compiled_filter(self):
        from unittest.mock import Mock

        pixels = np.random.default_rng(1).integers(0, 256, (12, 14, 3), dtype=np.uint8)
        expected = reduce_color_noise(pixels, 0.7, use_gpu=False)
        gpu = Mock()
        gpu.chroma.side_effect = RuntimeError("Driver failure")
        with (
            patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=gpu),
            patch("openraw_studio.raw.native.acceleration.disable_gpu") as disable,
        ):
            actual = reduce_color_noise(pixels, 0.7)
        disable.assert_called_once()
        np.testing.assert_array_equal(actual, expected)

    def test_disk_cache_failure_retries_compilation_in_memory(self):
        from unittest.mock import Mock

        from openraw_studio.raw.native import compiled_chroma

        if compiled_chroma.njit is None:
            self.skipTest("Numba unavailable")
        pixels = np.zeros((4, 5, 3), np.uint8)
        retry = Mock(return_value=np.zeros_like(pixels))
        with (
            patch.object(
                compiled_chroma, "chroma", side_effect=OSError("Cache denied")
            ),
            patch.object(compiled_chroma, "njit", return_value=lambda _: retry),
            patch.object(compiled_chroma, "cache_disabled_reason", None),
        ):
            np.testing.assert_array_equal(
                reduce_color_noise(pixels, 1, use_gpu=False), pixels
            )
            retry.assert_called_once()
            self.assertIn("Cache denied", compiled_chroma.cache_disabled_reason)

    def test_readonly_view_and_numpy_chunk_boundaries_are_supported(self):
        pixels = np.random.default_rng(4).integers(0, 256, (29, 31, 3), dtype=np.uint8)
        pixels.flags.writeable = False
        reference = reduce_color_noise(pixels, 0.6, use_gpu=False, use_compiled=False)
        for rows in (1, 3, 8):
            np.testing.assert_array_equal(
                reference,
                reduce_color_noise(
                    pixels, 0.6, use_gpu=False, use_compiled=False, chunk_rows=rows
                ),
            )
        reduce_color_noise(pixels, 0.6, use_gpu=False)
        self.assertFalse(pixels.flags.writeable)

    def test_disabled_jit_uses_numpy_instead_of_slow_python_scalar_loops(self):
        import os
        import subprocess
        import sys

        script = """
import numpy as np
from openraw_studio.raw.native import compiled_chroma
from openraw_studio.raw.native.chroma import reduce_color_noise
assert compiled_chroma.chroma is None
pixels=np.random.default_rng(1).integers(0,256,(11,17,3),dtype=np.uint8)
np.testing.assert_array_equal(reduce_color_noise(pixels,.7,use_gpu=False),reduce_color_noise(pixels,.7,use_gpu=False,use_compiled=False))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "NUMBA_DISABLE_JIT": "1", "OPENRAW_GPU": "off"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compiled_algorithm_scratch_is_chunk_bounded(self):
        from openraw_studio.raw.native import compiled_chroma
        from openraw_studio.raw.native.chroma import COLOR, LIGHT, SPATIAL

        pixels = np.zeros((99, 18, 3), np.uint8)
        with patch.object(np, "empty", wraps=np.empty) as allocate:
            compiled_chroma._filter(pixels, 20, 23, 0.75, SPATIAL, LIGHT, COLOR)
        self.assertEqual(
            [call.args[0] for call in allocate.call_args_list], [(3, 18, 3), (7, 22, 3)]
        )
