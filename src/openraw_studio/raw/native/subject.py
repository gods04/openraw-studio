"""Bounded local exposure on rendered RGB, identical for preview/detail/export."""

from functools import lru_cache

import numpy as np

from openraw_studio.core.subject import clean_subject, decode_mask


def _coordinates(subject, size, region):
    """Bilinear pixel-center sampling in the oriented full-frame coordinate system."""
    mask = decode_mask(subject["mask_zlib"], subject["width"], subject["height"])
    sw, sh = size
    x, y, width, height = region or (0, 0, sw, sh)
    if min(sw, sh, width, height) < 1 or min(x, y) < 0 or x + width > sw or y + height > sh:
        raise ValueError("Subject region is outside the oriented image")
    xs = np.clip((np.arange(x, x + width, dtype=np.float64) + .5) * mask.shape[1] / sw - .5, 0, mask.shape[1] - 1)
    ys = np.clip((np.arange(y, y + height, dtype=np.float64) + .5) * mask.shape[0] / sh - .5, 0, mask.shape[0] - 1)
    ix, iy = xs.astype(int), ys.astype(int)
    jx, jy = np.minimum(ix + 1, mask.shape[1] - 1), np.minimum(iy + 1, mask.shape[0] - 1)
    fx, fy = (xs - ix).astype(np.float32), (ys - iy).astype(np.float32)
    return mask, ix, iy, jx, jy, fx, fy


def subject_weights(subject, size, *, region=None):
    mask, ix, iy, jx, jy, fx, fy = _coordinates(subject, size, region)
    fy = fy[:, None]
    top = mask[iy[:, None], ix] * (1 - fx) + mask[iy[:, None], jx] * fx
    bottom = mask[jy[:, None], ix] * (1 - fx) + mask[jy[:, None], jx] * fx
    return (top * (1 - fy) + bottom * fy) / np.float32(255)


@lru_cache(maxsize=8)
def _gain_table(exposure, maximum):
    peak = np.arange(maximum + 1, dtype=np.float64) / maximum
    gain = 2 ** exposure
    # Match the native renderer's gamma 2.2 and keep white anchored. One common
    # RGB gain preserves channel ratios instead of clipping individual channels.
    result = (gain / (1 + (gain - 1) * peak ** 2.2)) ** (1 / 2.2)
    result = result.astype(np.float32)
    result.setflags(write=False)
    return result


def apply_subject(pixels, subject, *, full_size=None, region=None):
    if subject is None:
        return pixels
    subject = clean_subject(subject)
    if not subject["enabled"] or subject["exposure"] == 0:
        return pixels
    if pixels.dtype not in (np.dtype(np.uint8), np.dtype(np.uint16)) or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("Subject exposure requires RGB8 or RGB16")
    height, width = pixels.shape[:2]
    full_size = full_size or (width, height)
    x, y, rw, rh = region or (0, 0, width, height)
    if (rw, rh) != (width, height):
        raise ValueError("Subject tile dimensions do not match its region")
    maximum = np.iinfo(pixels.dtype).max
    table = _gain_table(subject["exposure"], maximum)
    from openraw_studio.raw.native import compiled_subject
    compiled = compiled_subject.render(pixels, _coordinates(subject, full_size, (x, y, width, height)), table)
    if compiled is not None:
        return compiled
    result = np.empty_like(pixels)
    for start in range(0, height, 128):
        end = min(start + 128, height)
        tile = pixels[start:end]
        weights = subject_weights(subject, full_size, region=(x, y + start, width, end - start))
        peak = np.maximum(np.maximum(tile[..., 0], tile[..., 1]), tile[..., 2])
        gains = 1 + weights * (table[peak] - 1)
        result[start:end] = np.clip(np.rint(tile * gains[..., None]), 0, maximum).astype(pixels.dtype)
    return result


def prepare_subject_renderer(subject):
    """Prime the optional RGB8 kernel in a worker, before the first slider drag."""
    if subject is not None:
        apply_subject(np.zeros((1, 1, 3), np.uint8), {**subject, "enabled": True, "exposure": .1})
