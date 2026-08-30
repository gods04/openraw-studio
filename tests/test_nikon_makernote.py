import unittest

from fixtures_nikon import nikon_makernote_bytes
from openraw_studio.raw.native.nikon import summarize_nikon_makernote_payload


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
        self.assertEqual(summary.compression_table_byte_count, 6)
        self.assertEqual(summary.compression_table_prefix, "F0")

    def test_summarize_nikon_makernote_payload_ignores_unknown_payloads(self) -> None:
        self.assertIsNone(summarize_nikon_makernote_payload(b"not a nikon makernote"))


if __name__ == "__main__":
    unittest.main()
