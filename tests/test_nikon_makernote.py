import tempfile
import unittest
from pathlib import Path

from fixtures_nikon import (
    nikon_makernote_bytes,
    synthetic_nikon_nef_compressed_bytes,
    synthetic_nikon_nef_metadata_bytes,
)
from openraw_studio.raw.native.dng import DngMetadataReader
from openraw_studio.raw.native.nikon import (
    decode_nikon_34713_lossless,
    extract_nikon_as_shot_white_balance,
    render_decoded_nikon_34713_image,
    summarize_nikon_makernote_payload,
)


class NikonMakerNoteTests(unittest.TestCase):
    def test_summarize_nikon_makernote_payload_extracts_compression_fields(self) -> None:
        summary = summarize_nikon_makernote_payload(nikon_makernote_bytes())

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.kind, "Nikon Type 2 MakerNote")
        self.assertEqual(summary.byte_order, "little")
        self.assertEqual(summary.tag_count, 6)
        self.assertEqual(summary.version, "0211")
        self.assertEqual(summary.crop_info, (12, 5600, 3728, 5600, 3728, 0, 0))
        self.assertEqual(summary.active_area, (16, 8, 5568, 3712))
        self.assertEqual(summary.compression_mode, 3)
        self.assertEqual(summary.curve_byte_count, 8)
        self.assertEqual(summary.curve_prefix, "I0")
        self.assertEqual(summary.compression_table_byte_count, 12)
        self.assertEqual(summary.compression_table_prefix, "F0")

    def test_summarize_nikon_makernote_payload_ignores_unknown_payloads(self) -> None:
        self.assertIsNone(summarize_nikon_makernote_payload(b"not a nikon makernote"))

    def test_extract_white_balance_prefers_standard_rb_levels(self) -> None:
        maker_note = nikon_makernote_bytes(
            white_balance_rb_levels=(2.25, 1.5, 1.0, 1.0),
            alternate_white_balance_rb_levels=(3.0, 1.25, 1.0, 1.0),
        )
        metadata = self._metadata_with_makernote(maker_note)

        white_balance = extract_nikon_as_shot_white_balance(metadata)
        summary = summarize_nikon_makernote_payload(maker_note)

        self.assertIsNotNone(white_balance)
        assert white_balance is not None
        self.assertEqual(white_balance.gains, (2.25, 1.0, 1.5))
        self.assertEqual(white_balance.source_tag, "0x000c")
        self.assertEqual(white_balance.mode, "AUTO1")
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.as_shot_white_balance, white_balance.gains)
        self.assertEqual(summary.white_balance_source, "0x000c")

    def test_extract_white_balance_does_not_use_multi_exposure_tag_as_camera_gain(self) -> None:
        maker_note = nikon_makernote_bytes(
            white_balance_rb_levels=(1.0, 1.0, 1.0, 1.0),
            alternate_white_balance_rb_levels=(3.30078125, 1.33935546875, 1.0, 1.0),
        )
        metadata = self._metadata_with_makernote(maker_note)

        white_balance = extract_nikon_as_shot_white_balance(metadata)

        summary = summarize_nikon_makernote_payload(maker_note)

        self.assertIsNone(white_balance)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIsNone(summary.as_shot_white_balance)
        self.assertIsNone(summary.white_balance_source)

    def test_extract_white_balance_rejects_unity_sentinels(self) -> None:
        metadata = self._metadata_with_makernote(
            nikon_makernote_bytes(
                white_balance_rb_levels=(1.0, 1.0, 1.0, 1.0),
                alternate_white_balance_rb_levels=(1.0, 1.0, 1.0, 1.0),
            )
        )

        self.assertIsNone(extract_nikon_as_shot_white_balance(metadata))

    def test_nikon_render_applies_as_shot_white_balance_without_modifying_source(self) -> None:
        samples = (4096,) * 16
        source_bytes = synthetic_nikon_nef_compressed_bytes(
            width=4,
            height=4,
            samples=samples,
            active_area=(0, 0, 4, 4),
            white_balance_rb_levels=(2.0, 1.5, 1.0, 1.0),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "white-balance.NEF"
            source.write_bytes(source_bytes)

            decoded = decode_nikon_34713_lossless(source)
            rendered = render_decoded_nikon_34713_image(decoded)

            first_pixel = tuple(rendered.rgb_bytes[:3])
            self.assertIsNotNone(decoded.white_balance)
            self.assertGreater(first_pixel[0], first_pixel[2])
            self.assertGreater(first_pixel[2], first_pixel[1])
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_nikon_decode_prefers_makernote_black_levels_and_loads_exact_camera_profile(self) -> None:
        source_bytes = synthetic_nikon_nef_compressed_bytes(
            width=4,
            height=4,
            samples=(1024,) * 16,
            active_area=(0, 0, 4, 4),
            maker_black_levels=(400, 400, 400, 400),
            model="NIKON D500",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "profile.NEF"
            source.write_bytes(source_bytes)

            decoded = decode_nikon_34713_lossless(source)

        self.assertEqual(decoded.black_level, 400)
        self.assertEqual(decoded.black_levels, (400, 400, 400, 400))
        self.assertIsNotNone(decoded.camera_profile)
        assert decoded.camera_profile is not None
        self.assertEqual(decoded.camera_profile.model, "NIKON D500")

    def test_d500_linear_color_pipeline_matches_validated_reference_pixel(self) -> None:
        source_bytes = synthetic_nikon_nef_compressed_bytes(
            width=2,
            height=2,
            samples=(2000, 1500, 1700, 1300),
            active_area=(0, 0, 2, 2),
            maker_black_levels=(400, 400, 400, 400),
            white_balance_rb_levels=(2.60595703125, 1.626953125, 1.0, 1.0),
            model="NIKON D500",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "d500-color.NEF"
            source.write_bytes(source_bytes)

            rendered = render_decoded_nikon_34713_image(decode_nikon_34713_lossless(source))

        self.assertEqual((rendered.width, rendered.height), (1, 1))
        self.assertEqual(tuple(rendered.rgb_bytes), (155, 59, 91))

    def test_nikon_render_applies_exif_orientation_before_resizing(self) -> None:
        source_bytes = synthetic_nikon_nef_compressed_bytes(
            width=4,
            height=2,
            samples=(1024,) * 8,
            active_area=(0, 0, 4, 2),
            orientation=8,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "orientation.NEF"
            source.write_bytes(source_bytes)
            decoded = decode_nikon_34713_lossless(source)

            rendered = render_decoded_nikon_34713_image(decoded)

        self.assertEqual((rendered.width, rendered.height), (1, 2))

    @staticmethod
    def _metadata_with_makernote(maker_note: bytes):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "metadata.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(maker_note=maker_note))
            return DngMetadataReader().read(source)


if __name__ == "__main__":
    unittest.main()
