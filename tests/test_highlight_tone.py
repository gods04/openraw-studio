import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes

from openraw_studio.raw.native.acceleration import (
    _validate_tone_renderer,
    color_parameters,
    get_gpu,
    tone_cpu,
)
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.raw.native.nikon import (
    _bayer_blocks_to_rgb8_python,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.tonal import (
    apply_tonal_regions,
    apply_tonal_regions_array,
)


class HighlightToneTests(unittest.TestCase):
    def test_device_validation_rejects_a_broken_highlight_branch(self):
        class Renderer:
            def __init__(self, broken=False):
                self.broken = broken
                self.modes = []

            def tone(self, samples, params):
                self.modes.append((params[13], params[16]))
                if self.broken and params[13] < 0:
                    return np.zeros(samples.shape, dtype=np.uint8)
                return tone_cpu(samples, params)

        good = Renderer()
        self.assertTrue(_validate_tone_renderer(good))
        self.assertEqual(len(good.modes), 3)
        self.assertEqual(good.modes[-1][1], 1)
        self.assertFalse(_validate_tone_renderer(Renderer(broken=True)))

    def test_negative_highlights_retain_order_above_display_white(self):
        values = np.linspace(1, 16, 2000)
        for highlights in (-1, -0.5, -0.3, -0.01):
            result = np.array(
                [
                    apply_tonal_regions(x, highlights=highlights, shadows=0)
                    for x in values
                ]
            )
            self.assertTrue(np.all(np.diff(result) > 0))
            self.assertTrue(np.all(result < 1))
            self.assertAlmostEqual(result[0], 1 + 0.3 * highlights)
        pixels = np.repeat(
            np.linspace(1.4, 4, 100, dtype=np.float32)[:, None], 3, axis=1
        )
        result = tone_cpu(pixels, color_parameters(np.eye(3), (1, 1, 1), highlights=-1))
        self.assertLess(result.max(), 255)
        self.assertGreater(len(np.unique(result[:, 0])), 10)

    def test_white_join_is_continuous_with_matching_slope(self):
        step = 1e-8
        for highlights in (-1, -0.3, -0.01, -0.0001):
            for shadows in (-1, 0, 1):
                y = [
                    apply_tonal_regions(x, highlights=highlights, shadows=shadows)
                    for x in (1 - step, 1, 1 + step)
                ]
                expected_slope = 1 + 0.6 * highlights
                self.assertAlmostEqual(
                    (y[1] - y[0]) / step, expected_slope, delta=0.001
                )
                self.assertAlmostEqual(
                    (y[2] - y[1]) / step, expected_slope, delta=0.001
                )

    def test_existing_subwhite_and_nonnegative_highlight_curves_are_preserved(self):
        for highlights in (-1, -0.3, 0, 0.5, 1):
            for shadows in (-1, 0, 1):
                values = np.linspace(-0.3, 1 if highlights < 0 else 8, 201)
                position = np.clip(values, 0, 1)
                expected = (
                    values
                    + shadows * 1.2 * position * (1 - position) ** 2
                    + highlights * 0.3 * position**2
                )
                actual = np.array(
                    [
                        apply_tonal_regions(x, highlights=highlights, shadows=shadows)
                        for x in values
                    ]
                )
                np.testing.assert_allclose(actual, expected, atol=1e-14)
        self.assertEqual(apply_tonal_regions(0, highlights=-1, shadows=1), 0)

    def test_array_and_scalar_tones_match_for_shadows_extremes_and_tiny_strength(self):
        values = (
            np.random.default_rng(6).uniform(-0.4, 32, (30, 24, 3)).astype(np.float32)
        )
        for highlights in (-1, -0.3, -1e-6, 0, 0.5):
            for shadows in (-1, 0, 1):
                actual = values.copy()
                apply_tonal_regions_array(
                    actual, highlights=highlights, shadows=shadows
                )
                expected = np.array(
                    [
                        apply_tonal_regions(
                            float(x), highlights=highlights, shadows=shadows
                        )
                        for x in values.flat
                    ]
                ).reshape(values.shape)
                np.testing.assert_allclose(actual, expected, atol=2e-6)

    def test_gpu_tone_matches_cpu_above_white_in_both_saturation_modes(self):
        gpu = get_gpu()
        if gpu is None:
            self.skipTest("No usable OpenCL GPU")
        values = np.random.default_rng(17).uniform(0, 2, (13, 21, 3)).astype(np.float32)
        matrix = np.array([[1.7, -0.5, -0.2], [-0.1, 1.3, -0.2], [0.1, -0.3, 1.2]])
        for linear in (False, True):
            for strength in (-1, -0.3, -1e-6, 0, 0.5):
                params = color_parameters(
                    matrix,
                    (4, 2, 3),
                    highlights=strength,
                    shadows=0.4,
                    contrast=0.7,
                    saturation=0.2,
                    linear_saturation=linear,
                )
                actual = gpu.tone(values, params).astype(int)
                expected = tone_cpu(values, params).astype(int)
                self.assertLessEqual(np.abs(actual - expected).max(), 1)

    def test_live_fast_python_and_full_nikon_match_even_above_old_lut_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "highlights.NEF"
            source.write_bytes(
                synthetic_nikon_nef_compressed_bytes(
                    width=16,
                    height=12,
                    samples=(12000,) * (16 * 12),
                    active_area=(0, 0, 16, 12),
                    model="NIKON D500",
                )
            )
            photo = prepare_interactive_photo(NativeRawProcessor(), source)
            decoded = decode_nikon_34713_lossless(source)
            for exposure in (0.5, 2, 4):
                settings = {
                    "exposure": exposure,
                    "highlights": -1,
                    "contrast": 0.5,
                    "shadows": 0.3,
                }
                live = np.asarray(photo.render(settings)[0])[0, 0].astype(int)
                fast = render_decoded_nikon_34713_image(decoded, **settings)
                with patch(
                    "openraw_studio.raw.native.nikon._bayer_blocks_to_rgb8_numpy",
                    side_effect=lambda _np, *args, **kwargs: (
                        _bayer_blocks_to_rgb8_python(*args, **kwargs)
                    ),
                ):
                    python = render_decoded_nikon_34713_image(decoded, **settings)
                self.assertEqual(fast.rgb_bytes, python.rgb_bytes)
                for use_gpu in (False, True):
                    with (
                        nullcontext()
                        if use_gpu
                        else patch(
                            "openraw_studio.raw.native.acceleration.get_gpu",
                            return_value=None,
                        )
                    ):
                        full = render_decoded_nikon_34713_image(
                            decoded, quality="full", **settings
                        )
                    for image in (fast, full):
                        self.assertLessEqual(
                            np.abs(
                                live
                                - np.frombuffer(image.rgb_bytes, dtype=np.uint8)[:3]
                            ).max(),
                            1,
                        )
                        self.assertLess(max(image.rgb_bytes[:3]), 255)
