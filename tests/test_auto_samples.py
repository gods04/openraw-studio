import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes

from openraw_studio.decision.auto_adjust import (
    suggest_auto_adjustments_for_photo,
    suggest_auto_adjustments_from_preview,
)
from openraw_studio.raw.native.auto_samples import prepare_native_auto_samples
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.raw.native.malvar import STANDARD_BAYER
from openraw_studio.raw.native.nikon import (
    _render_crop,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)


class NativeAutoSampleTests(unittest.TestCase):
    def fixture(self, folder):
        source = Path(folder) / "sample.NEF"
        source.write_bytes(
            synthetic_nikon_nef_compressed_bytes(
                width=32,
                height=24,
                active_area=(2, 2, 26, 20),
                model="NIKON D500",
                white_balance_rb_levels=(1.8, 1.4, 1, 1),
                maker_black_levels=(32, 64, 48, 96),
            )
        )
        return source

    def check_pixels(self, decoded):
        samples = prepare_native_auto_samples(decoded)
        core = samples.core_size
        left, top, _, _ = _render_crop(decoded)
        for edits in (
            {},
            {
                "exposure": 1.3,
                "warmth": -0.4,
                "tint": 0.3,
                "contrast": 0.2,
                "highlights": -0.3,
                "shadows": 0.4,
                "saturation": 0.1,
            },
        ):
            full = render_decoded_nikon_34713_image(
                replace(decoded, orientation=1), quality="full", **edits
            )
            pixels = np.frombuffer(full.rgb_bytes, np.uint8).reshape(
                full.height, full.width, 3
            )
            expected = np.stack(
                [pixels[y : y + core, x : x + core] for x, y in samples.locations]
            ).reshape(-1, 3)
            np.testing.assert_array_equal(samples.render(edits), expected)
        self.assertEqual(left % 2, 0)
        self.assertEqual(top % 2, 0)

    def test_samples_match_export_on_cpu_for_layouts_crops_edges_and_tiny_images(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=None),
        ):
            decoded = decode_nikon_34713_lossless(self.fixture(folder))
            for width, height, crop in (
                (2, 2, None),
                (8, 6, None),
                (78, 70, (3, 5, 69, 61)),
                (140, 102, (2, 4, 132, 94)),
            ):
                raw = (
                    np.random.default_rng(33)
                    .integers(0, 17000, (height, width), dtype="<u2")
                    .tobytes()
                )
                for cfa in STANDARD_BAYER:
                    value = replace(
                        decoded,
                        width=width,
                        height=height,
                        raw_bytes=raw,
                        cfa_pattern=cfa,
                        orientation=6,
                        compression_setup=replace(
                            decoded.compression_setup, active_area=crop
                        ),
                    )
                    with self.subTest(size=(width, height), cfa=cfa):
                        self.check_pixels(value)
                        self.assertIs(value.raw_bytes, raw)
                        self.assertEqual(value.orientation, 6)

    def test_accelerated_samples_match_export(self):
        with tempfile.TemporaryDirectory() as folder:
            self.check_pixels(decode_nikon_34713_lossless(self.fixture(folder)))

    def test_bright_sites_are_included_and_memory_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            decoded = decode_nikon_34713_lossless(self.fixture(folder))
            raw = np.full((384, 512), 300, dtype="<u2")
            sites = ((103, 121), (298, 280), (79, 302), (404, 65))
            for x, y in sites:
                raw[y, x] = 15000
            data = raw.tobytes()
            value = replace(
                decoded,
                width=512,
                height=384,
                raw_bytes=data,
                compression_setup=replace(decoded.compression_setup, active_area=None),
            )
            samples = prepare_native_auto_samples(value)
            self.assertEqual(
                samples.locations, prepare_native_auto_samples(value).locations
            )
            for x, y in sites:
                self.assertTrue(
                    any(
                        sx <= x < sx + 32 and sy <= y < sy + 32
                        for sx, sy in samples.locations
                    )
                )
            self.assertLessEqual(len(samples.locations), 128)
            self.assertLessEqual(len(samples.decoded.raw_bytes), 128 * 36 * 36 * 2)
            self.assertNotEqual(samples.decoded.raw_bytes, data)
            self.assertEqual(value.raw_bytes, data)

    def test_interactive_preparation_attaches_samples_and_reuses_them_when_resized(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            source = self.fixture(folder)
            before = source.read_bytes()
            processor = NativeRawProcessor()
            photo = prepare_interactive_photo(processor, source)
            self.assertIsNotNone(photo.native_samples)
            self.assertIs(photo.resized(4).native_samples, photo.native_samples)
            with patch.object(
                type(processor),
                "_decode_supported_nikon_34713",
                side_effect=AssertionError("Decode during Auto"),
            ):
                result = suggest_auto_adjustments_for_photo(photo)
            self.assertIn("native_validation_pixels", result.metrics)
            self.assertEqual(source.read_bytes(), before)

    def test_native_guard_removes_contrast_that_proxy_smoothing_hides(self):
        proxy = np.full((10, 10, 3), 100, np.uint8)
        native = proxy.copy()
        native[:2] = 25

        def render(values):
            result = native.copy()
            if values["contrast"] > 0:
                result[:2] = 0
            return result

        result = suggest_auto_adjustments_from_preview(
            proxy,
            render=lambda _: proxy,
            native_preview=native,
            render_native=render,
        )
        self.assertEqual(result.contrast, 0)
        self.assertEqual(result.metrics["native_new_shadow_clipping_fraction"], 0)
        self.assertEqual(result.metrics["contrast_guarded"], 1)

    def test_native_guard_checks_intermediate_strength_and_does_not_set_scene_median(
        self,
    ):
        proxy = np.full((10, 10, 3), 60, np.uint8)
        native = np.full((10, 10, 3), 200, np.uint8)

        def render(values):
            return np.full_like(
                native,
                255
                if values["exposure"] > 0.2 and values["highlights"] > -0.25
                else 170,
            )

        result = suggest_auto_adjustments_from_preview(
            proxy,
            render=lambda _: proxy,
            native_preview=native,
            render_native=render,
            validation_strengths=(0.25, 0.5, 0.7),
        )
        self.assertEqual(result.scene, "Balanced")
        self.assertGreater(result.exposure, 0)
        for strength in (0.25, 0.5, 0.7, 1):
            self.assertLess(
                render(
                    {k: v * strength for k, v in result.as_overrides().items()}
                ).max(),
                254,
            )

    def test_shadow_refinement_rechecks_native_clipping(self):
        from test_auto_adjust import AutoAdjustTests

        pixels, render = AutoAdjustTests.shadow_recovery_scene()
        _, unsafe = AutoAdjustTests.shadow_recovery_scene(clip_shadows=True)
        result = suggest_auto_adjustments_from_preview(
            pixels,
            render=render,
            native_preview=pixels,
            render_native=unsafe,
        )
        self.assertEqual(result.metrics["shadows_refined"], 0)

    def test_native_validation_requires_baseline_renderer_and_matching_shape(self):
        pixels = np.full((4, 4, 3), 100, np.uint8)
        for options in (
            {"native_preview": pixels},
            {"render_native": lambda _: pixels},
            {
                "native_preview": pixels,
                "render_native": lambda _: pixels,
                "render": None,
            },
        ):
            with self.assertRaisesRegex(ValueError, "Native validation requires"):
                suggest_auto_adjustments_from_preview(
                    pixels, **{"render": lambda _: pixels, **options}
                )
        with self.assertRaisesRegex(ValueError, "baseline preview dimensions"):
            suggest_auto_adjustments_from_preview(
                pixels,
                render=lambda _: pixels,
                native_preview=pixels,
                render_native=lambda _: np.zeros((3, 3, 3)),
            )
