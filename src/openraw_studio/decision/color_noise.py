"""Sampled rendered-chroma advice, not a calibrated sensor noise estimator.

Low-frequency structure and periodicity reject many textured regions. Random
color texture remains intrinsically ambiguous, so advice is bounded and explicit.
"""

from dataclasses import dataclass, field

import numpy as np

from openraw_studio.decision.auto_adjust import _RenderGuard


@dataclass(frozen=True)
class ColorNoiseSuggestion:
    strength: float | None
    status: str
    metrics: dict[str, float] = field(default_factory=dict)


def _blocks(tiles):
    count, height, width, _ = tiles.shape
    return (
        tiles[:, : height // 16 * 16, : width // 16 * 16]
        .reshape(count, height // 16, 16, width // 16, 16, 3)
        .transpose(0, 1, 3, 2, 4, 5)
        .reshape(-1, 16, 16, 3)
        .astype(np.float32)
    )


def _signals(blocks):
    red, green, blue = (blocks[..., c] for c in range(3))
    return np.stack(
        (red - green, blue - green, (54 * red + 183 * green + 19 * blue) / 256),
        axis=-1,
    )


def _residuals(signal):
    axis = np.arange(16, dtype=np.float32) - 7.5
    sx = np.mean(signal * axis[None, None, :, None], axis=(1, 2)) / np.mean(axis**2)
    sy = np.mean(signal * axis[None, :, None, None], axis=(1, 2)) / np.mean(axis**2)
    return (
        signal
        - np.mean(signal, axis=(1, 2), keepdims=True)
        - sx[:, None, None, :] * axis[None, None, :, None]
        - sy[:, None, None, :] * axis[None, :, None, None]
    )


def _dispersion(residual):
    sigma = (
        np.median(
            np.abs(residual - np.median(residual, axis=(1, 2), keepdims=True)),
            axis=(1, 2),
        )
        * 1.4826
    )
    return np.sqrt(np.mean(sigma[:, :2] ** 2, axis=1)), sigma[:, 2]


def _coarse(signal):
    return signal.reshape(-1, 4, 4, 4, 4, 3).mean(axis=(2, 4))


def _sample_mask(blocks, signal, residual, dispersion, luma_dispersion):
    usable = (
        np.mean(
            (blocks.min(axis=-1) > 3)
            & (blocks.max(axis=-1) < 252)
            & (signal[..., 2] > 20)
            & (signal[..., 2] < 220),
            axis=(1, 2),
        )
        >= 0.95
    )
    coarse = _coarse(residual)
    chroma = np.sqrt(np.mean(coarse[..., :2] ** 2, axis=(1, 2, 3)))
    luma = np.sqrt(np.mean(coarse[..., 2] ** 2, axis=(1, 2)))
    correlations = []
    for left, right in (
        (residual[:, :-4, :, :2], residual[:, 4:, :, :2]),
        (residual[:, :, :-4, :2], residual[:, :, 4:, :2]),
    ):
        magnitude = np.sqrt(
            np.sum(left * left, axis=(1, 2, 3)) * np.sum(right * right, axis=(1, 2, 3))
        )
        correlations.append(
            np.abs(np.sum(left * right, axis=(1, 2, 3))) / np.maximum(1, magnitude)
        )
    return (
        usable
        & (chroma <= np.maximum(1, dispersion * 0.45))
        & (luma <= np.maximum(2, luma_dispersion * 0.45))
        & (np.maximum(*correlations) < 0.45)
    )


def _region_median(values, groups, mask):
    # Each sampled region has one vote, regardless of its number of flat blocks.
    return float(
        np.median(
            [
                np.median(values[mask & (groups == group)])
                for group in np.unique(groups[mask])
            ]
        )
    )


def suggest_color_noise_from_tiles(tiles, render, *, grid_count):
    """Select a bounded global amount using native grid tiles and tone guards.

    ``render(amount)`` must return the same native tiles at the requested color
    filtering strength, retaining the caller's other adjustments. Extra bright
    tiles are used only for clipping checks, not the noise/texture vote.
    """
    if (
        not isinstance(tiles, np.ndarray)
        or tiles.dtype != np.uint8
        or tiles.ndim != 4
        or tiles.shape[-1] != 3
        or not isinstance(grid_count, (int, np.integer))
        or not 0 < grid_count <= min(64, len(tiles))
        or len(tiles) > 128
        or min(tiles.shape[1:3]) < 1
        or max(tiles.shape[1:3]) > 32
    ):
        raise ValueError("Color-noise analysis requires bounded native RGB8 tiles")
    if min(tiles.shape[1:3]) < 16:
        return ColorNoiseSuggestion(None, "insufficient-samples")
    blocks = _blocks(tiles[:grid_count])
    groups = np.repeat(np.arange(grid_count), len(blocks) // grid_count)
    signal = _signals(blocks)
    residual = _residuals(signal)
    dispersion, luma_dispersion = _dispersion(residual)
    mask = _sample_mask(blocks, signal, residual, dispersion, luma_dispersion)
    metrics = {
        "sample_blocks": float(len(blocks)),
        "usable_blocks": float(mask.sum()),
        "usable_regions": float(len(np.unique(groups[mask]))),
    }
    if metrics["usable_blocks"] < 12 or metrics["usable_regions"] < 6:
        return ColorNoiseSuggestion(None, "insufficient-samples", metrics)
    before = _region_median(dispersion, groups, mask)
    metrics["chroma_dispersion_before"] = before
    if before <= 2.5:
        return ColorNoiseSuggestion(0, "low-noise", metrics)

    cache = {}

    def filtered(values):
        amount = values["color_noise"]
        if amount not in cache:
            result = np.asarray(render(amount))
            if result.shape != tiles.shape or result.dtype != np.uint8:
                raise ValueError(
                    "Color-noise validation must preserve native tile shape and RGB8 type"
                )
            cache[amount] = result
        return cache[amount].reshape(-1, 3)

    guard = _RenderGuard(tiles.reshape(-1, 3), filtered, preserve_midtones=False)
    requested = round(float(np.clip((before - 2.5) / 12, 0.2, 0.7)) / 0.05) * 0.05
    baseline_coarse = _coarse(signal)[..., :2]
    baseline_luma = _signals(tiles.astype(np.float32))[..., 2]
    for amount in (round(requested, 2), round(requested / 2, 2)):
        values = {"color_noise": amount}
        if not guard.highlights_preserved(values) or not guard.tones_preserved(values):
            continue
        after_signal = _signals(_blocks(cache[amount][:grid_count]))
        after_dispersion, _ = _dispersion(_residuals(after_signal))
        after = _region_median(after_dispersion, groups, mask)
        drift = np.sqrt(
            np.mean((_coarse(after_signal)[..., :2] - baseline_coarse) ** 2, axis=-1)
        )
        # In accepted flat regions, reducing noisy 4x4 color averages is useful.
        # Guard structure elsewhere, and mean color in every 16x16 block.
        drift95, drift99 = (
            np.quantile(drift[~mask], (0.95, 0.99)) if np.any(~mask) else (0, 0)
        )
        mean_drift = float(
            np.quantile(
                np.abs(np.mean(after_signal[..., :2] - signal[..., :2], axis=(1, 2))),
                0.99,
            )
        )
        filtered_signal = _signals(cache[amount].astype(np.float32))
        luma_error = float(np.max(np.abs(filtered_signal[..., 2] - baseline_luma)))
        if (
            after <= before * 0.95
            and drift95 <= 1.5
            and drift99 <= 3
            and mean_drift <= 1
            and luma_error <= 0.5
        ):
            return ColorNoiseSuggestion(
                amount,
                "suggested",
                {
                    **metrics,
                    "chroma_dispersion_after": after,
                    "coarse_color_drift_p95": float(drift95),
                    "coarse_color_drift_p99": float(drift99),
                    "mean_color_drift_p99": mean_drift,
                    "max_luma_error": luma_error,
                    **guard.metrics(values),
                },
            )
    return ColorNoiseSuggestion(None, "no-safe-benefit", metrics)


def suggest_color_noise_for_photo(photo, adjustments):
    """Reuse retained Nikon samples, with no new RAW decode or proxy noise guess."""
    samples = getattr(photo, "native_samples", None)
    if samples is None:
        return ColorNoiseSuggestion(None, "native-samples-unavailable")
    values = {**adjustments, "color_noise": 0}
    return suggest_color_noise_from_tiles(
        samples.render_tiles(values),
        lambda amount: samples.render_tiles({**values, "color_noise": amount}),
        grid_count=samples.grid_count,
    )
