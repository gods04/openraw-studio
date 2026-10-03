from array import array
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from fixtures_nikon import _tiff_makernote_bytes, synthetic_nikon_nef_metadata_bytes
from openraw_studio.raw.native import compiled_decode
from openraw_studio.raw.native.nikon import (
    NikonCompressionError,
    _nikon_compression_setup,
    decode_nikon_34713_lossless,
)
from openraw_studio.raw.native.dng import DngMetadataReader
from openraw_studio.raw.native.support import inspect_native_support


def d20_source(path: Path, *, split=0, knot_count=257, descending=False):
    # Explicit canonical bit words, independent of the decoder lookup builder.
    words = {
        5: "00",
        4: "010",
        3: "011",
        6: "100",
        2: "101",
        7: "110",
        1: "1110",
        0: "11110",
        8: "111110",
        9: "1111110",
        11: "11111110",
        10: "111111110",
        12: "1111111110",
    }
    width, height = 16, 8
    indices = np.random.default_rng(12).integers(0, 4096, size=(height, width))
    indices[0, :4] = [328, 329, 0, 4095]
    vertical = [[328, 328], [328, 328]]
    bits = []
    for row in range(height):
        horizontal = vertical[row % 2].copy()
        for col in range(width):
            value = int(indices[row, col])
            diff = value - horizontal[col % 2]
            length = abs(diff).bit_length()
            bits.append(words[length])
            if length:
                bits.append(
                    format(
                        diff if diff >= 0 else diff + (1 << length) - 1, f"0{length}b"
                    )
                )
            horizontal[col % 2] = value
            if col < 2:
                vertical[row % 2][col] = value
    stream = "".join(bits)
    stream += "0" * (-len(stream) % 8)
    payload = int(stream, 2).to_bytes(len(stream) // 8, "big")
    knots = [min(4095, i * i // 16) for i in range(257)]
    if descending:
        knots[3] = 4000
    table = bytearray(b"D " + struct.pack("<5H", 328, 328, 328, 328, knot_count))
    table.extend(struct.pack("<257H", *knots))
    table.extend(bytes(624 - len(table)))
    struct.pack_into("<H", table, 562, split)
    maker = _tiff_makernote_bytes(
        [
            (0x0096, 7, len(table), bytes(table)),
            (0x0093, 3, 1, struct.pack("<H", 4)),
            (0x003D, 3, 4, struct.pack("<4H", 800, 800, 800, 800)),
        ]
    )
    path.write_bytes(
        synthetic_nikon_nef_metadata_bytes(
            width=width,
            height=height,
            bits_per_sample=12,
            compressed_sensor_payload=payload,
            maker_note=maker,
            model="NIKON 1 J5",
        )
    )
    expected = tuple(
        (
            knots[int(i) // 16] * (16 - int(i) % 16)
            + knots[int(i) // 16 + 1] * (int(i) % 16)
        )
        // 16
        for i in indices.flat
    )
    return expected


class NikonD20Tests(unittest.TestCase):
    def test_d20_decodes_and_linearizes_with_compiled_and_python_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.NEF"
            expected = d20_source(source)
            original = source.read_bytes()
            for fallback in (False, True):
                with patch.object(
                    compiled_decode,
                    "decode",
                    None if fallback else compiled_decode.decode,
                ):
                    decoded = decode_nikon_34713_lossless(source)
                actual = array("H")
                actual.frombytes(decoded.raw_bytes)
                self.assertEqual(tuple(actual), expected)
                self.assertEqual(decoded.black_levels, (200,) * 4)
                self.assertEqual(decoded.camera_profile.model, "NIKON 1 J5")
                self.assertEqual(decoded.storage_layout, "nikon-34713-lossy-strips")
            self.assertTrue(inspect_native_support(source).can_render)
            self.assertEqual(source.read_bytes(), original)

    def test_d20_rejects_unsupported_or_corrupt_metadata(self):
        for options, reason in (
            ({"split": 5}, "split-row"),
            ({"knot_count": 1}, "knot count"),
            ({"knot_count": 500}, "knot count"),
            ({"descending": True}, "linearization curve"),
        ):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "unsupported.NEF"
                d20_source(source, **options)
                metadata = DngMetadataReader().read(source)
                with self.assertRaisesRegex(NikonCompressionError, reason):
                    _nikon_compression_setup(metadata, 12)
                self.assertFalse(inspect_native_support(source).can_render)
