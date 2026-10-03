from array import array
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from fixtures_nikon import _tiff_makernote_bytes, synthetic_nikon_nef_metadata_bytes
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native import compiled_decode
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.raw.native.nikon import NikonCompressionError, decode_nikon_34713_lossless
from openraw_studio.raw.native.support import inspect_native_support


WORDS12 = {
    5: "00", 4: "010", 3: "011", 6: "100", 2: "101", 7: "110", 1: "1110",
    0: "11110", 8: "111110", 9: "1111110", 11: "11111110", 10: "111111110",
    12: "1111111110",
}
WORDS14 = {
    5: "00", 6: "010", 4: "011", 7: "100", 8: "101", 3: "1100", 9: "1101",
    2: "1110", 1: "11110", 0: "111110", 10: "1111110", 11: "11111110",
    12: "111111110", 13: "1111111110", 14: "1111111111",
}


def d40_fixture(*, bits=12, byte_order="little", bad_index=None, table_change=None,
                payload_change=None, orientation=1):
    """Independent canonical words and evenly spaced synthetic curve knots."""
    endian = "<" if byte_order == "little" else ">"
    domain, white = 1 << (bits - 2), (1 << bits) - 1
    words = WORDS12 if bits == 12 else WORDS14
    indices = np.random.default_rng(bits).integers(0, domain, size=(8, 16))
    indices[0, :4] = [0, 1, domain - 2, domain - 1]
    if bad_index is not None:
        indices[2, 4] = bad_index
    vertical = [[64, 100], [200, 256]]
    encoded = []
    for row in range(8):
        horizontal = vertical[row % 2].copy()
        for col in range(16):
            value = int(indices[row, col])
            diff = value - horizontal[col % 2]
            category = abs(diff).bit_length()
            encoded.append(words[category])
            if category:
                encoded.append(format(diff if diff > 0 else diff + (1 << category) - 1, f"0{category}b"))
            horizontal[col % 2] = value
            if col < 2:
                vertical[row % 2][col] = value
    stream = "".join(encoded)
    stream += "0" * (-len(stream) % 8)
    payload = int(stream, 2).to_bytes(len(stream) // 8, "big")
    if payload_change:
        payload = payload_change(payload)
    knots = np.minimum(white, np.arange(257, dtype=np.int64) ** 2 * (white + 1) // 65536)
    table = bytearray(b"D@" + struct.pack(endian + "5H", 64, 200, 100, 256, 257))
    table.extend(struct.pack(endian + "257H", *knots))
    table.extend(bytes(624 - len(table)))
    if table_change:
        table = table_change(table)
    maker = _tiff_makernote_bytes([
        (0x0096, 7, len(table), bytes(table)),
        (0x0093, 3, 1, struct.pack(endian + "H", 4)),
        (0x003D, 3, 4, struct.pack(endian + "4H", *([1008] * 4))),
        (0x0045, 3, 4, struct.pack(endian + "4H", 2, 2, 12, 4)),
    ], byte_order=byte_order)
    source = synthetic_nikon_nef_metadata_bytes(
        width=16, height=8, bits_per_sample=bits, maker_note=maker,
        model="NIKON Z 5", compressed_sensor_payload=payload, orientation=orientation,
    )
    expected = np.interp(indices, np.linspace(0, domain, 257), knots).astype(np.uint16)
    return source, expected


class NikonD40Tests(unittest.TestCase):
    def test_both_bit_depths_byte_orders_and_decoder_paths_match(self):
        for bits in (12, 14):
            for order in ("little", "big"):
                with self.subTest(bits=bits, order=order), tempfile.TemporaryDirectory() as folder:
                    source = Path(folder) / "sample.NEF"
                    original, expected = d40_fixture(bits=bits, byte_order=order)
                    source.write_bytes(original)
                    self.assertTrue(inspect_native_support(source).can_render)
                    for fallback in (False, True):
                        with patch.object(compiled_decode, "decode", None if fallback else compiled_decode.decode):
                            decoded = decode_nikon_34713_lossless(source)
                        actual = array("H")
                        actual.frombytes(decoded.raw_bytes)
                        np.testing.assert_array_equal(actual, expected.ravel())
                        self.assertEqual(decoded.compression_setup.huffman_select, 0 if bits == 12 else 3)
                        self.assertEqual(decoded.compression_setup.initial_predictors, ((64, 100), (200, 256)))
                        self.assertEqual(len(decoded.compression_setup.linearization), 1 << (bits - 2))
                        self.assertEqual(decoded.black_levels, ((252 if bits == 12 else 1008),) * 4)
                        self.assertEqual(decoded.white_level, (1 << bits) - 1)
                        self.assertLess(decoded.compression_setup.linearization[-1], decoded.white_level)
                        self.assertEqual(decoded.storage_layout, "nikon-34713-lossy-strips")
                    self.assertEqual(source.read_bytes(), original)

    def test_curve_and_split_metadata_are_checked_before_editing(self):
        def set_u16(offset, value):
            def change(table):
                struct.pack_into("<H", table, offset, value)
                return table
            return change

        cases = (
            (lambda table: table[:563], "too short"),
            (set_u16(562, 4), "split-row"),
            (set_u16(10, 0), "knot count"),
            (set_u16(10, 1), "knot count"),
            (set_u16(10, 4), "knot count"),
            (set_u16(10, 258), "knot count"),
            (set_u16(20, 65000), "linearization curve"),
            (set_u16(524, 65535), "linearization curve"),
            (set_u16(2, 65535), "initial predictor"),
            (lambda table: b"D\x50" + table[2:], "table version"),
        )
        for bits in (12, 14):
            for change, reason in cases:
                with self.subTest(bits=bits, reason=reason), tempfile.TemporaryDirectory() as folder:
                    source = Path(folder) / "damaged.NEF"
                    source.write_bytes(d40_fixture(bits=bits, table_change=change)[0])
                    self.assertFalse(inspect_native_support(source).can_render)
                    with self.assertRaisesRegex(NikonCompressionError, reason):
                        decode_nikon_34713_lossless(source)

    def test_out_of_domain_predictors_are_rejected_in_both_paths(self):
        for bits in (12, 14):
            for value in (-1, 1 << (bits - 2)):
                for fallback in (False, True):
                    with self.subTest(bits=bits, value=value, fallback=fallback), tempfile.TemporaryDirectory() as folder:
                        source = Path(folder) / "bad-index.NEF"
                        source.write_bytes(d40_fixture(bits=bits, bad_index=value)[0])
                        with patch.object(compiled_decode, "decode", None if fallback else compiled_decode.decode):
                            with self.assertRaisesRegex(NikonCompressionError, "predictor.*range"):
                                decode_nikon_34713_lossless(source)

    def test_truncated_or_invalid_entropy_is_never_returned_as_pixels(self):
        for change in (lambda payload: payload[:8], lambda payload: payload[:-8], lambda payload: bytes(len(payload))):
            for fallback in (False, True):
                with self.subTest(fallback=fallback), tempfile.TemporaryDirectory() as folder:
                    source = Path(folder) / "truncated.NEF"
                    source.write_bytes(d40_fixture(payload_change=change)[0])
                    with patch.object(compiled_decode, "decode", None if fallback else compiled_decode.decode):
                        with self.assertRaises(NikonCompressionError):
                            decode_nikon_34713_lossless(source)

    def test_native_edit_crop_orientation_and_export_reuse_the_decode(self):
        for bits in (12, 14):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "portrait.NEF"
                original, _expected = d40_fixture(bits=bits, orientation=6)
                source.write_bytes(original)
                pipeline = LocalPhotoPipeline()
                photo = prepare_interactive_photo(pipeline.raw_processor, source, max_dimension=64)
                before, _ = photo.render({})
                edited, _ = photo.render({"exposure": 0.5})
                self.assertNotEqual(before.tobytes(), edited.tobytes())
                with patch("openraw_studio.raw.native.engine.decode_nikon_34713_lossless", side_effect=AssertionError("Decoded twice")):
                    for format_name in ("jpeg", "tiff"):
                        exported = pipeline.process(PipelineRequest(
                            source, root / "output", export_format=format_name, overrides={"exposure": 0.5},
                        ))
                        with Image.open(exported.exports[0].path) as image:
                            self.assertEqual(image.size, (4, 12))
                self.assertEqual(source.read_bytes(), original)
