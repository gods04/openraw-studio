import importlib.util
import sys
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures_nikon import (
    embedded_jpeg_bytes,
    nikon_makernote_bytes,
    synthetic_nikon_nef_metadata_bytes,
)
from PIL import Image


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhotoCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_module = sys.modules.get("validate_photo_set")
        sys.modules["validate_photo_set"] = load_script("validate_photo_set")
        cls.catalog = load_script("catalog_raw_samples")

    @classmethod
    def tearDownClass(cls):
        if cls.previous_module is None:
            sys.modules.pop("validate_photo_set", None)
        else:
            sys.modules["validate_photo_set"] = cls.previous_module

    def test_inventory_and_thumbnail_leave_source_untouched(self):
        original = synthetic_nikon_nef_metadata_bytes(
            model="NIKON D500", maker_note=nikon_makernote_bytes(),
            embedded_jpeg=embedded_jpeg_bytes(30, 20), orientation=8,
        )
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "photo.NEF"
            source.write_bytes(original)
            record = self.catalog.read_record(source)
            self.assertEqual(record["model"], "NIKON D500")
            self.assertEqual(record["nikon_compression"], "Lossless")
            self.assertEqual(self.catalog.preview_image(record).size, (20, 30))
            self.assertEqual(source.read_bytes(), original)

    def test_raw_orientation_is_not_applied_twice(self):
        jpeg = BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (30, 20), "red").save(jpeg, format="JPEG", exif=exif)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "rotated.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(
                embedded_jpeg=jpeg.getvalue(), orientation=6,
            ))
            image = self.catalog.preview_image(self.catalog.read_record(source))
            self.assertEqual(image.size, (20, 30))

    def test_invalid_container_is_rejected_without_a_partial_record(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "broken.NEF"
            source.write_bytes(b"bad")
            with self.assertRaises(ValueError):
                self.catalog.read_record(source)

    def test_comparison_report_escapes_private_labels_and_errors(self):
        report = sys.modules["validate_photo_set"].comparison_report_html([
            {"source": "photo.NEF", "case": "<script>alert(1)</script>", "ok": True},
            {"source": "failed.NEF", "error": "<bad>", "ok": False},
        ])
        self.assertNotIn("<script>", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("&lt;bad&gt;", report)
        self.assertIn('photo-00/auto.jpg', report)
        self.assertNotIn('photo-01/auto.jpg', report)

    def test_auto_validation_uses_desktop_linear_proxy_not_display_thumbnail(self):
        validation = sys.modules["validate_photo_set"]
        display = SimpleNamespace(
            render=lambda _: (Image.new("RGB", (40, 30), (200, 200, 200)), "CPU"),
            pixels=SimpleNamespace(shape=(30, 40, 3)),
        )
        analysis = SimpleNamespace(render=lambda _: (Image.new("RGB", (4, 3), (100, 100, 100)), "CPU"))
        analysis.pixels = SimpleNamespace(shape=(3, 4, 3))
        display.resized = lambda dimension: analysis
        with tempfile.TemporaryDirectory() as folder, patch.object(
            validation, "prepare_interactive_photo", return_value=display
        ) as prepare, patch("sys.stdout", new_callable=StringIO):
            source = Path(folder) / "photo.NEF"
            source.write_bytes(b"synthetic harness input")
            record = validation.validate([source], Path(folder), False)[0]
        self.assertTrue(record["ok"])
        self.assertTrue(record["source_unchanged"])
        self.assertEqual([call.kwargs["max_dimension"] for call in prepare.call_args_list], [960])
        self.assertEqual(record["detail_size"], [40, 30])
        self.assertAlmostEqual(record["metrics"]["median_luma"], 100 / 255, places=5)
        self.assertAlmostEqual(record["metrics"]["detail_median_luma"], 200 / 255, places=5)


if __name__ == "__main__":
    unittest.main()
