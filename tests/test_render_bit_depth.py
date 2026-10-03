import tempfile
import unittest
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes
from PIL import Image

from openraw_studio.core.domain import ImageAsset
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawRenderRequest
from openraw_studio.raw.native import nikon
from openraw_studio.raw.native.demosaic import LinearRgbImage
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8
from openraw_studio.raw.native.preview import (
    render_png_preview,
    render_preview_image,
    resize_preview,
)
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.raw.native.tone import PreviewRgbImage, tone_map_preview


class RenderBitDepthTests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # Exercise real rendering on tiny fixtures without GPU or JIT startup.
        self.enterContext(patch.object(
            nikon, "render_bayer_full_resolution_rgb8",
            partial(render_bayer_full_resolution_rgb8, use_gpu=False, use_compiled=False),
        ))
        self.enterContext(patch(
            "openraw_studio.raw.native.compiled_decode.decode_samples", return_value=None,
        ))
        self.nikon_source = self.tmp_path / "sample.NEF"
        self.nikon_source.write_bytes(synthetic_nikon_nef_compressed_bytes(
            width=16, height=12, active_area=(2, 2, 12, 8), orientation=6,
            samples=tuple(map(int, np.random.default_rng(19).integers(200, 12000, 16 * 12))),
            maker_black_levels=(64, 64, 64, 64),
        ))

    def rgb16(self, rendered):
        self.assertEqual(rendered.bit_depth, 16)
        return np.frombuffer(rendered.rgb_bytes, "<u2").reshape(rendered.height, rendered.width, 3)

    def test_tone_encodes_directly_to_16_bits_and_keeps_8_bit_default(self):
        linear = LinearRgbImage(512, 1, tuple((i / 511,) * 3 for i in range(512)), "RGGB")
        eight = tone_map_preview(linear, gamma=1)
        sixteen = tone_map_preview(linear, gamma=1, bit_depth=16)
        self.assertEqual(eight.bit_depth, 8)
        self.assertEqual(sixteen.bit_depth, 16)
        self.assertEqual(eight.pixels, tuple((round(i / 511 * 255),) * 3 for i in range(512)))
        self.assertEqual(sixteen.pixels, tuple((round(i / 511 * 65535),) * 3 for i in range(512)))
        self.assertEqual(len(set(sixteen.pixels)), 512)
        self.assertTrue(np.any(np.asarray(sixteen.pixels) % 257))
        self.assertEqual(PreviewRgbImage(1, 1, ((1, 2, 3),), "test").bit_depth, 8)
        self.assertEqual(nikon.NikonRenderedRgbImage(1, 1, bytes([1, 2, 3])).bit_depth, 8)

    def test_noise_receives_uint16_without_precision_loss(self):
        for route in ("tone", "nikon"):
            with self.subTest(route=route), patch(
                "openraw_studio.raw.native.noise.reduce_noise",
                side_effect=lambda pixels, **kwargs: pixels.copy(),
            ) as noise:
                options = {"bit_depth": 16, "color_noise": .5, "luminance_noise": .4}
                if route == "tone":
                    linear = LinearRgbImage(2, 1, ((.1234, .3456, .5678), (.2345, .4567, .6789)), "RGGB")
                    actual = np.asarray(tone_map_preview(linear, **options).pixels).reshape(1, 2, 3)
                else:
                    decoded = replace(nikon.decode_nikon_34713_lossless(self.nikon_source), orientation=1)
                    actual = self.rgb16(nikon.render_decoded_nikon_34713_image(decoded, quality="full", **options))
                pixels = noise.call_args.args[0]
                self.assertEqual(pixels.dtype, np.uint16)
                self.assertGreater(pixels.max(), 255)
                self.assertTrue(np.any(pixels % 257))
                np.testing.assert_array_equal(actual, pixels)
                self.assertEqual(noise.call_args.kwargs, {"color_noise": .5, "luminance_noise": .4})

    def test_nikon_16_bit_orientation_and_region_match_full_image(self):
        decoded = replace(nikon.decode_nikon_34713_lossless(self.nikon_source), orientation=1)
        full = self.rgb16(nikon.render_decoded_nikon_34713_image(decoded, quality="full", bit_depth=16))
        planes = [Image.fromarray(full[:, :, channel]) for channel in range(3)]
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                operation = {
                    2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                    4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                    6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                    8: Image.Transpose.ROTATE_90,
                }.get(orientation)
                expected = np.stack([
                    np.asarray(plane.transpose(operation) if operation is not None else plane)
                    for plane in planes
                ], axis=2)
                oriented = replace(decoded, orientation=orientation)
                actual = nikon.render_decoded_nikon_34713_image(oriented, quality="full", bit_depth=16)
                np.testing.assert_array_equal(self.rgb16(actual), expected)
                for x, y, width, height in ((1, 2, 3, 4), (0, 0, 1, 1), (actual.width - 1, actual.height - 1, 1, 1)):
                    with self.subTest(region=(x, y, width, height)):
                        region = nikon.render_decoded_nikon_34713_image(
                            oriented, quality="full", bit_depth=16, region=(x, y, width, height),
                        )
                        np.testing.assert_array_equal(self.rgb16(region), expected[y:y + height, x:x + width])

    def test_nikon_resize_preserves_constant_low_bits_and_oriented_shape(self):
        decoded = nikon.decode_nikon_34713_lossless(self.nikon_source)
        full = nikon.NikonRenderedRgbImage(
            12, 8, np.full((8, 12, 3), [12345, 23456, 34567], dtype="<u2").tobytes(), bit_depth=16,
        )
        with patch.object(nikon, "render_bayer_full_resolution_rgb8", return_value=full) as render:
            resized = nikon.render_decoded_nikon_34713_image(decoded, quality="full", bit_depth=16, max_dimension=5)
            self.assertEqual((resized.width, resized.height), (3, 5))
            np.testing.assert_array_equal(self.rgb16(resized), np.full((5, 3, 3), [12345, 23456, 34567], np.uint16))
            self.assertEqual(render.call_args.kwargs["bit_depth"], 16)
            self.assertEqual(render.call_args.kwargs["crop"], (2, 2, 12, 8))
            with self.assertRaisesRegex(ValueError, "max_dimension"):
                nikon.render_decoded_nikon_34713_image(decoded, quality="full", bit_depth=16, max_dimension=0)

    def test_nikon_preview_uses_full_16_bit_render_and_preview_resize_preserves_depth(self):
        decoded = nikon.decode_nikon_34713_lossless(self.nikon_source)
        expected = nikon.render_decoded_nikon_34713_image(decoded, quality="full", bit_depth=16)
        preview = render_preview_image(self.nikon_source, bit_depth=16)
        self.assertEqual(preview.bit_depth, 16)
        np.testing.assert_array_equal(
            np.asarray(preview.pixels).reshape(expected.height, expected.width, 3), self.rgb16(expected),
        )
        resized = resize_preview(preview, max_dimension=4)
        self.assertEqual(resized.bit_depth, 16)
        self.assertEqual(resized.transfer, preview.transfer)
        self.assertTrue(all(pixel in preview.pixels for pixel in resized.pixels))
        self.assertEqual(max(resized.width, resized.height), 4)

    def test_engine_tiff_round_trip_resizes_and_preserves_source(self):
        dng_source = write_synthetic_dng(self.tmp_path / "sample.DNG", width=16, height=12)
        for kind, source in (("dng", dng_source), ("nikon", self.nikon_source)):
            for bit_depth in (8, 16):
                with self.subTest(kind=kind, bit_depth=bit_depth):
                    original = source.read_bytes()
                    output = self.tmp_path / f"render-{kind}-{bit_depth}.TIFF"
                    recipe = {"source": {"metadata": {"camera_make": "OpenRAW test", "orientation": 6}}}
                    options = {} if bit_depth == 8 else {"bit_depth": 16}
                    result = NativeRawProcessor().render_base(RawRenderRequest(
                        ImageAsset(path=source), recipe, output, max_dimension=5, **options,
                    ))
                    if kind == "nikon":
                        expected = nikon.render_decoded_nikon_34713_image(
                            nikon.decode_nikon_34713_lossless(source), quality="full", bit_depth=bit_depth, max_dimension=5,
                        )
                        pixels = np.frombuffer(
                            expected.rgb_bytes, np.uint8 if bit_depth == 8 else "<u2",
                        ).reshape(expected.height, expected.width, 3)
                    else:
                        expected = render_preview_image(source, bit_depth=bit_depth, max_dimension=5)
                        pixels = np.asarray(expected.pixels).reshape(expected.height, expected.width, 3)
                    with tifffile.TiffFile(output) as tiff:
                        page = tiff.pages[0]
                        self.assertEqual(page.bitspersample, bit_depth)
                        self.assertEqual(page.tags[271].value, "OpenRAW test")
                        self.assertEqual(page.tags[274].value, 1)
                        np.testing.assert_array_equal(page.asarray(), pixels)
                    self.assertEqual((result.width, result.height), (expected.width, expected.height))
                    self.assertEqual(max(result.width, result.height), 5)
                    if bit_depth == 16:
                        self.assertTrue(np.any(pixels % 257))
                    self.assertEqual(source.read_bytes(), original)

    def test_nikon_file_wrapper_forwards_16_bit_and_png_stays_8_bit(self):
        output = self.tmp_path / "direct.tif"
        size = nikon.render_nikon_34713_to_file(self.nikon_source, output, quality="full", bit_depth=16, max_dimension=5)
        self.assertEqual(tifffile.imread(output).dtype, np.uint16)
        self.assertEqual(size, (3, 5))
        for source in (self.nikon_source, write_synthetic_dng(self.tmp_path / "preview.DNG")):
            with self.subTest(source=source.name):
                preview = render_png_preview(source, self.tmp_path / "preview.png", max_dimension=4)
                self.assertEqual(preview.bit_depth, 8)
                with Image.open(self.tmp_path / "preview.png") as image:
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.size, (preview.width, preview.height))

    def test_invalid_bit_depth_is_rejected_before_rendering(self):
        for bit_depth in (0, 12, 32):
            with self.subTest(bit_depth=bit_depth):
                with self.assertRaisesRegex(ValueError, "8 or 16"):
                    tone_map_preview(None, bit_depth=bit_depth)
                with self.assertRaisesRegex(ValueError, "8 or 16"):
                    render_preview_image(Path("missing.DNG"), bit_depth=bit_depth)
                with self.assertRaisesRegex(ValueError, "8 or 16"):
                    nikon.render_decoded_nikon_34713_image(None, bit_depth=bit_depth)
                with self.assertRaisesRegex(ValueError, "8 or 16"):
                    nikon.render_decoded_nikon_34713_to_file(None, self.tmp_path / "bad.tif", bit_depth=bit_depth)
                with self.assertRaisesRegex(RawProcessingError, "8 or 16"):
                    NativeRawProcessor().render_base(RawRenderRequest(
                        ImageAsset(Path("missing.DNG")), {}, self.tmp_path / "bad.tif", bit_depth=bit_depth,
                    ))

    def test_16_bit_requires_full_quality_and_tiff_output(self):
        with self.assertRaisesRegex(ValueError, "quality='full'"):
            nikon.render_decoded_nikon_34713_image(None, bit_depth=16)
        for suffix in (".jpg", ".jpeg", ".png"):
            with self.subTest(suffix=suffix):
                destination = self.tmp_path / ("bad" + suffix)
                with self.assertRaisesRegex(ValueError, "TIFF"):
                    nikon.render_decoded_nikon_34713_to_file(None, destination, bit_depth=16, quality="full")
        with self.assertRaisesRegex(RawProcessingError, "requires TIFF"):
            NativeRawProcessor().render_base(RawRenderRequest(
                ImageAsset(Path("missing.DNG")), {}, self.tmp_path / "bad.jpg", bit_depth=16,
            ))


if __name__ == "__main__":
    unittest.main()
