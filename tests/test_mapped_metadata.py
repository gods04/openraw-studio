import mmap
import struct
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures_nikon import embedded_jpeg_bytes, synthetic_nikon_nef_metadata_bytes
from test_tiff_tag_values import tag_container

from openraw_studio.raw.native import dng


class MappedMetadataTests(unittest.TestCase):
    def test_metadata_uses_readonly_mapping_and_owns_values_after_close(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nef"
            payload = bytes(range(256)) * 16
            for endian in ("<", ">"):
                original = tag_container(37500, 7, len(payload), payload, endian)
                source.write_bytes(original)
                mappings = []
                constructor = mmap.mmap

                def mapped(*args, **kwargs):
                    self.assertEqual(kwargs["access"], mmap.ACCESS_READ)
                    value = constructor(*args, **kwargs)
                    with self.assertRaises(TypeError):
                        value[0] = 0
                    mappings.append(value)
                    return value

                with patch.object(dng.mmap, "mmap", side_effect=mapped):
                    result = dng.DngMetadataReader().read(source)
                self.assertTrue(mappings[0].closed)
                self.assertEqual(result.ifds[0].tags[37500].value, payload)
                self.assertIsInstance(result.ifds[0].tags[37500].value, bytes)
                self.assertEqual(source.read_bytes(), original)
                renamed = source.with_suffix(".moved")
                source.rename(renamed)
                self.assertEqual(result.ifds[0].tags[37500].value, payload)
                renamed.unlink()

    def test_sparse_metadata_does_not_allocate_or_read_the_sensor_body(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "large.nef"
            with source.open("wb") as handle:
                handle.write(synthetic_nikon_nef_metadata_bytes())
                handle.seek(16 * 1024 * 1024 - 1)
                handle.write(b"\x00")
            with patch.object(Path, "read_bytes", side_effect=AssertionError("Whole-file copy")):
                tracemalloc.start()
                try:
                    metadata = dng.DngMetadataReader().read(source)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
            self.assertEqual(metadata.summary["model"], "NIKON Z 6II")
            self.assertLess(peak, 4 * 1024 * 1024)

    def test_preview_retains_jpeg_and_orientation_after_mapping_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nef"
            jpeg = embedded_jpeg_bytes()
            reader = dng.DngMetadataReader()
            for orientation in range(1, 9):
                original = synthetic_nikon_nef_metadata_bytes(
                    embedded_jpeg=jpeg, orientation=orientation,
                )
                source.write_bytes(original)
                with patch.object(Path, "read_bytes", side_effect=AssertionError("Whole-file copy")):
                    preview = reader.read_embedded_jpeg_preview(source)
                self.assertIsInstance(preview.data, bytes)
                self.assertEqual(preview.data, jpeg)
                self.assertEqual(preview.orientation, orientation)
                self.assertEqual(source.read_bytes(), original)
                source.unlink()
                self.assertEqual(preview.data, jpeg)

    def test_mapping_failure_preserves_metadata_preview_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nef"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes(), orientation=8))
            reader = dng.DngMetadataReader()
            expected = reader.read(source), reader.read_embedded_jpeg_preview(source)
            for error in (OSError("Unavailable"), ValueError("Unmappable"), OverflowError("Address space")):
                with patch.object(dng.mmap, "mmap", side_effect=error):
                    self.assertEqual((reader.read(source), reader.read_embedded_jpeg_preview(source)), expected)
            source.write_bytes(b"bad")
            with patch.object(dng.mmap, "mmap", side_effect=OSError("Unavailable")):
                with self.assertRaises(dng.DngMetadataError):
                    reader.read(source)

    def test_empty_and_malformed_files_keep_errors_and_release_handles(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nef"
            reader = dng.DngMetadataReader()
            for contents in (b"", b"II", b"II" + struct.pack("<HI", 42, 9999)):
                for method in (reader.read, reader.read_embedded_jpeg_preview):
                    source.write_bytes(contents)
                    with self.assertRaises(dng.DngMetadataError):
                        method(source)
                    source.unlink()
            for method in (reader.read, reader.read_embedded_jpeg_preview):
                with self.assertRaises(FileNotFoundError):
                    method(source)

    def test_replaced_file_is_read_fresh_without_retained_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nef"
            reader = dng.DngMetadataReader()
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes(), orientation=1))
            first = reader.read_embedded_jpeg_preview(source)
            replacement = source.with_suffix(".new")
            replacement.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes(9, 6), orientation=6))
            replacement.replace(source)
            second = reader.read_embedded_jpeg_preview(source)
            self.assertEqual((first.orientation, second.orientation), (1, 6))
            self.assertNotEqual(first.data, second.data)
