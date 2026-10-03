"""Tone mapping for early OpenRAW Native previews."""

from __future__ import annotations

from dataclasses import dataclass

from openraw_studio.raw.native.demosaic import LinearRgbImage
from openraw_studio.raw.native.tonal import apply_tonal_regions as _apply_tonal_regions


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
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    tint: float = 0.0,
    saturation: float = 0.0,
    color_noise: float = 0.0,
    luminance_noise: float = 0.0,
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
    highlights_value = _clamp(highlights, -1.0, 1.0)
    shadows_value = _clamp(shadows, -1.0, 1.0)
    warmth_value = _clamp(warmth, -1.0, 1.0)
    tint_value = _clamp(tint, -1.0, 1.0)
    saturation_factor = 1.0 + _clamp(saturation, -1.0, 1.0) * 0.75
    pixels = tuple(
        _encode_pixel(
            red,
            green,
            blue,
            exposure_scale=exposure_scale,
            contrast_factor=contrast_factor,
            highlights=highlights_value,
            shadows=shadows_value,
            warmth=warmth_value,
            tint=tint_value,
            saturation_factor=saturation_factor,
            gamma=gamma,
        )
        for red, green, blue in linear.pixels
    )
    if color_noise != 0 or luminance_noise != 0:
        import numpy as np

        from openraw_studio.raw.native.noise import reduce_noise

        rgb = np.asarray(pixels, np.uint8).reshape(linear.height, linear.width, 3)
        pixels = tuple(map(tuple, reduce_noise(rgb, color_noise=color_noise, luminance_noise=luminance_noise).reshape(-1, 3).tolist()))
    return PreviewRgbImage(width=linear.width, height=linear.height, pixels=pixels, transfer=f"gamma-{gamma:g}")


def _encode_pixel(
    red: float,
    green: float,
    blue: float,
    *,
    exposure_scale: float,
    contrast_factor: float,
    highlights: float,
    shadows: float,
    warmth: float,
    tint: float,
    saturation_factor: float,
    gamma: float,
) -> tuple[int, int, int]:
    red, green, blue = _apply_white_balance(red, green, blue, warmth=warmth, tint=tint)
    red = _apply_tonal_regions(_apply_contrast(red * exposure_scale, contrast_factor), highlights=highlights, shadows=shadows)
    green = _apply_tonal_regions(_apply_contrast(green * exposure_scale, contrast_factor), highlights=highlights, shadows=shadows)
    blue = _apply_tonal_regions(_apply_contrast(blue * exposure_scale, contrast_factor), highlights=highlights, shadows=shadows)
    red, green, blue = _apply_saturation(red, green, blue, factor=saturation_factor)
    return (
        _encode_channel(red, gamma),
        _encode_channel(green, gamma),
        _encode_channel(blue, gamma),
    )


def _apply_white_balance(
    red: float,
    green: float,
    blue: float,
    *,
    warmth: float,
    tint: float,
) -> tuple[float, float, float]:
    red_scale = (1.0 + warmth * 0.12) * (1.0 + tint * 0.08)
    blue_scale = (1.0 - warmth * 0.12) * (1.0 + tint * 0.08)
    green_scale = (1.0 + warmth * 0.03) * (1.0 - tint * 0.12)
    return red * red_scale, green * green_scale, blue * blue_scale


def _apply_contrast(value: float, factor: float) -> float:
    pivot = 0.18
    return ((value - pivot) * factor) + pivot


def _apply_saturation(red: float, green: float, blue: float, *, factor: float) -> tuple[float, float, float]:
    luma = (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
    return (
        luma + ((red - luma) * factor),
        luma + ((green - luma) * factor),
        luma + ((blue - luma) * factor),
    )


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
