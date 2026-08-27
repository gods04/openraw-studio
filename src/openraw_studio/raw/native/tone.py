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


def tone_map_preview(
    linear: LinearRgbImage,
    *,
    exposure: float = 0.0,
    contrast: float = 0.0,
    warmth: float = 0.0,
    gamma: float = 2.2,
) -> PreviewRgbImage:
    """Map linear RGB values to a small 8-bit preview.

    This is a deliberately simple preview transform. It is not final color
    science, camera profiling, or perceptual rendering.
    """

    if gamma <= 0.0:
        raise ValueError("gamma must be greater than zero")

    exposure_scale = 2.0**exposure
    contrast_factor = 1.0 + _clamp(contrast, -1.0, 1.0) * 0.75
    warmth_value = _clamp(warmth, -1.0, 1.0)
    pixels = tuple(
        _encode_pixel(
            red,
            green,
            blue,
            exposure_scale=exposure_scale,
            contrast_factor=contrast_factor,
            warmth=warmth_value,
            gamma=gamma,
        )
        for red, green, blue in linear.pixels
    )
    return PreviewRgbImage(width=linear.width, height=linear.height, pixels=pixels, transfer=f"gamma-{gamma:g}")


def _encode_pixel(
    red: float,
    green: float,
    blue: float,
    *,
    exposure_scale: float,
    contrast_factor: float,
    warmth: float,
    gamma: float,
) -> tuple[int, int, int]:
    red, green, blue = _apply_warmth(red, green, blue, warmth=warmth)
    red = _apply_contrast(red * exposure_scale, contrast_factor)
    green = _apply_contrast(green * exposure_scale, contrast_factor)
    blue = _apply_contrast(blue * exposure_scale, contrast_factor)
    return (
        _encode_channel(red, gamma),
        _encode_channel(green, gamma),
        _encode_channel(blue, gamma),
    )


def _apply_warmth(red: float, green: float, blue: float, *, warmth: float) -> tuple[float, float, float]:
    red_scale = 1.0 + warmth * 0.12
    blue_scale = 1.0 - warmth * 0.12
    green_scale = 1.0 + warmth * 0.03
    return red * red_scale, green * green_scale, blue * blue_scale


def _apply_contrast(value: float, factor: float) -> float:
    pivot = 0.18
    return ((value - pivot) * factor) + pivot


def _encode_channel(value: float, gamma: float) -> int:
    encoded = _clamp01(value) ** (1.0 / gamma)
    return int(round(encoded * 255.0))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
