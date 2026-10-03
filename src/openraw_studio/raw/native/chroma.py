"""Local, luminance-preserving bilateral color-noise reduction on rendered RGB8.

Independent adaptation of the bilateral domain/range weighting described by
Tomasi and Manduchi (ICCV 1998):
https://users.cs.duke.edu/~tomasi/papers/tomasi/tomasiIccv98.pdf
This uses display-luma and R-G/B-G differences, not the paper's CIE-Lab metric.
It does not denoise sensor data or smooth luminance grain.
"""

import math

import numpy as np

RADIUS = 2
_axis = np.arange(-RADIUS, RADIUS + 1)
SPATIAL = np.exp(-(_axis[:, None] ** 2 + _axis[None, :] ** 2) / (2 * 1.2**2)).astype(
    np.float32
)
LIGHT = np.exp(-(np.arange(256) ** 2) / (2 * 32.0**2)).astype(np.float32)
COLOR = np.exp(-(np.arange(1021) ** 2) / (2 * 48.0**2)).astype(np.float32)
for _table in (SPATIAL, LIGHT, COLOR):
    _table.flags.writeable = False


def _reference_chunk(pixels, start, end, strength):
    height, width, _ = pixels.shape
    ys = np.clip(np.arange(start - 2, end + 2), 0, height - 1)
    xs = np.clip(np.arange(-2, width + 2), 0, width - 1)
    tile = pixels[ys[:, None], xs[None, :]].astype(np.int32)
    r, g, b = (tile[:, :, c] for c in range(3))
    weighted = 54 * r + 183 * g + 19 * b
    guide = (weighted + 128) >> 8
    u, v = r - g, b - g
    center = (slice(2, 2 + end - start), slice(2, 2 + width))
    cu, cv, cy = u[center], v[center], guide[center]
    su, sv, sw = (np.zeros(cu.shape, np.float32) for _ in range(3))
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            area = (slice(2 + dy, 2 + dy + end - start), slice(2 + dx, 2 + dx + width))
            nu, nv = u[area], v[area]
            weight = (
                SPATIAL[dy + 2, dx + 2]
                * LIGHT[np.abs(guide[area] - cy)]
                * COLOR[np.abs(nu - cu) + np.abs(nv - cv)]
            )
            sw += weight
            su += weight * nu.astype(np.float32)
            sv += weight * nv.astype(np.float32)
    u = cu + strength * (su / sw - cu)
    v = cv + strength * (sv / sw - cv)
    offset = (54 * u + 19 * v) / 256
    chroma = np.stack((u - offset, -offset, v - offset), axis=-1)
    luma = weighted[center] / 256.0
    luma = luma[:, :, None]
    # Contract the chroma towards the same luminance at the gamut boundary.
    # Per-channel clipping would instead change brightness and hue.
    bound = np.full_like(chroma, np.inf)
    np.divide(255 - luma, chroma, out=bound, where=chroma > 0)
    np.divide(-luma, chroma, out=bound, where=chroma < 0)
    scale = np.minimum(1, bound.min(axis=-1, keepdims=True))
    return np.rint(np.clip(luma + chroma * scale, 0, 255)).astype(np.uint8)


def reduce_color_noise(
    pixels, strength, *, use_gpu=True, use_compiled=True, chunk_rows=128
):
    """Return a new RGB8 image, or the unchanged input when strength is zero."""
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Color noise strength must be finite and within [0, 1]")
    if (
        pixels.dtype != np.uint8
        or pixels.ndim != 3
        or pixels.shape[2] != 3
        or min(pixels.shape[:2]) < 1
    ):
        raise ValueError(
            "Color noise reduction requires nonempty H x W x 3 RGB8 pixels"
        )
    if chunk_rows < 1:
        raise ValueError("Color noise chunk size must be positive")
    if strength == 0:
        return pixels
    pixels = np.ascontiguousarray(pixels)
    if use_gpu:
        from openraw_studio.raw.native.acceleration import disable_gpu, get_gpu

        gpu = get_gpu()
        if gpu is not None:
            try:
                return gpu.chroma(pixels, strength)
            except Exception:  # noqa: BLE001 - Preserve CPU rendering after driver failure.
                disable_gpu()
    output = np.empty_like(pixels)
    for start in range(0, pixels.shape[0], chunk_rows):
        end = min(pixels.shape[0], start + chunk_rows)
        part = None
        if use_compiled:
            from openraw_studio.raw.native.compiled_chroma import render_chunk

            part = render_chunk(pixels, start, end, strength, SPATIAL, LIGHT, COLOR)
        output[start:end] = (
            part if part is not None else _reference_chunk(pixels, start, end, strength)
        )
    return output
