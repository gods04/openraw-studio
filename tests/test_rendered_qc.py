import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.core.domain import ImageRef
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import (
    LocalPhotoPipeline,
    _record_rendered_preview_qc,
)
from openraw_studio.qc.histogram import analyze_rgb_bytes
from openraw_studio.qc.rendered import analyze_rendered_image
from openraw_studio.raw.native.synthetic import write_synthetic_dng


class RenderedQualityTests(unittest.TestCase):
    def test_neutral_image_passes_clipping_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "neutral.png"
            Image.new("RGB", (20, 10), (80, 120, 160)).save(path)

            report = analyze_rendered_image(path)

        self.assertEqual(report.status, "pass")
        self.assertEqual(report.warnings, ())
        self.assertEqual((report.sample_width, report.sample_height), (20, 10))
        self.assertEqual(report.histogram.pixel_count, 200)

    def test_clipped_image_reports_highlight_and_shadow_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clipped.png"
            image = Image.new("RGB", (10, 10), (128, 128, 128))
            for column in range(2):
                for row in range(10):
                    image.putpixel((column, row), (255, 255, 255))
                    image.putpixel((column + 2, row), (0, 0, 0))
            image.save(path)

            report = analyze_rendered_image(path, warning_threshold=0.1)
            recipe_quality = report.as_recipe_dict()

        self.assertEqual(report.status, "warning")
        self.assertEqual(report.warnings, ("highlight_clipping", "shadow_clipping"))
        self.assertAlmostEqual(recipe_quality["highlight_clip_fraction"], 0.2)
        self.assertAlmostEqual(recipe_quality["shadow_clip_fraction"], 0.2)
        self.assertEqual(recipe_quality["scope"], "rendered-preview-rgb8")

    def test_large_image_is_bounded_before_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.png"
            Image.new("RGB", (1200, 600), (100, 100, 100)).save(path)

            report = analyze_rendered_image(path, max_dimension=300)

        self.assertEqual((report.sample_width, report.sample_height), (300, 150))
        self.assertEqual(report.histogram.pixel_count, 45_000)

    def test_invalid_options_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            analyze_rendered_image(Path("unused.png"), max_dimension=0)
        with self.assertRaises(ValueError):
            analyze_rendered_image(Path("unused.png"), warning_threshold=1.1)

    def test_vectorized_report_matches_reference_after_resizing_and_thresholds(self):
        values = np.random.default_rng(31).integers(0, 256, (80, 120, 3), dtype=np.uint8)
        values[:20, :30] = 0
        values[40:, 60:, 0] = 255

        def reference(payload, **options):
            with patch.dict("sys.modules", {"numpy": None}):
                return analyze_rgb_bytes(payload, **options)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            Image.fromarray(values).save(path)
            before = path.read_bytes()
            for threshold in (0, .01, .2, 1):
                actual = analyze_rendered_image(path, max_dimension=59, warning_threshold=threshold)
                with patch("openraw_studio.qc.rendered.analyze_rgb_bytes", side_effect=reference):
                    expected = analyze_rendered_image(path, max_dimension=59, warning_threshold=threshold)
                self.assertEqual(actual, expected)
                self.assertEqual(actual.as_recipe_dict(), expected.as_recipe_dict())
            self.assertEqual(path.read_bytes(), before)

    def test_local_pipeline_records_rendered_preview_qc_in_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_dng(root / "sample.DNG", width=16, height=12)

            result = LocalPhotoPipeline().process(PipelineRequest(source, root / "output", preview_only=True))
            quality = result.recipe["analysis"]["quality"]

        self.assertEqual(quality["scope"], "rendered-preview-rgb8")
        self.assertIn(quality["status"], {"pass", "warning"})
        self.assertGreater(quality["pixel_count"], 0)
        self.assertEqual(result.recipe["qc"]["status"], quality["status"])

    def test_embedded_camera_preview_is_not_reported_as_openraw_qc(self) -> None:
        recipe = {"analysis": {"quality": {}}}
        preview = ImageRef(
            Path("camera-preview.jpg"),
            width=100,
            height=80,
            color_space="embedded-jpeg",
            role="preview",
        )

        _record_rendered_preview_qc(recipe, preview)

        self.assertEqual(recipe["analysis"]["quality"]["scope"], "camera-embedded-preview")
        self.assertEqual(recipe["analysis"]["quality"]["status"], "not_run")
        self.assertEqual(recipe["qc"]["status"], "not_run")


if __name__ == "__main__":
    unittest.main()
