"""Small RGB histogram and clipping analysis for rendered previews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class HistogramAnalysis:
    """Histogram counts and clipping fractions for an 8-bit RGB image."""

    bins: int
    pixel_count: int
    luminance: tuple[int, ...]
    red: tuple[int, ...]
    green: tuple[int, ...]
    blue: tuple[int, ...]
    shadow_clipped_pixels: int
    highlight_clipped_pixels: int

    @property
    def shadow_clip_fraction(self) -> float:
        return self.shadow_clipped_pixels / self.pixel_count if self.pixel_count else 0.0

    @property
    def highlight_clip_fraction(self) -> float:
        return self.highlight_clipped_pixels / self.pixel_count if self.pixel_count else 0.0

    def has_significant_clipping(self, *, threshold: float = 0.001) -> bool:
        return self.shadow_clip_fraction >= threshold or self.highlight_clip_fraction >= threshold


def analyze_rgb_bytes(payload: bytes | bytearray | memoryview, *, bins: int = 64) -> HistogramAnalysis:
    """Analyze packed RGB bytes without requiring an image-library object."""

    view = memoryview(payload).cast("B")
    if len(view) % 3:
        raise ValueError("RGB payload length must be divisible by three")
    return analyze_rgb_pixels(
        ((view[index], view[index + 1], view[index + 2]) for index in range(0, len(view), 3)),
        bins=bins,
    )


def analyze_rgb_pixels(pixels: Iterable[tuple[int, int, int]], *, bins: int = 64) -> HistogramAnalysis:
    """Build channel and luminance histograms from 8-bit RGB pixels."""

    if not 8 <= bins <= 256:
        raise ValueError("bins must be between 8 and 256")

    luminance_counts = [0] * bins
    red_counts = [0] * bins
    green_counts = [0] * bins
    blue_counts = [0] * bins
    pixel_count = 0
    shadow_clipped = 0
    highlight_clipped = 0

    for red, green, blue in pixels:
        _validate_channel(red)
        _validate_channel(green)
        _validate_channel(blue)
        luminance = ((54 * red) + (183 * green) + (19 * blue)) >> 8
        luminance_counts[_bin_index(luminance, bins)] += 1
        red_counts[_bin_index(red, bins)] += 1
        green_counts[_bin_index(green, bins)] += 1
        blue_counts[_bin_index(blue, bins)] += 1
        pixel_count += 1
        if max(red, green, blue) <= 2:
            shadow_clipped += 1
        if max(red, green, blue) >= 253:
            highlight_clipped += 1

    if pixel_count == 0:
        raise ValueError("RGB pixel collection is empty")

    return HistogramAnalysis(
        bins=bins,
        pixel_count=pixel_count,
        luminance=tuple(luminance_counts),
        red=tuple(red_counts),
        green=tuple(green_counts),
        blue=tuple(blue_counts),
        shadow_clipped_pixels=shadow_clipped,
        highlight_clipped_pixels=highlight_clipped,
    )


def _bin_index(value: int, bins: int) -> int:
    return min(bins - 1, (value * bins) // 256)


def _validate_channel(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError("RGB channel values must be integers from 0 to 255")
