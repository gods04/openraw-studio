import json
import math
import mmap
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures_nikon import synthetic_nikon_nef_compressed_bytes

from openraw_studio.raw.native import dng, nikon, support


def tag_container(tag, field_type, count, payload, endian="<"):
    header = (b"II" if endian == "<" else b"MM") + struct.pack(endian + "HI", 42, 8)
    value = payload.ljust(4, b"\x00") if len(payload) <= 4 else struct.pack(endian + "I", 26)
    entry = struct.pack(endian + "HHI", tag, field_type, count) + value
    return header + struct.pack(endian + "H", 1) + entry + bytes(4) + (payload if len(payload) > 4 else b"")


def read_value(field_type, payload, count, endian="<"):
    value = payload.ljust(4, b"\x00") if len(payload) <= 4 else struct.pack(endian + "I", 8)
    return dng._read_tag_value(bytes(8) + payload, endian, field_type, count, value)


class TiffTagValueTests(unittest.TestCase):
    def test_numeric_types_match_per_element_reference_for_both_byte_orders(self):
        cases = {
            1: ("B", (0, 127, 255)), 3: ("H", (0, 32768, 65535)),
            4: ("I", (0, 2147483648, 4294967295)), 6: ("b", (-128, 0, 127)),
            8: ("h", (-32768, 0, 32767)), 9: ("i", (-2147483648, 0, 2147483647)),
            11: ("f", (-.25, 1.5, 1024.25)), 12: ("d", (-1e100, .25, 1e100)),
        }
        for endian in ("<", ">"):
            for field_type, (fmt, values) in cases.items():
                for count in (0, 1, 3):
                    with self.subTest(endian=endian, field_type=field_type, count=count):
                        payload = struct.pack(endian + str(count) + fmt, *values[:count])
                        size = struct.calcsize(fmt)
                        expected = tuple(struct.unpack(endian + fmt, payload[i:i + size])[0] for i in range(0, len(payload), size))
                        actual = read_value(field_type, payload, count, endian)
                        self.assertEqual(actual, expected[0] if count == 1 else expected)
                        self.assertIsInstance(actual, (int, float) if count == 1 else tuple)

    def test_float_nonfinite_and_signed_zero_are_not_normalized(self):
        for endian in ("<", ">"):
            for field_type, fmt in ((11, "f"), (12, "d")):
                values = (-0.0, 0.0, math.inf, -math.inf, math.nan)
                payload = struct.pack(endian + "5" + fmt, *values)
                result = read_value(field_type, payload, len(values), endian)
                self.assertEqual(struct.pack(endian + "5" + fmt, *result), payload)

    def test_ascii_rationals_and_unsupported_types_retain_existing_results(self):
        self.assertEqual(read_value(2, b"camera\x00tail", 11), "camera")
        self.assertEqual(read_value(2, b"\xff\x00", 2), "\ufffd")
        for endian in ("<", ">"):
            for field_type, fmt, pairs in ((5, "II", ((1, 2), (1, 0))), (10, "ii", ((-3, 2), (1, 0)))):
                payload = b"".join(struct.pack(endian + fmt, *pair) for pair in pairs)
                self.assertEqual(read_value(field_type, payload, 2, endian), (pairs[0][0] / 2, None))
                self.assertEqual(read_value(field_type, b"", 0, endian), ())
        self.assertEqual(read_value(99, b"", 0), {"unsupported_type": 99, "count": 0})

    def test_numeric_arrays_use_one_bulk_unpack_after_checked_offset(self):
        payload = struct.pack("<8192I", *range(8192))
        with patch.object(dng.struct, "unpack", wraps=struct.unpack) as unpack:
            actual = read_value(4, payload, 8192)
        self.assertEqual(actual, tuple(range(8192)))
        self.assertEqual([call.args[0] for call in unpack.call_args_list], ["<I", "<8192I"])

    def test_undefined_payloads_stay_compact_binary_at_all_sizes(self):
        for endian in ("<", ">"):
            for payload in (b"", b"1", b"0211", bytes(range(256)) * 4096):
                with self.subTest(endian=endian, length=len(payload)):
                    data = tag_container(37500, 7, len(payload), payload, endian)
                    _, _, ifds = dng.DngMetadataReader()._read_structure(data)
                    value = ifds[0].tags[37500].value
                    self.assertIsInstance(value, bytes)
                    self.assertEqual(value, payload)
                    self.assertIs(nikon._undefined_bytes(value), value)
                    self.assertLessEqual(sys.getsizeof(value), len(payload) + 64)

    def test_summary_keeps_public_tuple_and_json_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.dng"
            original = tag_container(50706, 7, 4, bytes((1, 4, 0, 0)))
            path.write_bytes(original)
            metadata = dng.DngMetadataReader().read(path)
            summary = metadata.as_dict()
            self.assertEqual(metadata.ifds[0].tags[50706].value, bytes((1, 4, 0, 0)))
            self.assertEqual(summary["dng_version"], (1, 4, 0, 0))
            self.assertEqual(summary["dng_version_text"], "1.4.0.0")
            self.assertEqual(json.loads(json.dumps(summary))["dng_version"], [1, 4, 0, 0])
            self.assertEqual(path.read_bytes(), original)

    def test_mapped_metadata_remains_readable_after_closing_the_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mapped.nef"
            payload = bytes(range(256)) * 16
            original = tag_container(37500, 7, len(payload), payload)
            path.write_bytes(original)
            with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                _, _, ifds = dng.DngMetadataReader()._read_structure(mapped)
            self.assertIsInstance(ifds[0].tags[37500].value, bytes)
            self.assertEqual(ifds[0].tags[37500].value, payload)
            self.assertEqual(path.read_bytes(), original)

    def test_malformed_ranges_still_fail_before_bulk_allocation(self):
        for endian in ("<", ">"):
            for field_type in (1, 3, 4, 7, 11, 12):
                with self.subTest(endian=endian, field_type=field_type):
                    with self.assertRaises(dng.DngMetadataError):
                        dng._read_tag_value(bytes(32), endian, field_type, 0xFFFFFFFF, struct.pack(endian + "I", 8))
                    with self.assertRaises(dng.DngMetadataError):
                        dng._read_tag_value(bytes(32), endian, field_type, 8, struct.pack(endian + "I", 30))
            data = tag_container(37500, 7, 100, bytes(100), endian)
            with self.assertRaises(dng.DngMetadataError):
                dng.DngMetadataReader()._read_structure(data[:-1])

    def test_numeric_consumers_do_not_interpret_binary_values_as_ascii_numbers(self):
        for payload in (b"1", b"12", b"", b"\x00\xff"):
            for sequence in (payload, tuple(payload)):
                expected = tuple(payload)
                self.assertEqual(dng._tuple_of_ints(sequence), expected)
                self.assertEqual(dng._positive_offsets(sequence), tuple(v for v in payload if v > 0))
                self.assertEqual(support._tuple_int(sequence), expected)
                self.assertEqual(nikon._value_int_tuple(sequence), expected)
                ifd = dng.TiffIfd(0, {258: dng.TiffTag(258, "", 7, len(payload), sequence)}, 0)
                self.assertEqual(nikon._tag_int_tuple(ifd, 258), expected)
                self.assertEqual(nikon._tag_float_tuple(ifd, 258), tuple(float(v) for v in payload))
                scalar = payload[0] if len(payload) == 1 else None
                self.assertEqual(support._scalar_int(sequence), scalar)
                self.assertEqual(support._scalar_float(sequence), scalar)
                self.assertEqual(nikon._optional_int(ifd, 258), scalar)
                self.assertEqual(nikon._tag_int(ifd, 258), scalar)
                if len(payload) == 1:
                    self.assertEqual(dng._scalar_int_value(sequence), scalar)
                    self.assertEqual(nikon._required_bits_per_sample(ifd), scalar)
                else:
                    with self.assertRaises(dng.DngMetadataError):
                        dng._scalar_int_value(sequence)
                    with self.assertRaises(nikon.NikonCompressionError):
                        nikon._required_bits_per_sample(ifd)

    def test_nikon_decoding_support_and_summaries_match_legacy_tuple_metadata(self):
        read_tag_value = dng._read_tag_value

        def legacy(*args):
            value = read_tag_value(*args)
            return tuple(value) if isinstance(value, bytes) else value

        def inspect_and_decode(path):
            metadata = dng.DngMetadataReader().read(path)
            return (
                metadata.as_dict(), nikon.summarize_nikon_makernote(metadata),
                nikon.extract_nikon_as_shot_white_balance(metadata),
                support.inspect_native_support(path).as_dict(),
                nikon.decode_nikon_34713_lossless(path, metadata),
            )

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nef"
            payload = synthetic_nikon_nef_compressed_bytes(width=4, height=4, samples=(4096,) * 16)
            source.write_bytes(payload)
            actual = inspect_and_decode(source)
            with patch.object(dng, "_read_tag_value", side_effect=legacy):
                expected = inspect_and_decode(source)
            self.assertEqual(actual, expected)
            self.assertEqual(source.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
