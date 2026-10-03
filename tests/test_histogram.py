import unittest
from unittest.mock import patch

import numpy as np

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

    def test_vectorized_counts_match_reference_for_every_supported_bin_count(self):
        pixels = [(value, 255 - value, (value * 17) % 256) for value in range(256)]
        pixels.extend([(2, 2, 2), (3, 0, 0), (252, 0, 0), (0, 253, 0), (0, 0, 255), (255, 255, 255)])
        payload = bytes(channel for pixel in pixels for channel in pixel)
        for bins in range(8, 257):
            with self.subTest(bins=bins):
                self.assertEqual(analyze_rgb_bytes(payload, bins=bins), analyze_rgb_pixels(pixels, bins=bins))

    def test_large_buffer_uses_bounded_chunks_without_losing_boundary_pixels(self):
        payload = bytes((0, 1, 2)) * 262144 + bytes((255, 0, 0, 100, 101, 102))
        with patch.object(np, "bincount", wraps=np.bincount) as count:
            actual = analyze_rgb_bytes(payload, bins=127)
        self.assertEqual([len(call.args[0]) for call in count.call_args_list], [262144] * 4 + [2] * 4)
        self.assertEqual(actual.pixel_count, 262146)
        self.assertEqual(actual.shadow_clipped_pixels, 262144)
        self.assertEqual(actual.highlight_clipped_pixels, 1)
        for histogram in (actual.red, actual.green, actual.blue, actual.luminance):
            self.assertEqual(sum(histogram), actual.pixel_count)
            self.assertTrue(all(type(value) is int for value in histogram))

    def test_mutable_readonly_and_shaped_buffers_are_not_modified(self):
        payload = bytearray(range(18))
        original = bytes(payload)
        expected = analyze_rgb_bytes(original)
        for view in (payload, memoryview(payload), memoryview(original), memoryview(payload).cast("B", (2, 3, 3))):
            self.assertEqual(analyze_rgb_bytes(view), expected)
        self.assertEqual(bytes(payload), original)
        with self.assertRaises(TypeError):
            analyze_rgb_bytes(memoryview(payload)[::2])

    def test_numpy_unavailable_retains_streaming_reference(self):
        payload = bytes((1, 2, 3, 254, 120, 10, 255, 255, 255))
        expected = analyze_rgb_bytes(payload)
        with patch.dict("sys.modules", {"numpy": None}):
            self.assertEqual(analyze_rgb_bytes(payload), expected)

    def test_invalid_bins_and_pixel_types_are_not_silently_coerced(self):
        for bins in (True, 7, 257):
            with self.assertRaises(ValueError):
                analyze_rgb_bytes(b"\x00\x00\x00", bins=bins)
        for bins in (64.0, "64", None):
            with self.assertRaises(TypeError):
                analyze_rgb_bytes(b"\x00\x00\x00", bins=bins)
        for channel in (True, .5, np.uint8(1), -1, 256):
            with self.assertRaises(ValueError):
                analyze_rgb_pixels(((channel, 0, 0),))


if __name__ == "__main__":
    unittest.main()
