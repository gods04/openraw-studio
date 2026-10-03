from array import array
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from fixtures_nikon import (
    _tiff_makernote_bytes,
    synthetic_nikon_nef_compressed_bytes,
    synthetic_nikon_nef_metadata_bytes,
)
from openraw_studio.raw.native.nikon import (
    NikonCompressionError,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.profiles import find_camera_color_profile
from openraw_studio.raw.native.support import inspect_native_support


class NikonZ5Tests(unittest.TestCase):
    def test_lossless_black_units_crop_and_exact_sensor_values(self):
        for bits in (12, 14):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as folder:
                black = 252 if bits == 12 else 1008
                # Black samples must remain black, and 12-bit data must not be
                # crushed by subtracting the 14-bit MakerNote values directly.
                values = (black,) * 8 + (black + 500,) * 24
                original = synthetic_nikon_nef_compressed_bytes(
                    width=8, height=4, bits_per_sample=bits, samples=values,
                    active_area=(2, 0, 4, 4), maker_black_levels=(1008,) * 4,
                    model="NIKON Z 5",
                )
                source = Path(folder) / "z5.NEF"
                source.write_bytes(original)
                self.assertTrue(inspect_native_support(source).can_render)
                decoded = decode_nikon_34713_lossless(source)
                samples = array("H")
                samples.frombytes(decoded.raw_bytes)
                self.assertEqual(tuple(samples), values)
                self.assertEqual(decoded.black_levels, (black,) * 4)
                self.assertEqual(decoded.white_level, (1 << bits) - 1)
                self.assertEqual(decoded.camera_profile.model, "NIKON Z 5")
                rendered = render_decoded_nikon_34713_image(decoded, quality="full")
                self.assertEqual((rendered.width, rendered.height), (4, 4))
                self.assertGreater(max(rendered.rgb_bytes), 0)
                self.assertEqual(source.read_bytes(), original)

    def test_profile_d65_calibration_preserves_neutral_without_aliases(self):
        profile = find_camera_color_profile("nikon corporation", " Nikon Z 5 ")
        self.assertIsNotNone(profile)
        np.testing.assert_array_equal(profile.xyz_to_camera, (
            (0.8695, -0.2559, -0.0648), (-0.5015, 1.2710, 0.2575),
            (-0.1280, 0.2215, 0.7514),
        ))
        matrix = np.asarray(profile.camera_to_linear_srgb)
        self.assertTrue(np.isfinite(matrix).all())
        np.testing.assert_allclose(matrix @ np.ones(3), np.ones(3), atol=1e-12)
        for name in ("NIKON Z 5_2", "NIKON Z5", "NIKON Z 50"):
            self.assertIsNone(find_camera_color_profile("NIKON CORPORATION", name))

    def test_unknown_model_black_units_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "unknown.NEF"
            source.write_bytes(synthetic_nikon_nef_compressed_bytes(
                bits_per_sample=12, maker_black_levels=(1008,) * 4,
                model="NIKON Z 5_2",
            ))
            decoded = decode_nikon_34713_lossless(source)
            self.assertEqual(decoded.black_levels, (1008,) * 4)
            self.assertIsNone(decoded.camera_profile)

    def test_z5_d40_lossy_stays_blocked(self):
        for bits in (12, 14):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as folder:
                table = b"D@" + bytes(624)
                maker = _tiff_makernote_bytes([
                    (0x0096, 7, len(table), table),
                    (0x0093, 3, 1, struct.pack("<H", 4)),
                ])
                source = Path(folder) / "unverified.NEF"
                source.write_bytes(synthetic_nikon_nef_metadata_bytes(
                    bits_per_sample=bits, maker_note=maker, model="NIKON Z 5",
                    compressed_sensor_payload=b"\x00" * 32,
                ))
                self.assertFalse(inspect_native_support(source).can_render)
                with self.assertRaisesRegex(NikonCompressionError, "compression table version"):
                    decode_nikon_34713_lossless(source)
