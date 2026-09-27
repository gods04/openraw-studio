import struct
import unittest

from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8


class FullResolutionBayerTests(unittest.TestCase):
    def test_renders_every_standard_bayer_layout_at_full_dimensions(self) -> None:
        channel_samples = (1024, 2048, 3072)
        patterns = (
            (0, 1, 1, 2),
            (1, 0, 2, 1),
            (1, 2, 0, 1),
            (2, 1, 1, 0),
        )

        for pattern in patterns:
            with self.subTest(pattern=pattern):
                samples = tuple(
                    channel_samples[pattern[((row & 1) * 2) + (column & 1)]]
                    for row in range(6)
                    for column in range(8)
                )
                rendered = render_bayer_full_resolution_rgb8(
                    _raw_bytes(samples),
                    source_width=8,
                    source_height=6,
                    crop=(0, 0, 8, 6),
                    cfa_pattern=pattern,
                    black_levels=(0, 0, 0, 0),
                    white_level=4095,
                    channel_gains=(1.0, 1.0, 1.0),
                    camera_to_linear_srgb=None,
                )

                pixels = tuple(
                    rendered.rgb_bytes[index : index + 3]
                    for index in range(0, len(rendered.rgb_bytes), 3)
                )
                self.assertEqual((rendered.width, rendered.height), (8, 6))
                self.assertEqual(len(set(pixels)), 1)
                self.assertLess(pixels[0][0], pixels[0][1])
                self.assertLess(pixels[0][1], pixels[0][2])

    def test_chunk_boundaries_match_for_offset_crop_and_position_black_levels(self) -> None:
        samples = tuple(
            300 + (((row * 8) + column) * 47) % 3000
            for row in range(8)
            for column in range(8)
        )
        kwargs = {
            "source_width": 8,
            "source_height": 8,
            "crop": (1, 1, 6, 6),
            "cfa_pattern": (0, 1, 1, 2),
            "black_levels": (64, 80, 96, 112),
            "white_level": 4095,
            "channel_gains": (1.3, 1.0, 0.8),
            "camera_to_linear_srgb": (
                (1.1, -0.05, -0.05),
                (-0.05, 1.1, -0.05),
                (-0.05, -0.05, 1.1),
            ),
            "contrast": 0.2,
            "highlights": -0.15,
            "shadows": 0.1,
            "saturation": 0.25,
        }

        one_row = render_bayer_full_resolution_rgb8(
            _raw_bytes(samples), chunk_rows=1, **kwargs
        )
        two_rows = render_bayer_full_resolution_rgb8(
            _raw_bytes(samples), chunk_rows=2, **kwargs
        )
        one_chunk = render_bayer_full_resolution_rgb8(
            _raw_bytes(samples), chunk_rows=256, **kwargs
        )

        self.assertEqual(one_row, two_rows)
        self.assertEqual(two_rows, one_chunk)

    def test_rejects_invalid_buffer_crop_cfa_and_chunk_size(self) -> None:
        valid = {
            "raw_bytes": _raw_bytes((512,) * 16),
            "source_width": 4,
            "source_height": 4,
            "crop": (0, 0, 4, 4),
            "cfa_pattern": (0, 1, 1, 2),
            "black_levels": (0, 0, 0, 0),
            "white_level": 4095,
            "channel_gains": (1.0, 1.0, 1.0),
            "camera_to_linear_srgb": None,
        }

        for override in (
            {"raw_bytes": b"too short"},
            {"crop": (3, 3, 4, 4)},
            {"cfa_pattern": (0, 0, 1, 2)},
            {"chunk_rows": 0},
            {"white_level": 0},
            {"black_levels": (4095, 0, 0, 0)},
            {"black_levels": (0, 0, 0)},
            {"channel_gains": (1.0, float("nan"), 1.0)},
            {"camera_to_linear_srgb": ((1.0, 0.0), (0.0, 1.0))},
        ):
            with self.subTest(override=override):
                arguments = dict(valid)
                arguments.update(override)
                with self.assertRaises(ValueError):
                    render_bayer_full_resolution_rgb8(**arguments)


def _raw_bytes(samples: tuple[int, ...]) -> bytes:
    return struct.pack(f"<{len(samples)}H", *samples)


if __name__ == "__main__":
    unittest.main()
