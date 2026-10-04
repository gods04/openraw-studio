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
