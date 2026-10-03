import tempfile
import unittest
from pathlib import Path

import numpy as np

from fixtures_nikon import synthetic_nikon_nef_compressed_bytes

from openraw_studio.decision.auto_adjust import suggest_auto_adjustments, suggest_auto_adjustments_from_preview
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.raw.native.tone import PreviewRgbImage


class AutoAdjustTests(unittest.TestCase):
    def test_low_key_scene_keeps_its_intent(self) -> None:
        pixels = ((12, 12, 12),) * 90 + ((220, 220, 220),) * 10
        suggestion = suggest_auto_adjustments_from_preview(PreviewRgbImage(10, 10, pixels, "gamma-2.2"))
        self.assertEqual(suggestion.scene, "Low-key")
        self.assertLessEqual(suggestion.exposure, .35)
        self.assertLessEqual(suggestion.shadows, .10)

    def test_near_neutral_cast_and_grayscale(self) -> None:
        cool = PreviewRgbImage(4, 4, ((115, 120, 136),) * 16, "gamma-2.2")
        suggestion = suggest_auto_adjustments_from_preview(cool)
        self.assertGreater(suggestion.warmth, 0)
        gray = PreviewRgbImage(4, 4, ((128, 128, 128),) * 16, "gamma-2.2")
        neutral = suggest_auto_adjustments_from_preview(gray)
        self.assertEqual((neutral.warmth, neutral.tint, neutral.saturation), (0, 0, 0))

    def test_render_guard_reduces_a_clipping_candidate(self) -> None:
        preview = PreviewRgbImage(10, 10, ((50, 50, 50),) * 100, "gamma-2.2")
        renders = []
        def render(settings):
            renders.append(settings)
            return np.full((10, 10, 3), 255 if settings['exposure'] > .4 else 100, dtype=np.uint8)
        result = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertLessEqual(result.exposure, .4)
        self.assertLess(result.metrics["guard_strength"], 1)
        self.assertEqual(result.metrics["highlight_fraction_after"], 0)
        self.assertGreater(len(renders), 1)

    def test_balanced_input_is_deterministic_and_rejects_invalid_pixels(self) -> None:
        preview = PreviewRgbImage(2, 2, ((100, 110, 120),) * 4, "gamma-2.2")
        self.assertEqual(suggest_auto_adjustments_from_preview(preview), suggest_auto_adjustments_from_preview(preview))
        with self.assertRaises(ValueError):
            suggest_auto_adjustments_from_preview(PreviewRgbImage(0, 0, (), "gamma-2.2"))

    def test_dark_flat_preview_gets_lift_and_contrast(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((45, 45, 45), (52, 52, 52), (60, 60, 60), (66, 66, 66)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertGreater(suggestion.exposure, 0.0)
        self.assertGreater(suggestion.contrast, 0.0)
        self.assertGreater(suggestion.shadows, 0.0)
        self.assertIn("dark", " ".join(suggestion.rationale))

    def test_bright_preview_gets_exposure_reduction(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((220, 220, 220), (230, 230, 230), (240, 240, 240), (250, 250, 250)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertLess(suggestion.exposure, 0.0)
        self.assertLess(suggestion.highlights, 0.0)

    def test_blue_lighting_is_not_mistaken_for_a_white_balance_cast(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((90, 110, 155), (90, 110, 155), (90, 110, 155), (90, 110, 155)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertEqual(suggestion.warmth, 0.0)

    def test_sparse_bright_subject_keeps_night_background_dark(self) -> None:
        preview = PreviewRgbImage(100, 100, ((2, 2, 3),) * 9900 + ((180, 190, 200),) * 100, "gamma-2.2")
        suggestion = suggest_auto_adjustments_from_preview(preview)
        self.assertEqual(suggestion.scene, "Low-key")
        self.assertLessEqual(suggestion.exposure, .35)
        self.assertLessEqual(suggestion.shadows, .10)
        self.assertEqual(suggestion.contrast, 0)

    def test_saturated_green_scene_retains_its_color(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((90, 150, 100), (90, 150, 100), (90, 150, 100), (90, 150, 100)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertEqual(suggestion.tint, 0.0)

    def test_suggestion_exports_recipe_overrides(self) -> None:
        preview = PreviewRgbImage(width=1, height=1, pixels=((128, 128, 128),), transfer="gamma-1")

        overrides = suggest_auto_adjustments_from_preview(preview).as_overrides()

        self.assertEqual(set(overrides), {"exposure", "contrast", "highlights", "shadows", "warmth", "tint", "saturation"})

    def test_muted_preview_gets_saturation_lift(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((110, 112, 114), (118, 120, 122), (128, 130, 132), (138, 140, 142)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertGreater(suggestion.saturation, 0.0)

    def test_suggest_auto_adjustments_reads_synthetic_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=8, height=8)

            suggestion = suggest_auto_adjustments(source)

        self.assertLessEqual(abs(suggestion.exposure), 1.2)
        self.assertLessEqual(abs(suggestion.contrast), 0.22)
        self.assertLessEqual(abs(suggestion.highlights), 0.3)
        self.assertLessEqual(abs(suggestion.shadows), 0.28)
        self.assertLessEqual(abs(suggestion.warmth), 0.12)
        self.assertLessEqual(abs(suggestion.tint), 0.1)
        self.assertLessEqual(abs(suggestion.saturation), 0.14)

    def test_suggest_auto_adjustments_reads_nikon_34713_lossless_nef(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_compressed_bytes(width=8, height=8))

            suggestion = suggest_auto_adjustments(source)

        self.assertLessEqual(abs(suggestion.exposure), 1.2)
        self.assertLessEqual(abs(suggestion.contrast), 0.22)
        self.assertLessEqual(abs(suggestion.highlights), 0.3)
        self.assertLessEqual(abs(suggestion.shadows), 0.28)
        self.assertLessEqual(abs(suggestion.warmth), 0.12)
        self.assertLessEqual(abs(suggestion.tint), 0.1)
        self.assertLessEqual(abs(suggestion.saturation), 0.14)


if __name__ == "__main__":
    unittest.main()
