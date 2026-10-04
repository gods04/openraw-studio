"""Measured dark-scene headroom, not a fixed exposure preset for a label."""

from dataclasses import dataclass

import numpy as np

from openraw_studio.decision.white_balance import _rgb


@dataclass(frozen=True)
class DarkSceneIntent:
    weight: float
    lift: float
    median_ceiling: float


def dark_scene_intent(luma, evidence):
    if evidence is None or evidence.status != "ready":
        return None
    median = float(np.median(luma))
    ambient = sum(evidence.lights.get(key, 0) for key in ("Night", "Colored light"))
    weight = float(np.clip((ambient - .4) / .5, 0, 1)
                   * np.clip(evidence.reliability * 2, 0, 1)
                   * np.clip((.45 - median) / .20, 0, 1))
    if weight <= 0:
        return None
    lower, upper = np.quantile(luma, [.25, .75])
    # Permit relative fill while retaining the measured dark reference. Weak
    # semantic corroboration relaxes this limit continuously toward no limit.
    lift = max(3 / 255, median * .15, float(upper - lower) * .10) / weight
    return DarkSceneIntent(weight, lift, median + lift)


def little_visible_detail(image):
    """Conservative preview abstention; not a claim that the RAW has no signal."""
    rgb = _rgb(image)
    if rgb.ndim != 3 or min(rgb.shape[:2]) < 16:
        return False
    luma = rgb @ np.array([.2126, .7152, .0722], np.float32) / 255
    if not np.isfinite(luma).all():
        raise ValueError("Preview must contain finite RGB pixels")
    if np.mean(np.any(rgb > 16, axis=-1)) >= .0001:
        return False
    lower, upper = np.quantile(luma, [.05, .99])
    if upper >= 8 / 255 or upper - lower >= 4 / 255 or np.mean(luma > 8 / 255) >= .0001:
        return False
    spatial = np.minimum(luma, 8 / 255)
    tiles = [float(np.mean(tile)) for band in np.array_split(spatial, 8)
             for tile in np.array_split(band, 8, axis=1)]
    # Spatial structure can justify a lift even below the nominal usable band;
    # a nearly uniform dark field with isolated hot pixels does not.
    return float(np.ptp(tiles)) < 1 / 255


def dark_noise_evidence(preview, detail_preview=None):
    """Conservative RGB8 preview evidence, not an estimate of sensor SNR.

    Averaging can suppress noise in the analysis proxy. Require weak structure
    in both domains, but measure noise dominance in the finest supplied image.
    """
    images = (preview,) if detail_preview is None else (preview, detail_preview)
    measured = []
    for image in images:
        rgb = _rgb(image)
        if rgb.ndim != 3 or min(rgb.shape[:2]) < 64:
            return None
        if not np.isfinite(rgb).all():
            raise ValueError("Preview must contain finite RGB pixels")
        luma = rgb @ np.array([.2126, .7152, .0722], np.float32)
        if np.quantile(luma, .99) >= 24:
            return None
        low, high, upper = np.quantile(rgb.reshape(-1, 3), [.05, .95, .99], axis=0)
        if np.any(upper >= 48):
            return None
        bright = np.any(rgb > 64, axis=-1)
        # Keep tiny lights, including diagonal/edge pairs. A few disconnected
        # hot pixels alone are not sufficient evidence for lifting the frame.
        if bright.mean() >= .0001 or any(np.any(a & b) for a, b in (
            (bright[:-1], bright[1:]), (bright[:, :-1], bright[:, 1:]),
            (bright[:-1, :-1], bright[1:, 1:]),
            (bright[:-1, 1:], bright[1:, :-1]),
        )):
            return None
        tiles = np.array([tile.mean(axis=(0, 1)) for band in np.array_split(rgb, 16)
                          for tile in np.array_split(band, 16, axis=1)])
        coarse = np.diff(np.quantile(tiles, [.05, .95], axis=0), axis=0)[0]
        # The full tile range additionally protects spatially small subjects
        # which a percentile over tile means could omit.
        if np.any(coarse > 3) or np.any(np.ptp(tiles, axis=0) > 6):
            return None
        fine = high - low
        ratio = float(np.max(coarse / np.maximum(fine, 1)))
        measured.append((rgb.shape[0] * rgb.shape[1], rgb, fine, coarse, ratio))
    _, _, fine, coarse, ratio = max(measured, key=lambda item: item[0])
    # Flat noiseless colors and faint coherent gradients are not noise evidence.
    if np.min(fine) < 4 or ratio >= .12:
        return None
    coherence = 0.
    # Fine repeated texture can average away like noise, but keeps a spatial
    # relationship (including negative correlation in alternating patterns).
    for _, image, _, _, _ in measured:
        centered = image - image.mean(axis=(0, 1), dtype=np.float64).astype(np.float32)
        power = np.mean(centered * centered, axis=(0, 1), dtype=np.float64)
        for step in (1, 2, 4):
            for a, b in ((centered[:-step], centered[step:]),
                         (centered[:, :-step], centered[:, step:])):
                value = np.mean(a * b, axis=(0, 1), dtype=np.float64) / np.maximum(power, 1e-6)
                coherence = max(coherence, float(np.max(np.abs(value))))
                if coherence >= .25:
                    return None
    return {"noise_dominated_tones": 1., "dark_noise_structure_ratio": ratio,
            "dark_noise_coarse_span": float(np.max(coarse)) / 255,
            "dark_noise_pixel_span": float(np.max(fine)) / 255,
            "dark_noise_neighbor_coherence": coherence}


def limit_dark_lift(values, preserved):
    if preserved(values):
        return values
    keys = tuple(key for key in ("exposure", "shadows") if values[key] > 0)
    if not keys:
        return values
    safe = {**values, **{key: 0.0 for key in keys}}
    if not preserved(safe):
        return values
    low, high = 0.0, 1.0
    for _ in range(5):
        amount = (low + high) / 2
        candidate = {**values, **{key: round(values[key] * amount, 4) for key in keys}}
        if preserved(candidate):
            low, safe = amount, candidate
        else:
            high = amount
    return safe
