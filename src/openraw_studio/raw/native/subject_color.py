"""Local rendered-RGB color balance with luma preservation and gamut backoff."""

import numpy as np

from openraw_studio.core.subject import clean_subject
from openraw_studio.raw.native.subject import _coordinates, subject_weights


def color_gains(subject):
    warmth, tint = subject.get("warmth", 0), subject.get("tint", 0)
    return np.exp(np.array([.10 * warmth + .06 * tint, -.06 * tint, -.10 * warmth + .06 * tint], np.float32))


def apply_subject_color(pixels, subject, *, full_size=None, region=None):
    subject = clean_subject(subject)
    if subject is None or not subject['enabled'] or not (subject.get('warmth', 0) or subject.get('tint', 0)):
        return pixels
    if pixels.dtype not in (np.dtype(np.uint8), np.dtype(np.uint16)) or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("Subject color requires RGB8 or RGB16")
    height, width = pixels.shape[:2]
    full_size = full_size or (width, height)
    x, y, rw, rh = region or (0, 0, width, height)
    if (rw, rh) != (width, height):
        raise ValueError("Subject tile dimensions do not match its region")
    gains = color_gains(subject)
    from openraw_studio.raw.native import compiled_subject_color
    rendered = compiled_subject_color.render(pixels, _coordinates(subject, full_size, (x, y, width, height)), gains)
    if rendered is not None:
        return rendered
    maximum = np.float32(np.iinfo(pixels.dtype).max)
    ceiling = maximum - maximum / np.float32(255)
    result = np.empty_like(pixels)
    for start in range(0, height, 128):
        end = min(start + 128, height)
        tile = pixels[start:end].astype(np.float32)
        balanced = tile * gains
        luma = tile[..., 0] * np.float32(.2126) + tile[..., 1] * np.float32(.7152) + tile[..., 2] * np.float32(.0722)
        target = balanced[..., 0] * np.float32(.2126) + balanced[..., 1] * np.float32(.7152) + balanced[..., 2] * np.float32(.0722)
        balanced *= (luma / np.maximum(target, np.float32(1e-6)))[..., None]
        delta = balanced - tile
        # One common backoff retains the intended chroma direction, instead of
        # clipping channels separately. Keep one RGB8-equivalent code of room.
        limit = np.min(np.where(delta > 0, np.maximum(ceiling - tile, 0) / np.maximum(delta, np.float32(1e-6)), 1), axis=-1)
        weight = subject_weights(subject, full_size, region=(x, y + start, width, end - start))
        amount = np.minimum(limit, np.float32(1)) * weight
        result[start:end] = np.clip(np.rint(tile + delta * amount[..., None]), 0, maximum).astype(pixels.dtype)
    return result
