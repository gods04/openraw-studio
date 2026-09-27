import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openraw_studio.core.files import atomic_output_path
from openraw_studio.raw.native.jpeg import write_jpeg
from openraw_studio.raw.native.tone import PreviewRgbImage


class AtomicOutputPathTests(unittest.TestCase):
    def test_publishes_complete_file_and_replaces_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "exports" / "photo.jpg"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"old")

            with atomic_output_path(destination) as temporary_path:
                self.assertNotEqual(temporary_path, destination)
                self.assertEqual(temporary_path.suffix, destination.suffix)
                temporary_path.write_bytes(b"complete-image")
                self.assertEqual(destination.read_bytes(), b"old")

            self.assertEqual(destination.read_bytes(), b"complete-image")
            self.assertEqual(tuple(destination.parent.glob(".*.tmp*")), ())

    def test_failure_preserves_existing_output_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "photo.tif"
            destination.write_bytes(b"known-good")

            with self.assertRaisesRegex(RuntimeError, "encoding stopped"):
                with atomic_output_path(destination) as temporary_path:
                    temporary_path.write_bytes(b"partial")
                    raise RuntimeError("encoding stopped")

            self.assertEqual(destination.read_bytes(), b"known-good")
            self.assertEqual(tuple(destination.parent.glob(".*.tmp*")), ())

    def test_missing_temporary_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "recipe.json"

            with self.assertRaisesRegex(FileNotFoundError, "did not create"):
                with atomic_output_path(destination):
                    pass

            self.assertFalse(destination.exists())

    def test_failed_jpeg_encode_does_not_replace_existing_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "photo.jpg"
            destination.write_bytes(b"known-good-jpeg")
            image = PreviewRgbImage(width=1, height=1, pixels=((10, 20, 30),), transfer="srgb")

            def fail_after_partial_write(_image: object, target: Path, **_options: object) -> None:
                Path(target).write_bytes(b"partial-jpeg")
                raise OSError("simulated encoder failure")

            with patch("PIL.Image.Image.save", new=fail_after_partial_write):
                with self.assertRaisesRegex(OSError, "simulated encoder failure"):
                    write_jpeg(image, destination)

            self.assertEqual(destination.read_bytes(), b"known-good-jpeg")
            self.assertEqual(tuple(destination.parent.glob(".*.tmp*")), ())


if __name__ == "__main__":
    unittest.main()
