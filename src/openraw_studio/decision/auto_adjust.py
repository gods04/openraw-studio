"""Conservative V0.1 automatic tone suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.tone import PreviewRgbImage


@dataclass(frozen=True)
class AutoAdjustSuggestion:
    """Basic adjustment values suggested by the first local AUTO pass."""

    exposure: float
    contrast: float
    warmth: float
    rationale: tuple[str, ...]

    def as_overrides(self) -> dict[str, float]:
        return {
            "exposure": self.exposure,
            "contrast": self.contrast,
            "warmth": self.warmth,
        }


@dataclass(frozen=True)
class PreviewStats:
    mean_luma: float
    shadow_luma: float
    highlight_luma: float
    red_mean: float
    blue_mean: float

    @property
    def luma_range(self) -> float:
        return self.highlight_luma - self.shadow_luma


def suggest_auto_adjustments(source_path: str | Path) -> AutoAdjustSuggestion:
    """Suggest subtle starter adjustments for the current DNG-first pipeline."""

    preview = render_preview_image(Path(source_path), max_dimension=256)
    return suggest_auto_adjustments_from_preview(preview)


def suggest_auto_adjustments_from_preview(preview: PreviewRgbImage) -> AutoAdjustSuggestion:
    stats = _preview_stats(preview)
    exposure, exposure_note = _suggest_exposure(stats.mean_luma)
    contrast, contrast_note = _suggest_contrast(stats.luma_range)
    warmth, warmth_note = _suggest_warmth(stats.red_mean, stats.blue_mean)
    return AutoAdjustSuggestion(
        exposure=exposure,
        contrast=contrast,
        warmth=warmth,
        rationale=tuple(note for note in (exposure_note, contrast_note, warmth_note) if note),
    )


def _preview_stats(preview: PreviewRgbImage) -> PreviewStats:
    if not preview.pixels:
        raise ValueError("preview has no pixels")

    lumas = sorted(_luma(red, green, blue) for red, green, blue in preview.pixels)
    pixel_count = len(preview.pixels)
    shadow_index = max(0, min(pixel_count - 1, int(pixel_count * 0.05)))
    highlight_index = max(0, min(pixel_count - 1, int(pixel_count * 0.95)))
    red_mean = sum(red for red, _green, _blue in preview.pixels) / (255.0 * pixel_count)
    blue_mean = sum(blue for _red, _green, blue in preview.pixels) / (255.0 * pixel_count)
    return PreviewStats(
        mean_luma=sum(lumas) / pixel_count,
        shadow_luma=lumas[shadow_index],
        highlight_luma=lumas[highlight_index],
        red_mean=red_mean,
        blue_mean=blue_mean,
    )


def _suggest_exposure(mean_luma: float) -> tuple[float, str]:
    if mean_luma < 0.28:
        return 0.6, "Lifted exposure for a dark preview."
    if mean_luma < 0.38:
        return 0.3, "Added a small exposure lift."
    if mean_luma > 0.74:
        return -0.5, "Reduced exposure for a bright preview."
    if mean_luma > 0.64:
        return -0.25, "Added a small exposure reduction."
    return 0.0, "Exposure already looks balanced."


def _suggest_contrast(luma_range: float) -> tuple[float, str]:
    if luma_range < 0.34:
        return 0.22, "Added contrast because the preview is flat."
    if luma_range < 0.48:
        return 0.12, "Added a small contrast lift."
    if luma_range > 0.78:
        return -0.08, "Softened contrast for a high-range preview."
    return 0.06, "Kept contrast subtle."


def _suggest_warmth(red_mean: float, blue_mean: float) -> tuple[float, str]:
    if blue_mean > red_mean * 1.12:
        return 0.12, "Warmed a cool-looking preview."
    if red_mean > blue_mean * 1.18:
        return -0.06, "Cooled a very warm preview slightly."
    return 0.04, "Added a gentle warmth bias."


def _luma(red: int, green: int, blue: int) -> float:
    return ((0.2126 * red) + (0.7152 * green) + (0.0722 * blue)) / 255.0
