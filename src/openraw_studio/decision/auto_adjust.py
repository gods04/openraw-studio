"""Scene-aware, deterministic automatic adjustments with rendered clipping guards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from openraw_studio.decision.white_balance import NeutralCast, refine_white_balance
from openraw_studio.decision.ambient_color import analyze_ambient_color
from openraw_studio.decision.scene_color import refine_scene_color
from openraw_studio.decision.tonal_intent import dark_scene_intent, limit_dark_lift, little_visible_detail
from openraw_studio.vision.scene import SceneEvidence, analyze_scene
from openraw_studio.vision.person import PersonAnalysis, PersonEvidence, analyze_person
from openraw_studio.raw.native.interactive import InteractivePhoto
from openraw_studio.raw.native.cpu_tone_batch import cpu_tone_batch
from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.tone import PreviewRgbImage


_SHOULDER_STRENGTHS = (.10, .15, .20, .35)


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
    scene_evidence: SceneEvidence | None = None
    person_evidence: PersonEvidence | None = None

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
    shadow_risk: float
    new_clipping: float
    lost_highlight_channels: int
    shadow_midtone_mean: float
    neutral_bias: np.ndarray | None


def _pixels(preview):
    pixels = np.asarray(preview.pixels if isinstance(preview, PreviewRgbImage) else preview)
    if pixels.size == 0 or pixels.ndim < 2 or pixels.shape[-1] != 3:
        raise ValueError("Preview must contain finite RGB pixels")
    if pixels.dtype == np.uint8:
        # Rendered RGB8 is finite and bounded already; allocate only the result.
        return np.divide(pixels.reshape(-1, 3), np.float32(255), dtype=np.float32)
    pixels = np.asarray(pixels, dtype=np.float32)
    if not np.isfinite(pixels).all():
        raise ValueError("Preview must contain finite RGB pixels")
    return np.clip(pixels.reshape(-1, 3) / 255, 0, 1)


def _any_rgb(channels):
    return channels[:, 0] | channels[:, 1] | channels[:, 2]


def _clipping(pixels):
    return float(np.mean(_any_rgb(pixels >= 254 / 255)))


def _limit_contrast(values, preserved):
    """Fit a checked contrast; the caller still validates the whole correction."""
    high = values["contrast"]
    if high <= 0 or preserved(values):
        return values
    safe = {**values, "contrast": 0.0}
    if not preserved(safe):
        return safe
    low = 0.0
    for _ in range(5):
        middle = round((low + high) / 2, 4)
        candidate = {**values, "contrast": middle}
        if preserved(candidate):
            low, safe = middle, candidate
        else:
            high = middle
    return safe


class _RenderGuard:
    """Cached measurements in one unedited proxy's own sampling domain."""

    def __init__(self, preview, render, *, preserve_midtones, balance=False, ambient=None, shadow_margin=False, lighting=None):
        self.pixels = _pixels(preview)
        self.render = render
        self.neutral = NeutralCast(preview, ambient=ambient) if balance else None
        self.weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        luma = self.pixels @ self.weights
        self.median = float(np.median(luma))
        self.intent = dark_scene_intent(luma, lighting)
        self.clipping = _clipping(self.pixels)
        self.preserve_midtones = preserve_midtones and self.median < 0.5
        self.usable_shadows = (luma > 8 / 255) & (luma < 0.25)
        self.shadow_margin = shadow_margin
        self.usable_shadow_fraction = float(np.mean(self.usable_shadows))
        self.shadow_midtones = (luma >= 0.08) & (luma < 0.35)
        self.shadow_midtone_fraction = float(np.mean(self.shadow_midtones))
        self.shadow_midtone_mean = self._shadow_mean(luma)
        self.preserve_shadow_midtones = preserve_midtones and self.shadow_midtone_fraction >= 0.10
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
            crushed = float(np.mean(self.usable_shadows & (candidate_luma <= 2 / 255)))
            risk = crushed
            if self.shadow_margin and self.usable_shadow_fraction:
                # RGB8 quantization and proxy smoothing can hide an incipient
                # black threshold crossing. Count the next two codes softly.
                near_black = np.clip((4 / 255 - candidate_luma[self.usable_shadows]) * (255 / 2), 0, 1)
                risk = max(crushed, float(near_black.sum(dtype=np.float64) / len(candidate_luma)))
            self.cache[key] = _RenderedMetrics(
                float(np.mean(_any_rgb(clipped_channels))), float(np.median(candidate_luma)),
                crushed, risk,
                float(np.mean(_any_rgb(self.headroom & clipped_channels))),
                int(np.count_nonzero(self.highlight_detail & clipped_channels)),
                self._shadow_mean(candidate_luma),
                self.neutral.measure(candidate) if self.neutral is not None else None,
            )
        return self.cache[key]

    def tones_preserved(self, values, *, tolerance=0.01):
        checked = self.measure(values)
        # Bright clothing/background can raise the median while contrast still
        # darkens a substantial dim subject. Follow the original shadow pixels.
        return checked.shadow_risk <= 0.005 and (
            not self.preserve_midtones or checked.median >= self.median - tolerance
        ) and (
            not self.preserve_shadow_midtones
            or checked.shadow_midtone_mean >= self.shadow_midtone_mean - tolerance
        ) and self.intent_preserved(values)

    def intent_preserved(self, values):
        return self.intent is None or self.measure(values).median <= self.intent.median_ceiling

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
            **({"dark_scene_weight": self.intent.weight,
                "dark_scene_median_ceiling": self.intent.median_ceiling} if self.intent is not None else {}),
            "new_shadow_clipping_fraction": checked.crushed_shadows,
            **({"shadow_clipping_risk": checked.shadow_risk} if self.shadow_margin else {}),
            "new_highlight_clipping_fraction": checked.new_clipping,
            "highlight_detail_loss_fraction": checked.lost_highlight_channels / max(1, self.highlight_channels),
            "validation_renders": float(len(self.cache)),
            "validation_pixels": float(len(self.pixels)),
            "shadow_midtone_mean_before": self.shadow_midtone_mean,
            "shadow_midtone_mean_after": checked.shadow_midtone_mean,
            "shadow_midtone_fraction": self.shadow_midtone_fraction,
        }


@cpu_tone_batch()
def suggest_auto_adjustments_for_photo(
    photo: InteractivePhoto, *, person_analysis: PersonAnalysis | None = None,
) -> AutoAdjustSuggestion:
    """Analyze a small linear proxy; validate display and available native samples.

    A caller may retain person analysis from this same photo's unedited render
    for inspection, supplying it here to avoid running inference a second time.
    """
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
    scene_image = detail.get("detail_preview", original)
    return suggest_auto_adjustments_from_preview(
        original, render=lambda values: analysis.render(values)[0],
        validation_strengths=(.7, .5, .25, *_SHOULDER_STRENGTHS),
        scene_evidence=analyze_scene(scene_image),
        person=person_analysis if person_analysis is not None else analyze_person(scene_image), **detail
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
    scene_evidence: SceneEvidence | None = None,
    person=None,
) -> AutoAdjustSuggestion:
    """Analyze an unedited preview, optionally validating against the same renderer.

    The callback accepts adjustment overrides and returns RGB pixels/Pillow image.
    It must render from the original linear proxy, never from the edited preview.
    An optional finer-detail baseline/callback pair validates promising candidates
    without rendering every rejected candidate at the larger resolution.
    Native samples can additionally catch detail/noise hidden by proxy averaging;
    their biased brightness distribution never sets the scene's midtone target.
    Additional strength samples check nonlinear exposure/highlight interactions
    and contrast applied to substantial shadow midtones. Full strength is always checked.
    A highlight-limited result can receive bounded shadow refinement, measured on
    the same original shadow midtones and revalidated at both proxy resolutions.
    Still-dim scenes can recover bounded exposure with additional compression;
    these candidates also check low/intermediate-strength shoulder peaks.
    Optional local model evidence can refine color after the tonal safety checks.
    Without it, this remains the existing pixel-statistics heuristic.
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
    if little_visible_detail(preview) and (detail_preview is None or little_visible_detail(detail_preview)):
        return AutoAdjustSuggestion(
            0., 0., 0., 0., 0., 0., 0.,
            rationale=("Too little visible tonal information for a reliable automatic correction.",),
            scene="Low visible detail",
            scene_evidence=replace(scene_evidence, status="insufficient-information") if scene_evidence is not None else None,
            person_evidence=person.evidence if person is not None else None,
            metrics={"insufficient_tonal_information": 1., "median_luma": float(median)},
        )
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
    intent = dark_scene_intent(luma, scene_evidence)
    intent_target = intent is not None and midtone + intent.lift < target
    if intent is not None:
        target = min(target, midtone + intent.lift)
    metering_floor = 1 / 255 if intent_target else .025
    exposure = float(np.clip(2.2 * np.log2(target / max(midtone, metering_floor)), -0.8, 1.2))
    if abs(midtone - target) < (2 / 255 if intent_target else .035):
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
    ambient = analyze_ambient_color(preview, scene_evidence, person)
    warmth = tint = 0.0
    if not color_dominated and neutral_fraction >= 0.08 and np.count_nonzero(neutral) >= min(8, len(pixels)):
        red, green, blue = np.median(pixels[neutral], axis=0)
        if ambient is not None:
            bias = np.log(np.maximum([red, blue], .01) / max(green, .01))
            retained = ambient.retain(bias)
            red, blue = np.array([red, blue]) / np.exp(retained)
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
    if ambient is not None:
        notes.append("Spatial color evidence and lighting context limited neutralization of ambient color.")
    if intent is not None:
        notes.append("Dark lighting evidence and measured tones limited the intended brightness lift.")
    suggestion = AutoAdjustSuggestion(
        *[
            round(float(v), 4)
            for v in (exposure, contrast, highlights, shadows, warmth, tint, saturation)
        ],
        rationale=tuple(notes),
        scene=scene,
        scene_evidence=scene_evidence,
        person_evidence=person.evidence if person is not None else None,
        metrics={
            "median_luma": float(median),
            "shadow_fraction": dark_fraction,
            "highlight_fraction_before": clipped,
            "neutral_fraction": neutral_fraction,
            "dominant_channel_fraction": dominant_channel_fraction,
            **({'ambient_color_retention': ambient.weight, 'ambient_color_tiles': float(ambient.tiles),
                'ambient_color_fraction': ambient.fraction} if ambient is not None else {}),
        },
    )
    if render is None:
        return suggestion

    primary = _RenderGuard(preview, render, preserve_midtones=exposure >= 0 and not low_key,
                           balance=not low_key, ambient=ambient, shadow_margin=True, lighting=scene_evidence)
    guards = [primary]
    prefixes = [""]
    if render_native is not None:
        guards.append(_RenderGuard(native_preview, render_native, preserve_midtones=False, shadow_margin=True))
        prefixes.append("native_")
    if render_detail is not None:
        guards.append(_RenderGuard(
            detail_preview, render_detail, preserve_midtones=exposure >= 0 and not low_key,
            balance=primary.neutral is not None and primary.neutral.mask is not None,
            ambient=ambient,
            shadow_margin=True,
            lighting=scene_evidence,
        ))
        prefixes.append("detail_")

    def tones_preserved(values, *, tolerance=0.01):
        return all(
            guard.tones_preserved(values, tolerance=tolerance) for guard in guards
        )

    def needs_strength_checks(values):
        return (intent is not None and (values["exposure"] > 0 or values["shadows"] > 0)) or values["highlights"] < 0 or (
            values["contrast"] > 0 and any(
                guard.preserve_shadow_midtones or guard.usable_shadow_fraction > .005 for guard in guards
            )
        )

    # More exposure/compression can have a clipping peak below the usual 25%
    # strength sample, even when both the original and full correction are safe.
    recovery_strengths = tuple(dict.fromkeys((*validation_strengths, *_SHOULDER_STRENGTHS)))

    def highlights_preserved(values, *, all_strengths=False, strengths=None):
        # Compression can protect the endpoint while weaker settings still clip.
        # Reject on the small proxy before spending work at display resolution.
        samples = [values]
        if all_strengths or needs_strength_checks(values):
            samples.extend(
                {key: value * strength for key, value in values.items()}
                for strength in (validation_strengths if strengths is None else strengths)
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

    def refine_exposure(values):
        # The upper-tail exposure estimate precedes rendered highlight recovery.
        # Revisit that limit only for still-dim scenes with usable shadow tones.
        before = primary.measure(values)
        if (
            low_key or dark_fraction >= 0.55 or values["highlights"] > -0.16
            or requested_exposure <= values["exposure"] + 0.15
            or before.median >= min(0.35, target - 0.07)
            or primary.shadow_midtone_fraction < 0.10
        ):
            return values

        def useful(guard, candidate):
            old, new = guard.measure(values), guard.measure(candidate)
            ceiling = min(0.38, old.shadow_midtone_mean + 0.05, old.shadow_midtone_mean * 1.25)
            return (
                old.median + 2 / 255 <= new.median <= min(target, old.median + 0.06)
                and old.shadow_midtone_mean + 1 / 255 <= new.shadow_midtone_mean <= ceiling
            )

        for increment in (0.40, 0.20):
            exposure = round(min(requested_exposure, values["exposure"] + increment), 4)
            for highlights in dict.fromkeys((values["highlights"], -0.50)):
                candidate = {**values, "exposure": exposure, "highlights": highlights}
                if not useful(primary, candidate):
                    continue
                if (
                    highlights_preserved(candidate, all_strengths=True, strengths=recovery_strengths)
                    and tones_preserved(candidate)
                    and all(
                        useful(guard, candidate)
                        for guard, prefix in zip(guards[1:], prefixes[1:]) if prefix != "native_"
                    )
                ):
                    return candidate
        return values

    initial_values = suggestion.as_overrides()
    intent_values = limit_dark_lift(
        initial_values, lambda v: all(g.intent_preserved(v) for g in guards),
    ) if intent is not None else initial_values
    intent_refined = intent_values != initial_values
    if intent_refined:
        suggestion = replace(suggestion, **intent_values)
        initial_values = intent_values
    # Positive contrast can undo an exposure lift and clip dim subjects. Test
    # that component first, retaining the other corrections where possible.
    limited = _limit_contrast(initial_values, tones_preserved)
    contrast_guarded = limited["contrast"] != suggestion.contrast
    if contrast_guarded:
        suggestion = replace(suggestion, contrast=limited["contrast"])
        initial_values = limited

    exposure_guarded = False
    highlights_guarded = False
    saturation_guarded = False

    def recover_tones():
        fractions = (1.0, 0.75, 0.5, 0.25, 0.125, 0.0) if suggestion.exposure > 0 else (1.0,)
        contrasts = (suggestion.contrast, 0.0) if suggestion.contrast > 0 else (suggestion.contrast,)
        for fraction in fractions:
            color_candidate = None
            for candidate_contrast in contrasts:
                values = {
                    **initial_values, "exposure": round(suggestion.exposure * fraction, 4),
                    "contrast": candidate_contrast,
                }
                # A recovered correction must not darken dim midtones. Contrast
                # that worked before limiting exposure may now defeat the lift.
                if not primary.tones_preserved(values, tolerance=1e-6):
                    continue
                color_candidate = values
                if highlights_preserved(values) and tones_preserved(values, tolerance=1e-6):
                    return values
                for candidate_highlights in (-0.16, -0.30, -0.50):
                    if candidate_highlights >= suggestion.highlights:
                        continue
                    compressed = {**values, "highlights": candidate_highlights}
                    color_candidate = compressed
                    if highlights_preserved(compressed) and tones_preserved(compressed, tolerance=1e-6):
                        return compressed
            # A small saturation boost can consume the headroom needed for a
            # useful exposure lift. Test one unboosted candidate per exposure,
            # using the strongest compression/lowest contrast already tried.
            if color_candidate is not None and color_candidate["saturation"] > 0:
                unboosted = {**color_candidate, "saturation": 0.0}
                if highlights_preserved(unboosted) and tones_preserved(unboosted, tolerance=1e-6):
                    more_contrast = {**unboosted, "contrast": suggestion.contrast}
                    if highlights_preserved(more_contrast) and tones_preserved(more_contrast, tolerance=1e-6):
                        unboosted = more_contrast
                    less_compressed = {**unboosted, "highlights": suggestion.highlights}
                    if highlights_preserved(less_compressed) and tones_preserved(less_compressed, tolerance=1e-6):
                        unboosted = less_compressed
                    half_boost = {**unboosted, "saturation": round(suggestion.saturation * 0.5, 4)}
                    if highlights_preserved(half_boost) and tones_preserved(half_boost, tolerance=1e-6):
                        return half_boost
                    return unboosted
        return None

    if not highlights_preserved(initial_values):
        values = recover_tones()
        if values is not None:
            exposure_guarded = values["exposure"] != suggestion.exposure
            highlights_guarded = values["highlights"] != suggestion.highlights
            saturation_guarded = values["saturation"] != suggestion.saturation
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
            refined = refine_exposure(values) if amount else values
            exposure_refined = refined["exposure"] > values["exposure"]
            values = refined
            final_strengths = recovery_strengths if exposure_refined else validation_strengths
            balance_metrics = {"white_balance_refined": 0.0}
            if amount and not low_key:
                values, balance_metrics = refine_white_balance(
                    primary.neutral, lambda candidate: primary.measure(candidate).neutral_bias, values,
                    lambda candidate: highlights_preserved(candidate, all_strengths=True, strengths=final_strengths) and tones_preserved(candidate),
                    detail_evidence=guards[-1].neutral if render_detail is not None else None,
                    measure_detail=(lambda candidate: guards[-1].measure(candidate).neutral_bias) if render_detail is not None else None,
                    validation_strengths=final_strengths,
                )
            values, scene_metrics = refine_scene_color(
                preview, render, values, scene_evidence,
                lambda candidate: highlights_preserved(candidate, all_strengths=True, strengths=final_strengths) and tones_preserved(candidate),
                detail_preview=detail_preview, render_detail=render_detail,
                validation_strengths=final_strengths,
                person=person,
            )
            notes = suggestion.rationale + (
                ("Fitted exposure and shadow lift to the rendered dark-scene reference.",) if intent_refined else ()
            ) + (
                ("Reduced contrast to preserve dark subjects.",) if contrast_guarded else ()
            ) + (
                ("Limited exposure to preserve highlight detail.",) if exposure_guarded else ()
            ) + (
                ("Compressed highlights to retain a useful tonal correction.",) if highlights_guarded else ()
            ) + (
                ("Reduced added saturation to retain useful tones and highlight detail.",) if saturation_guarded else ()
            ) + (
                ("Reduced correction to protect highlights and shadows.",) if amount < 1 else ()
            ) + (
                ("Lifted usable shadows after limiting global exposure.",) if shadows_refined else ()
            ) + (
                ("Recovered midtones within rendered highlight limits.",) if exposure_refined else ()
            ) + (
                ("Refined a consistent near-neutral cast using measured renderer response.",) if balance_metrics["white_balance_refined"] else ()
            ) + (
                ("Refined content-conditioned color using measured renderer response.",) if scene_metrics["scene_color_refined"] else ()
            )
            metrics = primary.metrics(values)
            for guard, prefix in zip(guards[1:], prefixes[1:]):
                metrics.update({f"{prefix}{key}": value for key, value in guard.metrics(values).items()})
            return replace(
                suggestion,
                **values,
                rationale=notes,
                scene=f"{scene_evidence.scene} / {scene_evidence.lighting}" if scene_evidence is not None and scene_evidence.status == "ready" else suggestion.scene,
                metrics={
                    **suggestion.metrics,
                    **metrics,
                    **balance_metrics,
                    **scene_metrics,
                    "contrast_guarded": float(contrast_guarded),
                    **({"dark_scene_lift_refined": float(intent_refined)} if intent is not None else {}),
                    "exposure_guarded": float(exposure_guarded),
                    "highlights_guarded": float(highlights_guarded),
                    "saturation_guarded": float(saturation_guarded),
                    "shadows_refined": float(shadows_refined),
                    "exposure_refined": float(exposure_refined),
                    "guard_strength": amount,
                    "validated_strength_samples": float(1 + len(final_strengths) if needs_strength_checks(values) or balance_metrics["white_balance_refined"] or scene_metrics["scene_color_refined"] else 1),
                },
            )
    return suggestion
