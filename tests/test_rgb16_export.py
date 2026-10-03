from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import tifffile
from PIL import Image

from openraw_studio.core.domain import ImageRef
from openraw_studio.export.errors import ExportError
from openraw_studio.export.interfaces import ExportRequest
from openraw_studio.export.local import LocalImageExportEngine
from openraw_studio.export.metadata import build_tiff_info
from openraw_studio.raw.native.tiff import write_tiff_rgb16


def ramp():
    return np.arange(4095, dtype=np.uint16).reshape(15, 91, 3) * 16 + 7


class Rgb16ExportTests(unittest.TestCase):
    def test_rgb8_writers_reject_sixteen_bit_even_for_dark_samples(self):
        from openraw_studio.raw.native import PreviewRgbImage, write_png, write_jpeg, write_ppm, write_tiff_rgb8

        image = PreviewRgbImage(1, 1, ((20, 30, 40),), "gamma-2.2", bit_depth=16)
        for writer, suffix in ((write_png, ".png"), (write_jpeg, ".jpg"), (write_ppm, ".ppm"), (write_tiff_rgb8, ".tif")):
            path = self.tmp_path / ("invalid" + suffix)
            with self.subTest(writer=writer.__name__), self.assertRaisesRegex(ValueError, "8-bit"):
                writer(image, path)
            self.assertFalse(path.exists())

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp_path = Path(directory.name)

    def test_writer_roundtrips_real_precision_and_safe_tags(self):
        pixels = ramp()
        original = pixels.copy()
        recipe = {"source": {"metadata": {
            "camera_make": "NIKON", "camera_model": "NIKON D500", "iso": 11400,
            "exposure_time": [1, 160], "aperture": 4.5, "focal_length_mm": 31,
            "captured_at": "2026-09-30T12:34:56", "lens_model": "Test lens",
            "gps": "private", "serial_number": "private", "path": "private",
        }}}
        info = build_tiff_info(recipe)
        info[315] = "private artist"
        path = write_tiff_rgb16(pixels, self.tmp_path / "export.tif", tiffinfo=info)
        np.testing.assert_array_equal(tifffile.imread(path), pixels)
        np.testing.assert_array_equal(pixels, original)
        with tifffile.TiffFile(path) as opened:
            page = opened.pages[0]
            self.assertEqual(page.bitspersample, 16)
            self.assertIn(page.compression, (8, 32946))
            self.assertEqual(page.photometric, 2)
            self.assertEqual(page.tags[33434].value, (1, 160))
            self.assertEqual(page.tags[34855].value, 11400)
            self.assertEqual(page.tags[274].value, 1)
            self.assertEqual(page.tags[40961].value, 1)
            self.assertEqual(page.tags[42036].value, "Test lens")
            self.assertNotIn(34853, page.tags)
            self.assertNotIn(37500, page.tags)
            self.assertNotIn(315, page.tags)
        with Image.open(path) as display:
            self.assertEqual(display.size, (91, 15))
            self.assertEqual(display.mode, "RGB")
        self.assertGreater(len(np.unique(pixels)), 256)
        self.assertTrue(np.any(pixels % 257 != 0))

    def test_writer_rejects_invalid_pixels_before_overwriting(self):
        for name, pixels in (
            ("uint8", ramp().astype(np.uint8)),
            ("int16", ramp().astype(np.int16)),
            ("grayscale", ramp()[..., 0]),
            ("empty", ramp()[:0]),
            ("two_channels", ramp()[..., :2]),
        ):
            with self.subTest(pixels=name):
                path = self.tmp_path / "export.tif"
                path.write_bytes(b"existing")
                with self.assertRaises(ValueError):
                    write_tiff_rgb16(pixels, path)
                self.assertEqual(path.read_bytes(), b"existing")

    def test_writer_is_atomic_even_if_encoder_fails(self):
        path = self.tmp_path / "export.tif"
        path.write_bytes(b"existing")
        with patch.object(tifffile, "imwrite", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_tiff_rgb16(ramp(), path)
        self.assertEqual(path.read_bytes(), b"existing")
        self.assertEqual(list(self.tmp_path.iterdir()), [path])

    def test_export_copy_preserves_precision_and_passthrough_checks_depth(self):
        source = write_tiff_rgb16(ramp(), self.tmp_path / "source.tif")
        engine = LocalImageExportEngine()
        for output in (source, self.tmp_path / "copy.tif"):
            with self.subTest(output=output.name):
                request = ExportRequest(ImageRef(source, 91, 15, "sRGB", "base"), {}, output,
                                        format="tiff", bit_depth=16)
                result = engine.export(request)
                self.assertEqual(result.metadata["bit_depth"], 16)
                np.testing.assert_array_equal(tifffile.imread(output), ramp())
        fake = self.tmp_path / "fake.tif"
        Image.new("RGB", (91, 15)).save(fake)
        for output in (fake, self.tmp_path / "copy-fake.tif"):
            with self.subTest(output=output.name):
                with self.assertRaisesRegex(ExportError, "16-bit"):
                    engine.export(ExportRequest(ImageRef(fake, 91, 15, "sRGB", "base"), {}, output,
                                                format="tiff", bit_depth=16))
        self.assertFalse((self.tmp_path / "copy-fake.tif").exists())

    def test_export_detects_wrong_dimensions(self):
        path = write_tiff_rgb16(ramp(), self.tmp_path / "export.tif")
        with self.assertRaisesRegex(ExportError, "dimensions"):
            LocalImageExportEngine().export(ExportRequest(
                ImageRef(path, 1, 1, "sRGB", "base"), {}, path, format="tiff", bit_depth=16))

    def test_passthrough_cannot_label_sixteen_bit_pixels_as_eight_bit(self):
        path = write_tiff_rgb16(ramp(), self.tmp_path / "export.tif")
        before = path.read_bytes()
        with self.assertRaisesRegex(ExportError, "8-bit"):
            LocalImageExportEngine().export(ExportRequest(
                ImageRef(path, 91, 15, "sRGB", "base"), {}, path, format="tiff", bit_depth=8))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(path.with_name(path.name + ".recipe.json").exists())
