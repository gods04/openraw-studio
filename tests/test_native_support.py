import struct
import tempfile
import unittest
from pathlib import Path

from openraw_studio.core.domain import ImageAsset
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
            source.write_bytes(_synthetic_nikon_nef_metadata_bytes())

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
        self.assertIn("Render: NEF/NRW decoding is future work", report.details)

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
            source.write_bytes(_synthetic_nikon_nef_metadata_bytes())

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
        self.assertIsInstance(payload["details"], list)
        self.assertEqual(payload["metadata"]["width"], 4)

def _synthetic_nikon_nef_metadata_bytes(width: int = 6048, height: int = 4024) -> bytes:
    ifd0_defs = [
        (256, 4, 1, struct.pack("<I", width)),
        (257, 4, 1, struct.pack("<I", height)),
        (258, 3, 1, struct.pack("<H", 14)),
        (259, 3, 1, struct.pack("<H", 34713)),
        (271, 2, len(b"NIKON CORPORATION\x00"), b"NIKON CORPORATION\x00"),
        (272, 2, len(b"NIKON Z 6II\x00"), b"NIKON Z 6II\x00"),
        (274, 3, 1, struct.pack("<H", 1)),
        (277, 3, 1, struct.pack("<H", 1)),
    ]
    exif_defs = [
        (33434, 5, 1, _pack_rational(1, 125)),
        (33437, 5, 1, _pack_rational(28, 10)),
        (34855, 3, 1, struct.pack("<H", 400)),
        (36867, 2, len(b"2026:08:28 12:34:56\x00"), b"2026:08:28 12:34:56\x00"),
        (37386, 5, 1, _pack_rational(50, 1)),
        (42036, 2, len(b"NIKKOR Z 50mm f/1.8 S\x00"), b"NIKKOR Z 50mm f/1.8 S\x00"),
    ]

    ifd0_count = len(ifd0_defs) + 1
    ifd0_size = 2 + ifd0_count * 12 + 4
    exif_offset = 8 + ifd0_size
    exif_size = 2 + len(exif_defs) * 12 + 4
    external_base = exif_offset + exif_size
    external_data = bytearray()

    def encode(tag: int, field_type: int, count: int, payload: bytes) -> bytes:
        type_size = {2: 1, 3: 2, 4: 4, 5: 8}[field_type]
        byte_count = type_size * count
        if byte_count <= 4:
            return struct.pack("<HHI", tag, field_type, count) + payload.ljust(4, b"\x00")
        offset = external_base + len(external_data)
        external_data.extend(payload)
        if len(external_data) % 2:
            external_data.extend(b"\x00")
        return struct.pack("<HHII", tag, field_type, count, offset)

    ifd0_entries = [encode(*entry) for entry in ifd0_defs]
    ifd0_entries.append(encode(34665, 4, 1, struct.pack("<I", exif_offset)))
    exif_entries = [encode(*entry) for entry in exif_defs]
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd0 = struct.pack("<H", ifd0_count) + b"".join(ifd0_entries) + struct.pack("<I", 0)
    exif = struct.pack("<H", len(exif_entries)) + b"".join(exif_entries) + struct.pack("<I", 0)
    return header + ifd0 + exif + bytes(external_data)


def _pack_rational(numerator: int, denominator: int) -> bytes:
    return struct.pack("<II", numerator, denominator)


if __name__ == "__main__":
    unittest.main()
