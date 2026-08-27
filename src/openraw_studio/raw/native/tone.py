"""Tone mapping for early OpenRAW Native previews."""

from __future__ import annotations

from dataclasses import dataclass

from openraw_studio.raw.native.demosaic import LinearRgbImage


@dataclass(frozen=True)
class PreviewRgbImage:
    """8-bit RGB image ready for simple preview encoding."""

    width: int
    height: int
    pixels: tuple[tuple[int, int, int], ...]
    transfer: str

    def pixel_at(self, row: int, column: int) -> tuple[int, int, int]:
        if row < 0 or row >= self.height:
            raise IndexError("row outside image bounds")
        if column < 0 or column >= self.width:
            raise IndexError("column outside image bounds")
        return self.pixels[row * self.width + column]


def tone_map_preview(linear: LinearRgbImage, *, exposure: float = 0.0, gamma: float = 2.2) -> PreviewRgbImage:
    """Map linear RGB values to a small 8-bit preview.

    This is a deliberately simple preview transform. It is not final color
    science, camera profiling, or perceptual rendering.
    """

    if gamma <= 0.0:
        raise ValueError("gamma must be greater than zero")

    exposure_scale = 2.0**exposure
    pixels = tuple(
        (
            _encode_channel(red * exposure_scale, gamma),
            _encode_channel(green * exposure_scale, gamma),
            _encode_channel(blue * exposure_scale, gamma),
        )
        for red, green, blue in linear.pixels
    )
    return PreviewRgbImage(width=linear.width, height=linear.height, pixels=pixels, transfer=f"gamma-{gamma:g}")


def _encode_channel(value: float, gamma: float) -> int:
    encoded = _clamp01(value) ** (1.0 / gamma)
    return int(round(encoded * 255.0))


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
