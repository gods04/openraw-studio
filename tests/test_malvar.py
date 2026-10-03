import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.raw.native import compiled_bayer
from openraw_studio.raw.native.acceleration import get_gpu
from openraw_studio.raw.native.fullres import (
    _demosaic_numpy,
    render_bayer_full_resolution_rgb8,
)
from openraw_studio.raw.native.malvar import STANDARD_BAYER, demosaic_chunk


class MalvarTests(unittest.TestCase):
    def setUp(self):
        self.source = np.random.default_rng(80).integers(
            0, 17000, (15, 19), dtype=np.uint16
        )
        self.black = (32, 64, 48, 80)
        self.gains = (1.8, 1, 1.4)

    def test_reference_matches_published_convolution_filters(self):
        green = (
            np.array(
                [
                    [0, 0, -1, 0, 0],
                    [0, 0, 2, 0, 0],
                    [-1, 2, 4, 2, -1],
                    [0, 0, 2, 0, 0],
                    [0, 0, -1, 0, 0],
                ]
            )
            / 8
        )
        across = (
            np.array(
                [
                    [0, 0, -1.5, 0, 0],
                    [0, 2, 0, 2, 0],
                    [-1.5, 0, 6, 0, -1.5],
                    [0, 2, 0, 2, 0],
                    [0, 0, -1.5, 0, 0],
                ]
            )
            / 8
        )
        horizontal = (
            np.array(
                [
                    [0, 0, 0.5, 0, 0],
                    [0, -1, 0, -1, 0],
                    [-1, 4, 5, 4, -1],
                    [0, -1, 0, -1, 0],
                    [0, 0, 0.5, 0, 0],
                ]
            )
            / 8
        )
        for pattern in STANDARD_BAYER:
            planes = demosaic_chunk(
                self.source,
                (0, 0, 19, 15),
                0,
                15,
                pattern,
                self.black,
                16383,
                self.gains,
            )
            for y in range(2, 13):
                for x in range(2, 17):
                    window = np.empty((5, 5), np.float64)
                    for j in range(5):
                        for i in range(5):
                            pos = ((y + j - 2) & 1) * 2 + ((x + i - 2) & 1)
                            window[j, i] = (
                                np.clip(
                                    (
                                        int(self.source[y + j - 2, x + i - 2])
                                        - self.black[pos]
                                    )
                                    / (16383 - self.black[pos]),
                                    0,
                                    1,
                                )
                                * self.gains[pattern[pos]]
                            )
                    c = pattern[(y & 1) * 2 + (x & 1)]
                    for channel in range(3):
                        if c == channel:
                            expected = window[2, 2]
                        elif channel == 1:
                            expected = np.sum(window * green)
                        elif c != 1:
                            expected = np.sum(window * across)
                        else:
                            same_row = pattern[(y & 1) * 2 + ((x + 1) & 1)] == channel
                            expected = np.sum(
                                window * (horizontal if same_row else horizontal.T)
                            )
                        self.assertAlmostEqual(
                            float(planes[channel][y, x]), expected, places=5
                        )

    def test_compiled_matches_numpy_all_phases_crops_and_chunk_edges(self):
        if compiled_bayer.njit is None:
            self.skipTest("Numba unavailable")
        original = self.source.copy()
        for pattern in STANDARD_BAYER:
            for crop in (
                (0, 0, 19, 15),
                (1, 3, 16, 11),
                (2, 1, 9, 12),
                (1, 1, 2, 2),
                (1, 2, 3, 3),
            ):
                for start, end in ((0, 1), (1, crop[3]), (0, crop[3])):
                    args = (
                        self.source,
                        crop,
                        start,
                        end,
                        pattern,
                        self.black,
                        16383,
                        self.gains,
                    )
                    actual = compiled_bayer.render_chunk(*args, method="malvar")
                    self.assertIsNotNone(actual, compiled_bayer.last_error)
                    np.testing.assert_allclose(
                        actual,
                        np.stack(demosaic_chunk(*args), axis=2),
                        atol=1e-6,
                        rtol=1e-6,
                    )
        np.testing.assert_array_equal(self.source, original)
        self.assertTrue(self.source.flags.writeable)

    def test_flat_colors_and_measured_samples_are_preserved_at_edges(self):
        black = (63, 127, 191, 255)
        target = np.array([0.125, 0.25, 0.5])
        for pattern in STANDARD_BAYER:
            for height, width in ((2, 2), (3, 3), (8, 10)):
                phases = (np.arange(height)[:, None] & 1) * 2 + (
                    np.arange(width)[None, :] & 1
                )
                channels = np.asarray(pattern)[phases]
                levels = np.asarray(black)[phases]
                source = (target[channels] * (16383 - levels) + levels).astype(
                    np.uint16
                )
                # Equal calibrated constants with position-specific black levels.
                planes = demosaic_chunk(
                    source,
                    (0, 0, width, height),
                    0,
                    height,
                    pattern,
                    black,
                    16383,
                    self.gains,
                )
                for c in range(3):
                    np.testing.assert_allclose(
                        planes[c], target[c] * self.gains[c], rtol=1e-6, atol=1e-6
                    )
                    self.assertTrue(np.isfinite(planes[c]).all())

    def test_reconstruction_reduces_error_on_known_neutral_texture(self):
        y, x = np.mgrid[:80, :96]
        ground = (
            0.4 + 0.18 * np.sin(0.7 * x + 0.5 * y) + 0.08 * np.cos(0.31 * x - 0.27 * y)
        )
        raw = np.rint(ground * 16383).astype(np.uint16)
        args = (
            raw,
            (0, 0, 96, 80),
            0,
            80,
            (0, 1, 1, 2),
            (0, 0, 0, 0),
            16383,
            (1, 1, 1),
        )
        old = np.stack(_demosaic_numpy(np, *args), axis=2)[2:-2, 2:-2]
        new = np.stack(demosaic_chunk(*args), axis=2)[2:-2, 2:-2]
        target = ground[2:-2, 2:-2, None]
        self.assertLess(
            np.mean((new - target) ** 2), np.mean((old - target) ** 2) * 0.3
        )
        self.assertLess(
            np.mean(np.ptp(new, axis=2)), np.mean(np.ptp(old, axis=2)) * 0.5
        )

    def arguments(self):
        return {
            "raw_bytes": self.source.tobytes(),
            "source_width": 19,
            "source_height": 15,
            "crop": (1, 1, 16, 12),
            "cfa_pattern": (0, 1, 1, 2),
            "black_levels": self.black,
            "white_level": 16383,
            "channel_gains": self.gains,
            "camera_to_linear_srgb": None,
            "demosaic": "malvar",
            "use_gpu": False,
        }

    def test_full_color_matches_reference_and_chunk_sizes(self):
        for exposure in (-4, 0, 4):
            args = {
                **self.arguments(),
                "channel_gains": tuple(v * 2**exposure for v in self.gains),
                "highlights": -0.3,
                "shadows": 0.5,
                "saturation": 0.1,
                "contrast": -0.1,
            }
            expected = render_bayer_full_resolution_rgb8(**args, use_compiled=False)
            actual = [
                render_bayer_full_resolution_rgb8(**args, chunk_rows=n)
                for n in (1, 5, 256)
            ]
            self.assertEqual(actual[0], actual[1])
            self.assertEqual(actual[1], actual[2])
            self.assertLessEqual(
                np.max(
                    np.abs(
                        np.frombuffer(expected.rgb_bytes, np.uint8).astype(int)
                        - np.frombuffer(actual[0].rgb_bytes, np.uint8).astype(int)
                    )
                ),
                1,
            )

    def test_compiler_failure_uses_mhc_reference_not_bilinear(self):
        args = self.arguments()
        expected = render_bayer_full_resolution_rgb8(**args, use_compiled=False)
        failure = Mock(side_effect=RuntimeError("MHC compiler unavailable"))
        with (
            patch.object(compiled_bayer, "malvar_demosaic", failure),
            patch.object(compiled_bayer, "last_error", None),
        ):
            actual = render_bayer_full_resolution_rgb8(**args, chunk_rows=1)
            self.assertEqual(actual, expected)
            failure.assert_called_once()
            self.assertIn("MHC compiler unavailable", compiled_bayer.last_error)

    def test_unknown_method_and_non_bayer_layout_are_rejected(self):
        for override in ({"demosaic": "unknown"}, {"cfa_pattern": (0, 1, 2, 1)}):
            with self.assertRaises(ValueError):
                render_bayer_full_resolution_rgb8(**{**self.arguments(), **override})

    def test_gpu_matches_reference_for_all_layouts_and_adjustment_extremes(self):
        gpu = get_gpu()
        if gpu is None:
            self.skipTest("No validated GPU")
        for pattern in STANDARD_BAYER:
            for crop in ((0, 0, 19, 15), (1, 3, 16, 11), (1, 1, 2, 2), (1, 2, 3, 3)):
                for strength in (-1, 0, 1):
                    args = {
                        **self.arguments(),
                        "cfa_pattern": pattern,
                        "crop": crop,
                        "channel_gains": tuple(
                            v * 2 ** (strength * 4) for v in self.gains
                        ),
                        "camera_to_linear_srgb": (
                            (1.3, -0.2, -0.1),
                            (-0.1, 1.2, -0.1),
                            (0.1, -0.2, 1.1),
                        ),
                        "contrast": strength,
                        "highlights": -abs(strength),
                        "shadows": strength,
                        "saturation": strength,
                        "highlight_ceiling": 2 ** (strength * 4),
                    }
                    expected = render_bayer_full_resolution_rgb8(
                        **args, use_compiled=False
                    )
                    with patch.object(
                        compiled_bayer,
                        "render_chunk",
                        side_effect=AssertionError("GPU silently fell back"),
                    ):
                        actual = render_bayer_full_resolution_rgb8(
                            **{**args, "use_gpu": True}
                        )
                    self.assertLessEqual(
                        np.max(
                            np.abs(
                                np.frombuffer(expected.rgb_bytes, np.uint8).astype(int)
                                - np.frombuffer(actual.rgb_bytes, np.uint8).astype(int)
                            )
                        ),
                        1,
                    )
        self.assertTrue(gpu._malvar_validated)

    def test_failed_gpu_validation_falls_back_to_same_mhc_algorithm(self):
        from openraw_studio.raw.native.acceleration import OpenClRenderer

        gpu = object.__new__(OpenClRenderer)
        gpu._malvar_validated = False
        gpu._bayer = Mock(return_value=np.full((6, 8, 3), 255, dtype=np.uint8))
        args = self.arguments()
        expected = render_bayer_full_resolution_rgb8(**args)
        with (
            patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=gpu),
            patch("openraw_studio.raw.native.acceleration.disable_gpu") as disable,
        ):
            actual = render_bayer_full_resolution_rgb8(**{**args, "use_gpu": True})
        self.assertEqual(actual, expected)
        disable.assert_called_once()
        self.assertFalse(gpu._malvar_validated)

    def test_unwritable_mhc_cache_retries_in_memory(self):
        if compiled_bayer.njit is None:
            self.skipTest("Numba unavailable")
        with (
            patch.object(
                compiled_bayer,
                "malvar_demosaic",
                side_effect=OSError("cache unavailable"),
            ),
            patch.object(compiled_bayer, "cache_disabled_reason", None),
        ):
            actual = render_bayer_full_resolution_rgb8(**self.arguments())
            expected = render_bayer_full_resolution_rgb8(
                **self.arguments(), use_compiled=False
            )
            self.assertLessEqual(
                np.max(
                    np.abs(
                        np.frombuffer(expected.rgb_bytes, np.uint8).astype(int)
                        - np.frombuffer(actual.rgb_bytes, np.uint8).astype(int)
                    )
                ),
                1,
            )
            self.assertIn("cache unavailable", compiled_bayer.cache_disabled_reason)

    def test_mhc_tone_fallback_keeps_reference_colors(self):
        from openraw_studio.raw.native import compiled_tone

        expected = render_bayer_full_resolution_rgb8(
            **self.arguments(), use_compiled=False
        )
        with patch.object(compiled_tone, "tone", None):
            actual = render_bayer_full_resolution_rgb8(**self.arguments())
        self.assertLessEqual(
            np.max(
                np.abs(
                    np.frombuffer(expected.rgb_bytes, np.uint8).astype(int)
                    - np.frombuffer(actual.rgb_bytes, np.uint8).astype(int)
                )
            ),
            1,
        )

    def test_disabled_jit_uses_vector_reference(self):
        code = """
import numpy as np
from openraw_studio.raw.native import compiled_bayer, compiled_tone
from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8
assert compiled_bayer.malvar_demosaic is None and compiled_tone.tone is None
args = dict(raw_bytes=np.arange(16,dtype=np.uint16).tobytes(),source_width=4,
source_height=4,crop=(0,0,4,4),cfa_pattern=(0,1,1,2),black_levels=(0,0,0,0),
white_level=255,channel_gains=(1,1,1),camera_to_linear_srgb=None,use_gpu=False,demosaic='malvar')
assert render_bayer_full_resolution_rgb8(**args) == render_bayer_full_resolution_rgb8(**args,use_compiled=False)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "NUMBA_DISABLE_JIT": "1", "OPENRAW_GPU": "off"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_readonly_source_and_bounded_chunk_allocations(self):
        if compiled_bayer.njit is None:
            self.skipTest("Numba unavailable")
        original = self.source.copy()
        for readonly in (False, True):
            self.source.flags.writeable = not readonly
            actual = compiled_bayer.render_chunk(
                self.source,
                (1, 1, 16, 12),
                3,
                5,
                (0, 1, 1, 2),
                self.black,
                16383,
                self.gains,
                method="malvar",
            )
            self.assertEqual(actual.shape, (2, 16, 3))
            self.assertEqual(self.source.flags.writeable, not readonly)
            np.testing.assert_array_equal(original, self.source)
        self.assertEqual(len(compiled_bayer.malvar_demosaic.signatures), 1)


if __name__ == "__main__":
    unittest.main()
