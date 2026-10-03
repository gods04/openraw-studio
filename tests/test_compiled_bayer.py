import itertools
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.raw.native import compiled_bayer, compiled_tone
from openraw_studio.raw.native.fullres import (
    _demosaic_numpy,
    render_bayer_full_resolution_rgb8,
)


class CompiledBayerTests(unittest.TestCase):
    def setUp(self):
        self.source = np.random.default_rng(41).integers(0, 65536, (19, 23), dtype=np.uint16)
        self.arguments = {
            "raw_bytes": self.source.tobytes(), "source_width": 23, "source_height": 19,
            "crop": (1, 3, 21, 15), "cfa_pattern": (0, 1, 1, 2),
            "black_levels": (64, 80, 96, 112), "white_level": 16383,
            "channel_gains": (1.8, 1, 1.3),
            "camera_to_linear_srgb": ((1.3, -.2, -.1), (-.1, 1.2, -.1), (.1, -.2, 1.1)),
            "highlights": -.3, "shadows": .2, "saturation": .1, "use_gpu": False,
        }

    def require_compiler(self):
        if compiled_bayer.njit is None:
            self.skipTest("Numba unavailable")

    def test_linear_interpolation_matches_reference_at_crops_edges_and_all_layouts(self):
        self.require_compiler()
        original = self.source.copy()
        for pattern in sorted(set(itertools.permutations((0, 1, 1, 2)))):
            for crop in ((0, 0, 23, 19), (1, 1, 21, 17), (2, 1, 19, 18), (1, 2, 2, 2)):
                for start, end in ((0, 1), (1, crop[3]), (0, crop[3])):
                    args = (self.source, crop, start, end, pattern, (64, 80, 96, 112), 16383, (1.8, 1, 1.3))
                    with self.subTest(pattern=pattern, crop=crop, rows=(start, end)):
                        actual = compiled_bayer.render_chunk(*args)
                        expected = np.stack(_demosaic_numpy(np, *args), axis=2)
                        self.assertIsNotNone(actual, compiled_bayer.last_error)
                        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(self.source, original)
        self.assertTrue(self.source.flags.writeable)

    def test_full_color_matches_numpy_for_control_extremes_and_chunk_sizes(self):
        self.require_compiler()
        for exposure in (-4, 0, 4):
            for control in (-1, -.3, 0, 1):
                args = {**self.arguments, "channel_gains": tuple(x * 2.0 ** exposure for x in (1.8, 1, 1.3)),
                        "contrast": control, "highlights": control, "shadows": -control, "saturation": control}
                for ceiling in (None, 2.0 ** exposure):
                    args["highlight_ceiling"] = ceiling
                    reference = render_bayer_full_resolution_rgb8(**args, use_compiled=False)
                    outputs = [render_bayer_full_resolution_rgb8(**args, chunk_rows=rows) for rows in (1, 4, 256)]
                    self.assertEqual(outputs[0], outputs[1])
                    self.assertEqual(outputs[1], outputs[2])
                    difference = np.abs(np.frombuffer(outputs[0].rgb_bytes, np.uint8).astype(int)
                                        - np.frombuffer(reference.rgb_bytes, np.uint8).astype(int))
                    self.assertLessEqual(difference.max(), 1)

    def test_compilation_does_not_allocate_a_whole_float_image(self):
        self.require_compiler()
        kernel = compiled_bayer.render_chunk
        calls = []

        def checked(source, crop, start, end, *args):
            self.assertTrue(np.shares_memory(source, np.frombuffer(self.arguments["raw_bytes"], np.uint16)))
            result = kernel(source, crop, start, end, *args)
            self.assertEqual(result.shape, (end - start, crop[2], 3))
            self.assertLessEqual(end - start, 4)
            calls.append((start, end))
            return result

        with patch.object(compiled_bayer, "render_chunk", side_effect=checked):
            render_bayer_full_resolution_rgb8(**self.arguments, chunk_rows=4)
        self.assertEqual(calls, [(0, 4), (4, 8), (8, 12), (12, 15)])

    def test_readonly_inputs_share_one_compiled_signature(self):
        self.require_compiler()
        for readonly in (False, True):
            self.source.flags.writeable = not readonly
            result = compiled_bayer.render_chunk(self.source, (0, 0, 23, 19), 0, 1,
                                                 (0, 1, 1, 2), (0, 0, 0, 0), 65535, (1, 1, 1))
            self.assertEqual(result.shape, (1, 23, 3))
            self.assertEqual(self.source.flags.writeable, not readonly)
        self.assertEqual(len(compiled_bayer.demosaic.signatures), 1)

    def test_compiler_failure_retains_reference_and_stops_retrying(self):
        expected = render_bayer_full_resolution_rgb8(**self.arguments, use_compiled=False)
        broken = Mock(side_effect=RuntimeError("compiler unavailable"))
        with patch.object(compiled_bayer, "demosaic", broken), patch.object(compiled_bayer, "last_error", None):
            actual = render_bayer_full_resolution_rgb8(**self.arguments, chunk_rows=1)
            self.assertEqual(actual, expected)
            broken.assert_called_once()
            self.assertIn("compiler unavailable", compiled_bayer.last_error)

    def test_tone_failure_keeps_compiled_interpolation_and_numpy_color(self):
        self.require_compiler()
        expected = render_bayer_full_resolution_rgb8(**self.arguments, use_compiled=False)
        with patch.object(compiled_tone, "tone", None):
            actual = render_bayer_full_resolution_rgb8(**self.arguments, chunk_rows=4)
        self.assertEqual(actual, expected)
        self.assertTrue(compiled_bayer.demosaic.signatures)

    def test_unwritable_cache_retries_without_disk_cache(self):
        self.require_compiler()
        with (
            patch.object(compiled_bayer, "demosaic", side_effect=OSError("cache unavailable")),
            patch.object(compiled_bayer, "cache_disabled_reason", None),
            patch.object(compiled_bayer, "last_error", None),
        ):
            result = render_bayer_full_resolution_rgb8(**self.arguments)
            self.assertEqual((result.width, result.height), (21, 15))
            self.assertTrue(compiled_bayer.demosaic.signatures)
            self.assertIn("cache unavailable", compiled_bayer.cache_disabled_reason)
            self.assertIsNone(compiled_bayer.last_error)

    def test_invalid_bounds_and_layout_do_not_enter_kernel(self):
        with patch.object(compiled_bayer, "demosaic") as kernel:
            for crop, start, end, pattern in (
                ((-1, 0, 20, 10), 0, 1, (0, 1, 1, 2)),
                ((0, 0, 24, 19), 0, 1, (0, 1, 1, 2)),
                ((0, 0, 23, 19), -1, 1, (0, 1, 1, 2)),
                ((0, 0, 23, 19), 0, 20, (0, 1, 1, 2)),
                ((0, 0, 23, 19), 1, 1, (0, 1, 1, 2)),
                ((0, 0, 23, 19), 0, 1, (0, 1, 2, 3)),
            ):
                with self.assertRaises(ValueError):
                    compiled_bayer.render_chunk(self.source, crop, start, end, pattern, (0, 0, 0, 0), 65535, (1, 1, 1))
            kernel.assert_not_called()

    def test_programming_error_is_not_hidden(self):
        with (
            patch.object(compiled_bayer, "demosaic", side_effect=ValueError("bad input")),
            self.assertRaisesRegex(ValueError, "bad input"),
        ):
            render_bayer_full_resolution_rgb8(**self.arguments)

    def test_gpu_success_does_not_compile_cpu_bayer(self):
        class Gpu:
            def bayer(self, *args):
                return np.zeros((15, 21, 3), np.uint8)

        with (
            patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=Gpu()),
            patch.object(compiled_bayer, "render_chunk") as cpu,
        ):
            render_bayer_full_resolution_rgb8(**{**self.arguments, "use_gpu": True})
            cpu.assert_not_called()

    def test_disabled_jit_uses_numpy_without_scalar_pixel_loops(self):
        code = """
import numpy as np
from openraw_studio.raw.native import compiled_bayer, compiled_tone
from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8
assert compiled_bayer.demosaic is None and compiled_tone.tone is None
assert 'NUMBA_DISABLE_JIT' in compiled_bayer.last_error
args = dict(raw_bytes=np.arange(16, dtype=np.uint16).tobytes(), source_width=4,
source_height=4, crop=(0,0,4,4), cfa_pattern=(0,1,1,2), black_levels=(0,0,0,0),
white_level=255, channel_gains=(1,1,1), camera_to_linear_srgb=None, use_gpu=False)
assert render_bayer_full_resolution_rgb8(**args) == render_bayer_full_resolution_rgb8(**args, use_compiled=False)
"""
        result = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True,
                                timeout=30, env={**os.environ, "NUMBA_DISABLE_JIT": "1", "OPENRAW_GPU": "off"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
