"""Bounded RGB-guided refinement of coarse, already corroborated selections."""

from __future__ import annotations

import numpy as np
from PIL import Image


def _box_mean(values, radius):
    result = np.asarray(values, dtype=np.float32)
    for axis in (0, 1):
        length = result.shape[axis]
        start = np.maximum(0, np.arange(length) - radius)
        end = np.minimum(length, np.arange(length) + radius + 1)
        padding = [(0, 0)] * result.ndim
        padding[axis] = (1, 0)
        total = np.pad(np.cumsum(result, axis=axis, dtype=np.float64), padding)
        shape = [1] * result.ndim
        shape[axis] = length
        result = ((np.take(total, end, axis=axis) - np.take(total, start, axis=axis))
                  / (end - start).reshape(shape)).astype(np.float32)
    return result


def guided_selection(image, selection):
    """Refine edges without expanding the model's selection or changing the image.

    RGB guided-filter equations: He, Sun, Tang, ECCV 2010, equations 13-16;
    coefficient subsampling: He and Sun, Fast Guided Filter, 2015.
    https://people.csail.mit.edu/kaiming/publications/eccv10guidedfilter.pdf
    https://arxiv.org/abs/1505.00996
    The result is an editing weight, not a calibrated model probability.
    This cannot repair a wrong semantic label or reconstruct a missed subject.
    """
    image = image.convert("RGB")
    image.thumbnail((960, 960), Image.Resampling.BOX)
    selection = np.asarray(selection, dtype=np.float32)
    if (selection.ndim != 2 or not selection.size or not np.isfinite(selection).all()
            or selection.min() < 0 or selection.max() > 1):
        raise ValueError("Selection must be a finite two-dimensional weight in [0, 1]")
    working = image.copy()
    working.thumbnail((384, 384), Image.Resampling.BOX)
    source = np.asarray(Image.fromarray(selection).resize(working.size, Image.Resampling.BILINEAR))
    guide = np.asarray(working, dtype=np.float32) / 255
    radius = max(1, round(max(working.size) / 192 * 2))
    mean_rgb = _box_mean(guide, radius)
    mean_mask = _box_mean(source, radius)
    covariance = np.empty((*source.shape, 3, 3), dtype=np.float32)
    for a in range(3):
        for b in range(a, 3):
            value = _box_mean(guide[..., a] * guide[..., b], radius) - mean_rgb[..., a] * mean_rgb[..., b]
            covariance[..., a, b] = covariance[..., b, a] = value
        covariance[..., a, a] += .001
    cross = _box_mean(guide * source[..., None], radius) - mean_rgb * mean_mask[..., None]
    slope = np.linalg.solve(covariance, cross[..., None])[..., 0]
    intercept = mean_mask - np.sum(slope * mean_rgb, axis=-1)
    slope, intercept = _box_mean(slope, radius), _box_mean(intercept, radius)
    if working.size != image.size:
        def expand(values):
            return np.asarray(Image.fromarray(values).resize(image.size, Image.Resampling.BILINEAR))
        filtered = expand(intercept).copy()
        guide = np.asarray(image, dtype=np.float32) / 255
        for channel in range(3):
            filtered += expand(slope[..., channel]) * guide[..., channel]
        source = np.asarray(Image.fromarray(selection).resize(image.size, Image.Resampling.BILINEAR))
    else:
        filtered = np.sum(slope * guide, axis=-1) + intercept
    # Refinement may remove uncertain boundary pixels, never invent new people.
    result = np.minimum(source, np.clip(filtered, 0, 1)).astype(np.float32)
    result.setflags(write=False)
    return result


def project_selection(selection, shape, *, box=(0, 0, 1, 1)):
    """Project an oriented full-frame selection into a normalized viewport box."""
    height, width = shape
    if height < 1 or width < 1 or len(box) != 4 or not np.isfinite(box).all():
        raise ValueError("Invalid selection viewport")
    x0, y0, x1, y1 = box
    if not (-1e-9 <= x0 < x1 <= 1 + 1e-9 and -1e-9 <= y0 < y1 <= 1 + 1e-9):
        raise ValueError("Selection viewport must lie within the full frame")
    x0, y0, x1, y1 = np.clip(box, 0, 1)
    if x0 >= x1 or y0 >= y1:
        raise ValueError("Selection viewport must have positive area")
    image = Image.fromarray(selection)
    bounds = (x0 * image.width, y0 * image.height, x1 * image.width, y1 * image.height)
    return np.asarray(image.resize((width, height), Image.Resampling.BILINEAR, box=bounds))
