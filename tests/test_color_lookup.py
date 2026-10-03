import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.raw.native.nikon import NikonWhiteBalance, _linear_color_luts
from openraw_studio.raw.native.profiles import find_camera_color_profile


class ColorLookupTests(unittest.TestCase):
    def options(self, **overrides):
        profile = find_camera_color_profile("NIKON CORPORATION", "NIKON D500")
        return {
            "black_levels": (400., 400.5, 401.), "fallback_black_level": 400, "white_level": 16383,
            "exposure": 0, "contrast": 0, "highlights": 0, "shadows": 0, "warmth": 0, "tint": 0,
            "camera_white_balance": NikonWhiteBalance(2.1, 1, 1.6, "test"),
            "camera_to_linear_srgb": profile.camera_to_linear_srgb, **overrides,
        }

    def assert_tables_match(self, options):
        actual = _linear_color_luts(**options)
        with patch.dict("sys.modules", {"numpy": None}):
            expected = _linear_color_luts(**options)
        for actual_source, expected_source in zip(actual[:3], expected[:3]):
            self.assertIsInstance(actual_source, tuple)
            for actual_table, expected_table in zip(actual_source, expected_source):
                self.assertEqual(actual_table.typecode, "i")
                self.assertEqual(len(actual_table), 65536)
                np.testing.assert_array_equal(np.frombuffer(actual_table, np.int32), np.frombuffer(expected_table, np.int32))
        self.assertIsInstance(actual[3], bytes)
        self.assertEqual(len(actual[3]), 4 * 65535 + 1)
        np.testing.assert_array_equal(np.frombuffer(actual[3], np.uint8), np.frombuffer(expected[3], np.uint8))

    def test_tables_match_scalar_precision_boundaries_and_control_extremes(self):
        cases = [
            self.options(),
            self.options(black_levels=(0, 0, 0), white_level=65535, camera_white_balance=None, camera_to_linear_srgb=None),
            self.options(white_level=4095, exposure=-4, contrast=-1, shadows=1, highlights=1, warmth=-1, tint=1),
            self.options(exposure=4, contrast=1, highlights=-1, shadows=-1, warmth=1, tint=-1),
            self.options(exposure=1.2, highlights=-.3, shadows=.04),
            self.options(black_levels=(-1, float("inf"), float("nan"))),
        ]
        for options in cases:
            with self.subTest(options=options):
                self.assert_tables_match(options)

    def test_randomized_tables_match_reference_without_changing_options(self):
        rng = np.random.default_rng(29)
        for _ in range(6):
            values = rng.uniform(-1, 1, 6)
            options = self.options(**dict(zip(("contrast", "highlights", "shadows", "warmth", "tint", "exposure"), values)))
            options["exposure"] *= 4
            before = options.copy()
            self.assert_tables_match(options)
            self.assertEqual(options, before)

    def test_separate_calls_return_independent_mutable_matrix_tables(self):
        options = self.options()
        first = _linear_color_luts(**options)
        second = _linear_color_luts(**options)
        unchanged = second[0][0][12345]
        first[0][0][12345] += 1
        self.assertEqual(second[0][0][12345], unchanged)


if __name__ == "__main__":
    unittest.main()
