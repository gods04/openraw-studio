"""Scene-aware, deterministic automatic adjustments with rendered clipping guards."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import numpy as np

from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.tone import PreviewRgbImage


@dataclass(frozen=True)
class PreviewStats:
    """Legacy public summary shape retained for existing integrations."""

    mean_luma: float
    shadow_luma: float
    highlight_luma: float
    red_mean: float
    green_mean: float
    blue_mean: float
    mean_chroma: float

    @property
    def luma_range(self) -> float:
        return self.highlight_luma - self.shadow_luma


@dataclass(frozen=True)
class AutoAdjustSuggestion:
    exposure: float
    contrast: float
    highlights: float
    shadows: float
    warmth: float
    tint: float
    saturation: float
    rationale: tuple[str, ...]
    scene: str = "Balanced"
    metrics: dict[str, float] = field(default_factory=dict)

    def as_overrides(self) -> dict[str, float]:
        return {
            key: getattr(self, key)
            for key in (
                "exposure",
                "contrast",
                "highlights",
                "shadows",
                "warmth",
                "tint",
                "saturation",
            )
        }


def _pixels(preview):
    if isinstance(preview, PreviewRgbImage):
        pixels = np.asarray(preview.pixels, dtype=np.float32)
    else:
        pixels = np.asarray(preview, dtype=np.float32)
    if (
        pixels.size == 0
        or pixels.ndim < 2
        or pixels.shape[-1] != 3
        or not np.isfinite(pixels).all()
    ):
        raise ValueError("Preview must contain finite RGB pixels")
    return np.clip(pixels.reshape(-1, 3) / 255, 0, 1)


def _clipping(pixels):
    return float(np.mean(np.max(pixels, axis=1) >= 254 / 255))


def suggest_auto_adjustments(source_path: str | Path) -> AutoAdjustSuggestion:
    preview = render_preview_image(Path(source_path), max_dimension=256)
    return suggest_auto_adjustments_from_preview(preview)


def suggest_auto_adjustments_from_preview(
    preview: PreviewRgbImage,
    *,
    render: Callable | None = None,
) -> AutoAdjustSuggestion:
    """Analyze an unedited preview, optionally validating against the same renderer.

    The callback accepts adjustment overrides and returns RGB pixels/Pillow image.
    It must render from the original linear proxy, never from the edited preview.
    This is a local heuristic, not a trained AI model or semantic scene classifier.
    """
    pixels = _pixels(preview)
    luma = pixels @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    p05, p10, median, p90, p95, p99 = np.quantile(
        luma, [0.05, 0.1, 0.5, 0.9, 0.95, 0.99]
    )
    trimmed = luma[(luma >= p10) & (luma <= p90)]
    midtone = float(0.65 * median + 0.35 * np.mean(trimmed))
    spread = float(p95 - p05)
    clipped = _clipping(pixels)
    dark_fraction = float(np.mean(luma < 0.12))
    low_key = (dark_fraction > 0.55 and p95 > 0.50) or (
        dark_fraction > 0.90 and float(np.mean(luma > 0.25)) > 0.001
    )
    high_key = p10 > 0.62 and median > 0.72
    backlit = p10 < 0.10 and p95 > 0.84 and median < 0.4
    scene = (
        "Low-key"
        if low_key
        else "High-key"
        if high_key
        else "High contrast"
        if backlit
        else "Balanced"
    )
    target = 0.30 if low_key else 0.69 if high_key else 0.44
    exposure = float(np.clip(2.2 * np.log2(target / max(midtone, 0.025)), -0.8, 1.2))
    if abs(midtone - target) < 0.035:
        exposure = 0.0
    if low_key or backlit:
        exposure = min(exposure, 0.35)
    # A bright upper tail limits global exposure even when average luma is low.
    if exposure > 0 and p99 > 0.80:
        exposure = min(exposure, max(0, 2.2 * np.log2(0.96 / max(p99, 0.01))))
    contrast = (
        0.18
        if spread < 0.30
        else 0.10
        if spread < 0.48
        else -0.08
        if spread > 0.78
        else 0.03
    )
    highlights = -0.30 if p95 > 0.92 or clipped > 0.01 else -0.16 if p95 > 0.82 else 0.0
    shadows = (
        0.24 if p05 < 0.06 else 0.14 if p05 < 0.15 else 0.08 if p05 < 0.25 else 0.0
    )
    if low_key:
        shadows = min(shadows, 0.10)
    if low_key or backlit:
        contrast = 0.0
    if high_key:
        contrast = min(contrast, 0.05)
    if exposure > 0.5:
        shadows *= 0.5

    # Only weakly chromatic, usable midtones can support a neutral-balance vote.
    # Saturated scenery (blue lighting, foliage, sunset) is not a gray card.
    maximum, minimum = pixels.max(axis=1), pixels.min(axis=1)
    chroma = maximum - minimum
    neutral = (
        (chroma / np.maximum(maximum, 0.001) < 0.22) & (luma > 0.15) & (luma < 0.8)
    )
    neutral_fraction = float(np.mean(neutral))
    warmth = tint = 0.0
    if neutral_fraction >= 0.08 and np.count_nonzero(neutral) >= min(8, len(pixels)):
        red, green, blue = np.median(pixels[neutral], axis=0)
        warmth = float(
            np.clip(np.log(max(blue, 0.01) / max(red, 0.01)) * 0.65, -0.12, 0.12)
        )
        tint = float(
            np.clip(
                np.log(max(green, 0.01) / max((red + blue) / 2, 0.01)) * 0.6,
                -0.10,
                0.10,
            )
        )
        if abs(warmth) < 0.02:
            warmth = 0.0
        if abs(tint) < 0.02:
            tint = 0.0
    mean_chroma = float(np.mean(chroma))
    colorful = float(np.mean(chroma > 0.4))
    saturation = (
        -0.08
        if colorful > 0.35
        else 0.10
        if 0.005 < mean_chroma < 0.12
        else 0.04
        if mean_chroma < 0.22 and mean_chroma > 0.005
        else 0.0
    )
    notes = [
        f"{scene} luminance distribution.",
        "Lifted dark midtones."
        if exposure > 0
        else "Reduced bright midtones."
        if exposure < 0
        else "Preserved balanced exposure.",
        "Neutral midtones informed white balance."
        if warmth or tint
        else "Preserved scene color without reliable cast evidence.",
    ]
    suggestion = AutoAdjustSuggestion(
        *[
            round(float(v), 4)
            for v in (exposure, contrast, highlights, shadows, warmth, tint, saturation)
        ],
        rationale=tuple(notes),
        scene=scene,
        metrics={
            "median_luma": float(median),
            "shadow_fraction": dark_fraction,
            "highlight_fraction_before": clipped,
            "neutral_fraction": neutral_fraction,
        },
    )
    if render is None:
        return suggestion

    # Back off the complete correction until the actual renderer respects the
    # highlight budget. These small proxy renders never decode the RAW again.
    allowance = clipped + 0.005
    for amount in (1.0, 0.75, 0.5, 0.25, 0.0):
        values = {
            key: round(value * amount, 4)
            for key, value in suggestion.as_overrides().items()
        }
        candidate = _pixels(render(values))
        after = _clipping(candidate)
        if after <= allowance or amount == 0:
            notes = suggestion.rationale + (
                ("Reduced correction to protect highlights.",) if amount < 1 else ()
            )
            return replace(
                suggestion,
                **values,
                rationale=notes,
                metrics={
                    **suggestion.metrics,
                    "highlight_fraction_after": after,
                    "guard_strength": amount,
                },
            )
    return suggestion
