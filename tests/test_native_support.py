import tempfile
import unittest
from pathlib import Path

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

    def test_non_dng_reports_unsupported_without_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(b"fake raw bytes")

            report = inspect_native_support(source)

        self.assertTrue(report.file_exists)
        self.assertFalse(report.can_inspect)
        self.assertFalse(report.can_render)
        self.assertEqual(report.status, "unsupported")
        self.assertIn("DNG files", report.reason)

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
        self.assertIsInstance(payload["details"], list)
        self.assertEqual(payload["metadata"]["width"], 4)


if __name__ == "__main__":
    unittest.main()
