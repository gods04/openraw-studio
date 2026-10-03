"""Scene-aware, deterministic automatic adjustments with rendered clipping guards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from openraw_studio.raw.native.interactive import InteractivePhoto
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


@dataclass(frozen=True)
class _RenderedMetrics:
    clipping: float
    median: float
    crushed_shadows: float
    new_clipping: float
    lost_highlight_channels: int
    shadow_midtone_mean: float


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


class _RenderGuard:
    """Cached measurements in one unedited proxy's own sampling domain."""

    def __init__(self, preview, render, *, preserve_midtones):
        self.pixels = _pixels(preview)
        self.render = render
        self.weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        luma = self.pixels @ self.weights
        self.median = float(np.median(luma))
        self.clipping = _clipping(self.pixels)
        self.preserve_midtones = preserve_midtones and self.median < 0.5
        self.usable_shadows = (luma > 8 / 255) & (luma < 0.25)
        self.shadow_midtones = (luma >= 0.08) & (luma < 0.35)
        self.shadow_midtone_fraction = float(np.mean(self.shadow_midtones))
        self.shadow_midtone_mean = self._shadow_mean(luma)
        self.headroom = self.pixels <= 250 / 255
        # Channel-level headroom protects small bright subjects even if a
        # different channel at that pixel was already clipped before editing.
        self.highlight_detail = (self.pixels >= 0.60) & self.headroom
        self.highlight_channels = int(np.count_nonzero(self.highlight_detail))
        self.detail_allowance = max(2, self.highlight_channels * 0.02)
        self.cache = {}

    def _shadow_mean(self, luma):
        return float(np.mean(luma[self.shadow_midtones])) if self.shadow_midtone_fraction else 0.0

    def measure(self, values):
        key = tuple(sorted(values.items()))
        if key not in self.cache:
            candidate = _pixels(self.render(values))
            if candidate.shape != self.pixels.shape:
                raise ValueError("Auto validation render must match the baseline preview dimensions")
            candidate_luma = candidate @ self.weights
            clipped_channels = candidate >= 254 / 255
            self.cache[key] = _RenderedMetrics(
                _clipping(candidate), float(np.median(candidate_luma)),
                float(np.mean(self.usable_shadows & (candidate_luma <= 2 / 255))),
                float(np.mean(np.any(self.headroom & clipped_channels, axis=1))),
                int(np.count_nonzero(self.highlight_detail & clipped_channels)),
                self._shadow_mean(candidate_luma),
            )
        return self.cache[key]

    def tones_preserved(self, values, *, tolerance=0.01):
        checked = self.measure(values)
        return checked.crushed_shadows <= 0.005 and (
            not self.preserve_midtones or checked.median >= self.median - tolerance
        )

    def highlights_preserved(self, values):
        checked = self.measure(values)
        return (
            checked.clipping <= self.clipping + 0.005
            and checked.new_clipping <= 0.005
            and checked.lost_highlight_channels <= self.detail_allowance
        )

    def shadow_lift_useful(self, before, after):
        previous = self.measure(before).shadow_midtone_mean
        improved = self.measure(after).shadow_midtone_mean
        ceiling = min(0.30, previous + 0.04, previous * 1.25)
        return self.shadow_midtone_fraction >= 0.10 and previous + 1 / 255 < improved <= ceiling

    def metrics(self, values):
        checked = self.measure(values)
        return {
            "median_luma": self.median,
            "highlight_fraction_before": self.clipping,
            "highlight_fraction_after": checked.clipping,
            "median_luma_after": checked.median,
            "new_shadow_clipping_fraction": checked.crushed_shadows,
            "new_highlight_clipping_fraction": checked.new_clipping,
            "highlight_detail_loss_fraction": checked.lost_highlight_channels / max(1, self.highlight_channels),
            "validation_renders": float(len(self.cache)),
            "validation_pixels": float(len(self.pixels)),
            "shadow_midtone_mean_before": self.shadow_midtone_mean,
            "shadow_midtone_mean_after": checked.shadow_midtone_mean,
        }


def suggest_auto_adjustments_for_photo(photo: InteractivePhoto) -> AutoAdjustSuggestion:
    """Analyze a small linear proxy; validate display and available native samples."""
    analysis = photo.resized(256)
    original, _ = analysis.render({})
    detail = {}
    if photo.pixels.shape[:2] != analysis.pixels.shape[:2]:
        detail = {
            "detail_preview": photo.render({})[0],
            "render_detail": lambda values: photo.render(values)[0],
        }
    native = getattr(photo, "native_samples", None)
    if native is not None:
        detail.update(
            native_preview=native.render({}),
            render_native=native.render,
        )
    return suggest_auto_adjustments_from_preview(
        original, render=lambda values: analysis.render(values)[0],
        validation_strengths=(.7, .5, .25), **detail
    )


def suggest_auto_adjustments(source_path: str | Path) -> AutoAdjustSuggestion:
    preview = render_preview_image(Path(source_path), max_dimension=256)
    return suggest_auto_adjustments_from_preview(preview)


def suggest_auto_adjustments_from_preview(
    preview: PreviewRgbImage,
    *,
    render: Callable | None = None,
    detail_preview=None,
    render_detail: Callable | None = None,
    native_preview=None,
    render_native: Callable | None = None,
    validation_strengths: tuple[float, ...] = (),
) -> AutoAdjustSuggestion:
    """Analyze an unedited preview, optionally validating against the same renderer.

    The callback accepts adjustment overrides and returns RGB pixels/Pillow image.
    It must render from the original linear proxy, never from the edited preview.
    An optional finer-detail baseline/callback pair validates promising candidates
    without rendering every rejected candidate at the larger resolution.
    Native samples can additionally catch detail/noise hidden by proxy averaging;
    their biased brightness distribution never sets the scene's midtone target.
    Additional strength samples check the nonlinear exposure/highlight interaction
    when a candidate relies on highlight compression. Full strength is always checked.
    A highlight-limited result can receive bounded shadow refinement, measured on
    the same original shadow midtones and revalidated at both proxy resolutions.
    This is a local heuristic, not a trained AI model or semantic scene classifier.
    """
    if (detail_preview is None) != (render_detail is None) or (render_detail is not None and render is None):
        raise ValueError("Detail validation requires a baseline, a detail renderer, and the analysis renderer")
    if (native_preview is None) != (render_native is None) or (render_native is not None and render is None):
        raise ValueError("Native validation requires a baseline, a native renderer, and the analysis renderer")
    if any(not np.isfinite(value) or not 0 < value <= 1 for value in validation_strengths):
        raise ValueError("Validation strengths must be finite and within (0, 1]")
    validation_strengths = tuple(dict.fromkeys(value for value in validation_strengths if value != 1))
    if validation_strengths and render is None:
        raise ValueError("Strength validation requires a renderer")
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
    backlit = p10 < 0.10 and p99 > 0.84 and median < 0.4
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
    requested_exposure = exposure
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
    dominant_channel_fraction = float(
        np.bincount(np.argmax(pixels, axis=1), minlength=3).max() / len(pixels)
    )
    color_dominated = dominant_channel_fraction > 0.85 and neutral_fraction < 0.50
    warmth = tint = 0.0
    if not color_dominated and neutral_fraction >= 0.08 and np.count_nonzero(neutral) >= min(8, len(pixels)):
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
            "dominant_channel_fraction": dominant_channel_fraction,
        },
    )
    if render is None:
        return suggestion

    primary = _RenderGuard(preview, render, preserve_midtones=exposure >= 0 and not low_key)
    guards = [primary]
    prefixes = [""]
    if render_native is not None:
        guards.append(_RenderGuard(native_preview, render_native, preserve_midtones=False))
        prefixes.append("native_")
    if render_detail is not None:
        guards.append(_RenderGuard(
            detail_preview, render_detail, preserve_midtones=exposure >= 0 and not low_key
        ))
        prefixes.append("detail_")

    def tones_preserved(values, *, tolerance=0.01):
        return all(
            guard.tones_preserved(values, tolerance=tolerance) for guard in guards
        )

    def highlights_preserved(values):
        # Compression can protect the endpoint while weaker settings still clip.
        # Reject on the small proxy before spending work at display resolution.
        samples = [values]
        if values["highlights"] < 0:
            samples.extend(
                {key: value * strength for key, value in values.items()}
                for strength in validation_strengths
            )
        for guard in guards:
            for index, candidate in enumerate(samples):
                if not guard.highlights_preserved(candidate) or (index and not guard.tones_preserved(candidate)):
                    return False
        return True

    def refine_shadows(values):
        # Once highlights limit exposure, recover a little usable shadow detail
        # without moving exposure or lifting a predominantly dark scene.
        before = primary.measure(values)
        if (
            low_key or dark_fraction >= 0.55 or requested_exposure <= values["exposure"] + 0.15
            or before.median >= 0.35 or primary.shadow_midtone_fraction < 0.10
            or before.shadow_midtone_mean >= 0.30
        ):
            return values
        for increment in (0.40, 0.20):
            candidate = {**values, "shadows": round(min(0.65, values["shadows"] + increment), 4)}
            if candidate["shadows"] <= values["shadows"]:
                continue
            if not primary.shadow_lift_useful(values, candidate):
                continue
            if (
                highlights_preserved(candidate) and tones_preserved(candidate)
                and all(
                    guard.shadow_lift_useful(values, candidate)
                    for guard, prefix in zip(guards[1:], prefixes[1:])
                    if prefix != "native_"
                )
            ):
                return candidate
        return values

    # Positive contrast can undo an exposure lift and clip dim subjects. Test
    # that component first, retaining the other corrections where possible.
    initial_values = suggestion.as_overrides()
    contrast_guarded = False
    if suggestion.contrast > 0 and not tones_preserved(initial_values):
        for fraction in (0.5, 0.0):
            values = {**initial_values, "contrast": round(suggestion.contrast * fraction, 4)}
            if tones_preserved(values) or fraction == 0:
                suggestion = replace(suggestion, contrast=values["contrast"])
                initial_values = values
                contrast_guarded = True
                break

    exposure_guarded = False
    highlights_guarded = False

    def recover_tones():
        fractions = (1.0, 0.75, 0.5, 0.25, 0.125, 0.0) if suggestion.exposure > 0 else (1.0,)
        contrasts = (suggestion.contrast, 0.0) if suggestion.contrast > 0 else (suggestion.contrast,)
        for fraction in fractions:
            for candidate_contrast in contrasts:
                values = {
                    **initial_values, "exposure": round(suggestion.exposure * fraction, 4),
                    "contrast": candidate_contrast,
                }
                # A recovered correction must not darken dim midtones. Contrast
                # that worked before limiting exposure may now defeat the lift.
                if not primary.tones_preserved(values, tolerance=1e-6):
                    continue
                if highlights_preserved(values) and tones_preserved(values, tolerance=1e-6):
                    return values
                for candidate_highlights in (-0.16, -0.30):
                    if candidate_highlights >= suggestion.highlights:
                        continue
                    compressed = {**values, "highlights": candidate_highlights}
                    if highlights_preserved(compressed) and tones_preserved(compressed, tolerance=1e-6):
                        return compressed
        return None

    if not highlights_preserved(initial_values):
        values = recover_tones()
        if values is not None:
            exposure_guarded = values["exposure"] != suggestion.exposure
            highlights_guarded = values["highlights"] != suggestion.highlights
            contrast_guarded |= values["contrast"] != suggestion.contrast
            suggestion = replace(suggestion, **values)

    # Back off the complete correction until rendered highlights AND dark
    # subjects stay inside their budgets. Proxy renders do not decode RAW again.
    for amount in (1.0, 0.75, 0.5, 0.25, 0.0):
        values = {
            key: round(value * amount, 4)
            for key, value in suggestion.as_overrides().items()
        }
        if (highlights_preserved(values) and tones_preserved(values)) or amount == 0:
            refined = refine_shadows(values) if amount else values
            shadows_refined = refined["shadows"] > values["shadows"]
            values = refined
            notes = suggestion.rationale + (
                ("Reduced contrast to preserve dark subjects.",) if contrast_guarded else ()
            ) + (
                ("Limited exposure to preserve highlight detail.",) if exposure_guarded else ()
            ) + (
                ("Compressed highlights to retain a useful tonal correction.",) if highlights_guarded else ()
            ) + (
                ("Reduced correction to protect highlights and shadows.",) if amount < 1 else ()
            ) + (
                ("Lifted usable shadows after limiting global exposure.",) if shadows_refined else ()
            )
            metrics = primary.metrics(values)
            for guard, prefix in zip(guards[1:], prefixes[1:]):
                metrics.update({f"{prefix}{key}": value for key, value in guard.metrics(values).items()})
            return replace(
                suggestion,
                **values,
                rationale=notes,
                metrics={
                    **suggestion.metrics,
                    **metrics,
                    "contrast_guarded": float(contrast_guarded),
                    "exposure_guarded": float(exposure_guarded),
                    "highlights_guarded": float(highlights_guarded),
                    "shadows_refined": float(shadows_refined),
                    "guard_strength": amount,
                    "validated_strength_samples": float(1 + len(validation_strengths) if values["highlights"] < 0 else 1),
                },
            )
    return suggestion
