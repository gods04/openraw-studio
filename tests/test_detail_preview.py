import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes
from PIL import Image

from openraw_studio.raw.native.detail import NikonDetailPhoto, prepare_detail_photo
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8
from openraw_studio.raw.native.nikon import (
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.regions import sensor_region
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.viewport import DetailView


class DetailPreviewTests(unittest.TestCase):
    def fixture(self, folder):
        source = Path(folder) / "sensor.NEF"
        source.write_bytes(synthetic_nikon_nef_compressed_bytes(
            width=32, height=24, active_area=(2, 2, 26, 20), model="NIKON D500",
            samples=tuple(map(int, np.random.default_rng(17).integers(128, 13000, 32 * 24))),
            white_balance_rb_levels=(1.8, 1.4, 1, 1), maker_black_levels=(32, 64, 48, 96),
        ))
        return source

    def check_regions(self, decoded):
        edits = {"exposure": .3, "contrast": .15, "warmth": -.2, "tint": .1,
                 "highlights": -.25, "shadows": .2, "saturation": .3}
        for orientation in range(1, 9):
            photo = NikonDetailPhoto(replace(decoded, orientation=orientation))
            full = render_decoded_nikon_34713_image(photo.decoded, quality="full", **edits)
            expected = Image.frombytes("RGB", (full.width, full.height), full.rgb_bytes)
            self.assertEqual(photo.size, expected.size)
            w, h = photo.size
            for region in ((0, 0, w, h), (3, 5, 7, 9), (0, 0, 1, 1), (w - 1, h - 1, 1, 1), (w - 5, 0, 5, h)):
                with self.subTest(orientation=orientation, region=region, cfa=decoded.cfa_pattern):
                    x, y, rw, rh = region
                    result = photo.render_region(edits, region)
                    np.testing.assert_array_equal(np.asarray(result), np.asarray(expected.crop((x, y, x + rw, y + rh))))

    def test_cpu_regions_match_full_export_for_every_orientation_cfa_and_border(self):
        with tempfile.TemporaryDirectory() as folder, patch("openraw_studio.raw.native.acceleration.get_gpu", return_value=None):
            decoded = decode_nikon_34713_lossless(self.fixture(folder))
            for cfa in ((0, 1, 1, 2), (1, 0, 2, 1), (1, 2, 0, 1), (2, 1, 1, 0)):
                self.check_regions(replace(decoded, cfa_pattern=cfa))

    def test_accelerated_regions_match_full_export(self):
        with tempfile.TemporaryDirectory() as folder:
            self.check_regions(decode_nikon_34713_lossless(self.fixture(folder)))

    def test_native_detail_reuses_decode_and_limits_render_to_region_plus_halo(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.fixture(folder)
            processor = NativeRawProcessor()
            photo = prepare_detail_photo(processor, source)
            with patch("openraw_studio.raw.native.engine.decode_nikon_34713_lossless", side_effect=AssertionError("Decoded twice")):
                self.assertIs(prepare_detail_photo(processor, source).decoded, photo.decoded)
            with patch("openraw_studio.raw.native.nikon.render_bayer_full_resolution_rgb8", wraps=render_bayer_full_resolution_rgb8) as render:
                result = photo.render_region({}, (5, 7, 3, 4))
            self.assertEqual(result.size, (3, 4))
            self.assertEqual(render.call_args.kwargs["crop"], (5, 7, 7, 8))
            self.assertEqual(render.call_args.kwargs["demosaic"], "malvar")

    def test_generic_dng_detail_matches_existing_full_resolution_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            source = write_synthetic_dng(Path(folder) / "sample.DNG", width=24, height=18)
            photo = prepare_detail_photo(NativeRawProcessor(), source)
            edits = {"exposure": .2, "shadows": .1, "saturation": .2}
            expected = render_preview_image(source, **edits)
            self.assertEqual(photo.size, (expected.width, expected.height))
            region = np.asarray(photo.render_region(edits, (3, 5, 7, 8)), dtype=int)
            pixels = np.asarray(expected.pixels).reshape(expected.height, expected.width, 3)
            self.assertLessEqual(np.abs(region - pixels[5:13, 3:10]).max(), 1)

    def test_invalid_regions_and_scaled_detail_requests_are_rejected(self):
        for region in ((-1, 0, 1, 1), (0, 0, 0, 1), (0, 0, 33, 10), (1.5, 0, 1, 1)):
            with self.assertRaises(ValueError):
                sensor_region(region, (32, 24), 1)
        with tempfile.TemporaryDirectory() as folder:
            decoded = decode_nikon_34713_lossless(self.fixture(folder))
            for options in ({"quality": "fast"}, {"quality": "full", "max_dimension": 100}):
                with self.assertRaisesRegex(ValueError, "unscaled full-resolution"):
                    render_decoded_nikon_34713_image(decoded, region=(0, 0, 3, 4), **options)

    def test_native_viewport_pixel_pitch_pan_bounds_and_small_photos(self):
        self.assertEqual(DetailView((801, 603)).region((6000, 4000)), (2600, 1698, 801, 603))
        view = DetailView((801, 603), 2)
        region = view.region((6000, 4000))
        self.assertEqual(region[2:], (401, 302))
        self.assertEqual(view.display_size(region), (801, 603))
        self.assertEqual(DetailView((801, 603), 1, (1e6, -1e6)).region((6000, 4000)), (0, 3397, 801, 603))
        self.assertEqual(view.region((12, 8)), (0, 0, 12, 8))
        self.assertEqual(view.display_size((0, 0, 12, 8)), (24, 16))
        self.assertEqual(DetailView((800, 600), anchor=(.1, .8)).region((6000, 4000)), (200, 2900, 800, 600))
        self.assertEqual(DetailView((800, 600), anchor=(0, 1)).region((6000, 4000)), (0, 3400, 800, 600))
        with self.assertRaises(ValueError):
            DetailView((0, 100)).region((6000, 4000))
