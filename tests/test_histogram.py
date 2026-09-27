import unittest

from openraw_studio.qc.histogram import analyze_rgb_bytes, analyze_rgb_pixels


class HistogramAnalysisTests(unittest.TestCase):
    def test_analyze_rgb_pixels_builds_channel_and_luminance_counts(self) -> None:
        analysis = analyze_rgb_pixels(((0, 0, 0), (255, 128, 64), (32, 64, 255)), bins=8)

        self.assertEqual(analysis.pixel_count, 3)
        self.assertEqual(sum(analysis.luminance), 3)
        self.assertEqual(sum(analysis.red), 3)
        self.assertEqual(sum(analysis.green), 3)
        self.assertEqual(sum(analysis.blue), 3)
        self.assertEqual(analysis.red[0], 1)
        self.assertEqual(analysis.red[-1], 1)

    def test_clipping_counts_near_black_and_any_clipped_highlight_channel(self) -> None:
        analysis = analyze_rgb_pixels(((0, 1, 2), (252, 252, 252), (253, 120, 80), (255, 255, 255)), bins=16)

        self.assertEqual(analysis.shadow_clipped_pixels, 1)
        self.assertEqual(analysis.highlight_clipped_pixels, 2)
        self.assertAlmostEqual(analysis.shadow_clip_fraction, 0.25)
        self.assertAlmostEqual(analysis.highlight_clip_fraction, 0.5)
        self.assertTrue(analysis.has_significant_clipping())

    def test_analyze_rgb_bytes_matches_pixel_analysis(self) -> None:
        payload = bytes((10, 20, 30, 240, 250, 255))

        from_bytes = analyze_rgb_bytes(payload, bins=32)
        from_pixels = analyze_rgb_pixels(((10, 20, 30), (240, 250, 255)), bins=32)

        self.assertEqual(from_bytes, from_pixels)

    def test_rejects_empty_malformed_or_out_of_range_input(self) -> None:
        with self.assertRaises(ValueError):
            analyze_rgb_bytes(b"\x00\x01")
        with self.assertRaises(ValueError):
            analyze_rgb_bytes(b"")
        with self.assertRaises(ValueError):
            analyze_rgb_pixels(((256, 0, 0),))
        with self.assertRaises(ValueError):
            analyze_rgb_pixels(((0, 0, 0),), bins=4)


if __name__ == "__main__":
    unittest.main()
