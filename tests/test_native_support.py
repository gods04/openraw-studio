import tempfile
import unittest
from pathlib import Path

from fixtures_nikon import embedded_jpeg_bytes, synthetic_nikon_nef_metadata_bytes, synthetic_nikon_nef_sensor_bytes
from openraw_studio.core.domain import ImageAsset
from openraw_studio.core.image_info import read_image_size
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.support import inspect_native_support
from openraw_studio.raw.native.synthetic import write_synthetic_dng


class NativeSupportTests(unittest.TestCase):
    def test_supported_synthetic_dng_reports_renderable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=8, height=8)

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertTrue(report.can_inspect)
        self.assertTrue(report.can_render)
        self.assertEqual(report.status, "supported")
        self.assertEqual(report.reason, "Supported by OpenRAW Native V0.1.")
        self.assertEqual(report.metadata["width"], 8)
        self.assertIn("Storage: 1 strip", report.details)
        self.assertIn("CFA: RGGB", report.details)

    def test_other_raw_reports_unsupported_without_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.CR2"
            source.write_bytes(b"fake raw bytes")

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertFalse(report.can_inspect)
        self.assertFalse(report.can_render)
        self.assertEqual(report.status, "unsupported")
        self.assertIn("Nikon RAW metadata", report.reason)

    def test_nikon_nef_reports_importable_metadata_without_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes())

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertTrue(report.can_inspect)
        self.assertFalse(report.can_render)
        self.assertEqual(report.status, "import_only")
        self.assertEqual(report.metadata["make"], "NIKON CORPORATION")
        self.assertEqual(report.metadata["model"], "NIKON Z 6II")
        self.assertEqual(report.metadata["iso"], 400)
        self.assertEqual(report.metadata["exposure_time"], 0.008)
        self.assertEqual(report.metadata["aperture"], 2.8)
        self.assertIn("Format: Nikon NEF/TIFF", report.details)
        self.assertIn("Camera: NIKON CORPORATION NIKON Z 6II", report.details)
        self.assertIn(
            "Render: Unsupported Nikon RAW compression: 34713; only uncompressed TIFF-style sensor data is supported.",
            report.details,
        )
        self.assertIn("Render blocker: No complete strip or tile pixel payload was found.", report.details)
        self.assertIn("Create Sample DNG/NEF", report.next_steps[0])
        self.assertIn("Nikon compression value 34713", report.next_steps[1])

    def test_nikon_nef_reports_preview_only_when_embedded_jpeg_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            embedded_jpeg = embedded_jpeg_bytes()
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg))

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertTrue(report.can_inspect)
        self.assertTrue(report.can_preview)
        self.assertFalse(report.can_render)
        self.assertEqual(report.status, "preview_only")
        self.assertEqual(report.metadata["jpeg_interchange_format_length"], len(embedded_jpeg))
        self.assertIn("Preview: embedded JPEG", "\n".join(report.details))
        self.assertIn("final export is blocked", report.reason)
        self.assertIn("Use Update Preview", report.next_steps[0])
        self.assertIn("Nikon compression value 34713", report.next_steps[1])

    def test_nikon_nef_reports_renderable_when_sensor_payload_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_sensor_bytes())

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertTrue(report.can_inspect)
        self.assertTrue(report.can_preview)
        self.assertTrue(report.can_render)
        self.assertEqual(report.status, "supported")
        self.assertEqual(report.reason, "Supported by OpenRAW Native V0.1 guarded Nikon sensor decode.")
        self.assertIn("Storage: 1 strip", report.details)
        self.assertIn("Render: native TIFF-style sensor decode", report.details)

    def test_nikon_nef_reports_renderable_for_14_bit_packed_sensor_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample-14bit.NEF"
            source.write_bytes(synthetic_nikon_nef_sensor_bytes(bits_per_sample=14))

            report = inspect_native_support(source)

        self.assertTrue(report.can_preview)
        self.assertTrue(report.can_render)
        self.assertEqual(report.status, "supported")
        self.assertIn("Bit depth: 14-bit", report.details)
        self.assertIn("Storage: 1 strip", report.details)

    def test_native_processor_writes_nikon_embedded_jpeg_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.NEF"
            preview_path = root / "preview.jpg"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes()))

            preview = NativeRawProcessor().create_preview(ImageAsset(source), preview_path, max_dimension=2048)

            self.assertEqual(preview.path, preview_path)
            self.assertEqual(preview.width, 3)
            self.assertEqual(preview.height, 2)
            self.assertEqual(preview.color_space, "embedded-jpeg")
            self.assertEqual(preview.role, "preview")
            self.assertEqual(read_image_size(preview_path), (3, 2))
            self.assertTrue(preview_path.read_bytes().startswith(b"\xff\xd8"))

    def test_invalid_nikon_nef_reports_metadata_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "broken.NEF"
            source.write_bytes(b"fake raw bytes")

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertFalse(report.can_inspect)
        self.assertFalse(report.can_render)
        self.assertIn("Nikon RAW metadata could not be read", report.reason)

    def test_native_processor_inspects_nikon_nef_metadata_for_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes())

            inspection = NativeRawProcessor().inspect(ImageAsset(source))

        self.assertEqual(inspection.metadata.camera_make, "NIKON CORPORATION")
        self.assertEqual(inspection.metadata.camera_model, "NIKON Z 6II")
        self.assertEqual(inspection.metadata.iso, 400)
        self.assertEqual(inspection.metadata.aperture, 2.8)
        self.assertEqual(inspection.metadata.focal_length_mm, 50.0)
        self.assertEqual(inspection.metadata.raw["raw_format"], "nikon-nef")
        self.assertEqual(inspection.metadata.raw["raw_container"], "tiff")

    def test_missing_file_reports_missing(self) -> None:
        report = inspect_native_support(Path("missing.DNG"))

        self.assertFalse(report.file_exists)
        self.assertFalse(report.can_render)
        self.assertEqual(report.status, "missing")

    def test_invalid_dng_reports_metadata_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "broken.DNG"
            source.write_bytes(b"not a tiff")

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertFalse(report.can_render)
        self.assertIn("DNG metadata could not be read", report.reason)

    def test_report_as_dict_is_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=4, height=4)

            payload = inspect_native_support(source).as_dict()

        self.assertEqual(payload["status"], "supported")
        self.assertTrue(payload["can_preview"])
        self.assertIsInstance(payload["details"], list)
        self.assertIsInstance(payload["next_steps"], list)
        self.assertEqual(payload["metadata"]["width"], 4)


if __name__ == "__main__":
    unittest.main()
