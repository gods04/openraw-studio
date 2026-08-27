import tempfile
import unittest
from pathlib import Path

from openraw_studio.decision.auto_adjust import suggest_auto_adjustments, suggest_auto_adjustments_from_preview
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.raw.native.tone import PreviewRgbImage


class AutoAdjustTests(unittest.TestCase):
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

    def test_cool_preview_gets_warmth(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((90, 110, 155), (90, 110, 155), (90, 110, 155), (90, 110, 155)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertGreater(suggestion.warmth, 0.0)

    def test_suggestion_exports_recipe_overrides(self) -> None:
        preview = PreviewRgbImage(width=1, height=1, pixels=((128, 128, 128),), transfer="gamma-1")

        overrides = suggest_auto_adjustments_from_preview(preview).as_overrides()

        self.assertEqual(set(overrides), {"exposure", "contrast", "warmth"})

    def test_suggest_auto_adjustments_reads_synthetic_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=8, height=8)

            suggestion = suggest_auto_adjustments(source)

        self.assertLessEqual(abs(suggestion.exposure), 0.6)
        self.assertLessEqual(abs(suggestion.contrast), 0.22)
        self.assertLessEqual(abs(suggestion.warmth), 0.12)


if __name__ == "__main__":
    unittest.main()
