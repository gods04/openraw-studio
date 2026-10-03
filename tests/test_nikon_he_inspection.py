import unittest

from test_photo_catalog import load_script


def framing_sample(count=18):
    data = bytearray(b"\xff\x10")
    for marker in (0xFF50, 0xFF12, 0xFF13, 0xFF14):
        payload = b"sample"
        data.extend(marker.to_bytes(2, "big") + (len(payload) + 2).to_bytes(2, "big") + payload)
    data.extend(b"\xff\x20\x00\x04\x00\x00")
    for index in range(count):
        data.extend((56).to_bytes(3, "big") + bytes((2 + index % 2, 8)) + bytes(7))
        data.extend(bytes(56))
        if (index + 1) % 16 == 0 and index + 1 < count:
            data.extend(b"\xff\x20\x00\x04" + ((index + 1) // 16).to_bytes(2, "big"))
    data.extend(b"\xff\x11")
    return data


class NikonHeInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = load_script("inspect_nikon_he")

    def inspect(self, data, height=72):
        return self.probe.inspect_framing(data, offset=0, length=len(data), height=height)

    def test_variable_header_and_precinct_boundaries_without_decoding(self):
        data = framing_sample()
        original = bytes(data)
        report = self.inspect(data)
        self.assertEqual(report["precincts"], 18)
        self.assertEqual(report["slices"], 2)
        self.assertEqual(report["precinct_offset"], 48)
        self.assertEqual(report["bp_br_counts"], [{"bp": 2, "br": 8, "count": 9}, {"bp": 3, "br": 8, "count": 9}])
        self.assertEqual(report["depth_hint_counts"], {0: 18 * 28})
        self.assertFalse(report["pixels_decoded"])
        self.assertEqual(bytes(data), original)

    def test_offsets_and_exact_slice_height(self):
        stream = framing_sample(16)
        data = b"prefix" + stream + b"suffix"
        report = self.probe.inspect_framing(data, offset=6, length=len(stream), height=64)
        self.assertEqual(report["slices"], 1)
        self.assertTrue(report["framing_valid"])

    def test_truncation_bad_lengths_and_wrong_end_marker(self):
        original = framing_sample()
        cases = [original[:10], original[:-1], original + b"padding"]
        bad = original.copy()
        bad[4:6] = b"\xff\xff"
        cases.append(bad)
        bad = original.copy()
        bad[48:51] = b"\xff\xff\xff"
        cases.append(bad)
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.inspect(data)

    def test_out_of_order_slice_is_rejected(self):
        data = framing_sample()
        position = 48 + 16 * 68
        data[position + 5] = 2
        with self.assertRaisesRegex(ValueError, "slice boundary"):
            self.inspect(data)

    def test_invalid_dimensions_and_strip_bounds_are_rejected(self):
        data = framing_sample()
        for height in (0, -4, 3, 10**12):
            with self.subTest(height=height), self.assertRaises(ValueError):
                self.inspect(data, height=height)
        for offset, length in ((-1, len(data)), (0, len(data) + 1), (0, -1)):
            with self.subTest(offset=offset, length=length), self.assertRaises(ValueError):
                self.probe.inspect_framing(data, offset=offset, length=length, height=72)


if __name__ == "__main__":
    unittest.main()
