import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fixtures_nikon import (
    _pack_rational_values,
    _tiff_makernote_bytes,
    synthetic_nikon_nef_metadata_bytes,
)
from PIL import Image
from test_nikon_he_entropy import encode_precinct, frame_stream, synthetic_header

from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native import compiled_he
from openraw_studio.raw.native.dng import DngMetadataReader
from openraw_studio.raw.native.he import HeFormatError, read_header
from openraw_studio.raw.native.he_transform import (
    _zf_linearization_curve,
    linearize_zf_he_bayer,
    reconstruct_nonlinear_bayer,
)
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.raw.native.nikon import (
    NikonCompressionError,
    can_decode_nikon_34713_lossless,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.nikon_he import ZF_HE_PICTURE_TAIL
from openraw_studio.raw.native.support import inspect_native_support


def he_fixture(*, model="NIKON Z f", mode=14, black=1008, payload_change=None, width=64, orientation=1):
    header = synthetic_header(width=64, height=72)
    previous, blocks = None, []
    for index in range(18):
        if index % 16 == 0:
            previous = None
        block, previous, _ = encode_precinct(header, previous, quantization=0, dc=(256, 0, 0, 0))
        blocks.append(block)
    stream = bytearray(frame_stream(blocks))
    picture = stream.index(b"\xff\x12") + 4
    stream[picture + 12:picture + 37] = ZF_HE_PICTURE_TAIL
    if payload_change:
        payload_change(stream, picture)
    record = b"01020501\0\0" + struct.pack("<H", mode)
    maker = _tiff_makernote_bytes([
        (0x0051, 7, len(record), record),
        (0x003D, 3, 4, struct.pack("<4H", *([black] * 4))),
        (0x0045, 3, 4, struct.pack("<4H", 2, 2, 60, 68)),
        (0x000C, 5, 4, _pack_rational_values((2.0, 1.5, 1.0, 1.0))),
    ])
    return synthetic_nikon_nef_metadata_bytes(
        width=width, height=72, bits_per_sample=14, model=model, maker_note=maker,
        compressed_sensor_payload=bytes(stream), orientation=orientation,
    )


class HeColorTests(unittest.TestCase):
    def test_color_lift_known_constant_and_channel_order(self):
        components = np.empty((4, 3, 4), np.int32)
        for plane, value in zip(components, (2048, 256, 32, 512)):
            plane[:] = value
        original = components.copy()
        bayer = reconstruct_nonlinear_bayer(components)
        # Greens are 2048 +/- 16 minus (256 + 512) / 4.
        np.testing.assert_array_equal(bayer, np.tile([[2112, 1840], [1872, 2368]], (3, 4)))
        np.testing.assert_array_equal(components, original)

    def test_color_lift_keeps_fractional_negative_values(self):
        components = np.zeros((4, 1, 1), np.int32)
        components[0] = -1000
        np.testing.assert_array_equal(reconstruct_nonlinear_bayer(components), np.full((2, 2), -1000))
        for shape in ((3, 4, 4), (4, 0, 4), (4, 4), (4, 4, 4, 1)):
            with self.assertRaises(HeFormatError):
                reconstruct_nonlinear_bayer(np.zeros(shape, np.int32))

    def test_spatial_lift_negative_rounding_and_bottom_boundary(self):
        components = np.arange(48, dtype=np.int32).reshape(4, 3, 4) * 7 - 99
        expected = [
            [-147, -170, -137, -169, -126, -170, -115, -168],
            [-94, 25, -87, 36, -80, 46, -72, 56],
            [-106, -159, -95, -159, -85, -159, -74, -157],
            [-62, 72, -55, 82, -49, 93, -41, 103],
            [-65, -159, -54, -159, -44, -159, -33, -157],
            [-41, 110, -35, 121, -27, 131, -20, 141],
        ]
        np.testing.assert_array_equal(reconstruct_nonlinear_bayer(components), expected)

    def test_nonlinear_curve_monotone_black_white_and_saturation(self):
        curve = _zf_linearization_curve()
        self.assertEqual(curve.dtype, np.uint16)
        self.assertFalse(curve.flags.writeable)
        self.assertEqual((int(curve[0]), int(curve[-1])), (0, 16383))
        self.assertTrue(np.all(np.diff(curve.astype(np.int32)) >= 0))
        self.assertEqual(int(curve[16256]), 1008)
        values = np.array([-100000, -32768, 16256-32768, 32767, 100000], np.int64)
        np.testing.assert_array_equal(linearize_zf_he_bayer(values), [0, 0, 1008, 16383, 16383])


class NativeHeTests(unittest.TestCase):
    def test_guarded_profile_decode_matches_python_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.NEF"
            original = he_fixture()
            source.write_bytes(original)
            metadata = DngMetadataReader().read(source)
            self.assertFalse(can_decode_nikon_34713_lossless(metadata))
            self.assertTrue(can_decode_nikon_34713_lossless(metadata, source))
            fast = decode_nikon_34713_lossless(source)
            with patch.object(compiled_he, "decode", None):
                slow = decode_nikon_34713_lossless(source)
            self.assertEqual(fast.raw_bytes, slow.raw_bytes)
            self.assertEqual(fast.storage_layout, "nikon-he-star-strips")
            self.assertEqual(fast.black_levels, (1008,) * 4)
            self.assertEqual(fast.white_level, 16383)
            self.assertEqual(fast.camera_profile.model, "NIKON Z F")
            self.assertEqual(fast.white_balance.gains, (2.0, 1.0, 1.5))
            self.assertEqual(fast.compression_setup.active_area, (2, 2, 60, 68))
            support = inspect_native_support(source)
            self.assertTrue(support.can_render)
            self.assertIn("approximation", " ".join(support.details))
            self.assertEqual(source.read_bytes(), original)

    def test_unknown_camera_mode_black_or_curve_never_claim_supported(self):
        for options in (
            {"model": "NIKON Z 5"}, {"model": "NIKON Z fc"}, {"mode": 13},
            {"black": 1024}, {"width": 72},
            {"payload_change": lambda data, picture: data.__setitem__(picture + 36, 0)},
            {"payload_change": lambda data, _picture: data.__setitem__(-1, 0)},
        ):
            with self.subTest(options=list(options)), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "unknown.NEF"
                source.write_bytes(he_fixture(**options))
                metadata = DngMetadataReader().read(source)
                self.assertFalse(can_decode_nikon_34713_lossless(metadata, source))
                self.assertFalse(inspect_native_support(source).can_render)
                with self.assertRaises(NikonCompressionError):
                    decode_nikon_34713_lossless(source)

    def test_raw_packet_or_depth_hint_mode_is_rejected_before_editing(self):
        for relative in (5, 12):
            def damage(stream, _picture, relative=relative):
                start = read_header(stream).precinct_start
                stream[start + relative] = 0x80

            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "other-coding.NEF"
                source.write_bytes(he_fixture(payload_change=damage))
                self.assertFalse(inspect_native_support(source).can_render)
                with self.assertRaises(NikonCompressionError):
                    decode_nikon_34713_lossless(source)

    def test_pipeline_preview_edit_and_export_reuse_native_decode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "sample.NEF"
            original = he_fixture()
            source.write_bytes(original)
            pipeline = LocalPhotoPipeline()
            photo = prepare_interactive_photo(pipeline.raw_processor, source, max_dimension=64)
            before, _ = photo.render({})
            after, _ = photo.render({"exposure": 0.5})
            self.assertNotEqual(before.tobytes(), after.tobytes())
            with patch("openraw_studio.raw.native.engine.decode_nikon_34713_lossless", side_effect=AssertionError("Decoded twice")):
                for format_name in ("jpeg", "tiff"):
                    exported = pipeline.process(PipelineRequest(
                        source, root / "output", export_format=format_name,
                        overrides={"exposure": 0.5},
                    ))
                    with Image.open(exported.exports[0].path) as image:
                        self.assertEqual(image.size, (60, 68))
            self.assertEqual(source.read_bytes(), original)

    def test_active_crop_and_orientation_are_applied_after_he_decode(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "portrait.NEF"
            source.write_bytes(he_fixture(orientation=6))
            decoded = decode_nikon_34713_lossless(source)
            rendered = render_decoded_nikon_34713_image(decoded, quality="full")
            self.assertEqual((rendered.width, rendered.height), (68, 60))


if __name__ == "__main__":
    unittest.main()
