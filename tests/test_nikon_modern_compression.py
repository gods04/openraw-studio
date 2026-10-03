import struct
import tempfile
import unittest
from pathlib import Path

from fixtures_nikon import (
    _tiff_makernote_bytes,
    embedded_jpeg_bytes,
    synthetic_nikon_nef_metadata_bytes,
)
from openraw_studio.raw.native.dng import DngMetadataReader, TiffIfd, TiffTag
from openraw_studio.raw.native.nikon import (
    NikonCompressionError,
    _compression_from_makernote,
    can_decode_nikon_34713_lossless,
    decode_nikon_34713_lossless,
    summarize_nikon_makernote_payload,
)
from openraw_studio.raw.native.support import inspect_native_support


def modern_note(mode, *, stale_table=False):
    record = b"01020501\0\0" + struct.pack("<H", mode) + bytes(12)
    entries = [(0x0051, 7, len(record), record)]
    if stale_table:
        entries.extend([
            (0x0093, 3, 1, struct.pack("<H", 3)),
            (0x0096, 7, 12, b"F0" + bytes(10)),
        ])
    return _tiff_makernote_bytes(entries)


class ModernNikonCompressionTests(unittest.TestCase):
    def test_modern_modes_are_reported_without_guessing_from_model(self):
        for mode, name in [(3, "Lossless"), (13, "High Efficiency"), (14, "High Efficiency*")]:
            with self.subTest(mode=mode):
                summary = summarize_nikon_makernote_payload(modern_note(mode))
                self.assertEqual(summary.compression_mode, mode)
                self.assertEqual(summary.compression_name, name)
                self.assertEqual(summary.compression_source, "0x0051+10")

    def test_byte_order_and_unknown_codes_are_preserved(self):
        for order in ("little", "big"):
            record = b"01020501\0\0" + (999).to_bytes(2, order)
            ifd = TiffIfd(0, {0x51: TiffTag(0x51, "", 7, 12, tuple(record))}, 0)
            self.assertEqual(_compression_from_makernote(ifd, order), (999, "0x0051+10"))

    def test_truncated_or_invalid_modern_record_does_not_invent_a_mode(self):
        for record in (b"01020501\0\0", b"invalid!\0\0\x0e\0"):
            ifd = TiffIfd(0, {0x51: TiffTag(0x51, "", 7, len(record), tuple(record))}, 0)
            self.assertEqual(_compression_from_makernote(ifd, "little"), (None, None))
            ifd.tags[0x93] = TiffTag(0x93, "", 3, 1, 3)
            self.assertEqual(_compression_from_makernote(ifd, "little"), (3, "0x0093"))

    def test_he_is_never_sent_to_huffman_even_with_stale_legacy_table(self):
        for mode in (13, 14):
            for jpeg in (None, embedded_jpeg_bytes()):
                with self.subTest(mode=mode, preview=jpeg is not None), tempfile.TemporaryDirectory() as temp:
                    source = Path(temp) / "modern.NEF"
                    original = synthetic_nikon_nef_metadata_bytes(
                        width=4, height=4, model="NIKON Z f",
                        maker_note=modern_note(mode, stale_table=True),
                        compressed_sensor_payload=bytes(32), embedded_jpeg=jpeg,
                    )
                    source.write_bytes(original)
                    metadata = DngMetadataReader().read(source)
                    self.assertFalse(can_decode_nikon_34713_lossless(metadata))
                    with self.assertRaisesRegex(NikonCompressionError, "High Efficiency"):
                        decode_nikon_34713_lossless(source, metadata)
                    support = inspect_native_support(source)
                    self.assertFalse(support.can_render)
                    self.assertEqual(support.can_preview, jpeg is not None)
                    self.assertIn("High Efficiency", support.reason)
                    self.assertIn("existing HE/HE* files", support.next_steps[1])
                    self.assertNotIn("Update Preview", " ".join(support.next_steps))
                    self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
