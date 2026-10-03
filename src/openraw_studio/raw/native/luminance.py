"""Bounded bilateral smoothing of rendered RGB8/RGB16 luminance, not sensor denoise.

Independent domain/range weighting based on Tomasi and Manduchi (ICCV 1998):
https://users.cs.duke.edu/~tomasi/papers/tomasi/tomasiIccv98.pdf
An equal integer shift of all channels preserves their color differences. The
shift is bounded before application, avoiding per-channel truncation or hue drift.
Low-contrast texture can soften; this is not a learned detail reconstruction.
"""

import math

import numpy as np

from openraw_studio.raw.native.chroma import SPATIAL

LIGHT = np.exp(-(np.arange(256) ** 2) / (2 * 12.0**2)).astype(np.float32)
LIGHT.flags.writeable = False
WEIGHTS = np.rint(SPATIAL[..., None] * LIGHT * 65536).astype(np.int32)
WEIGHTS.flags.writeable = False


def _reference_chunk(pixels, start, end, strength):
    unit = 257 if pixels.dtype == np.uint16 else 1
    maximum = 255 * unit
    divisor = 256 * unit
    height, width, _ = pixels.shape
    ys = np.clip(np.arange(start - 2, end + 2), 0, height - 1)
    xs = np.clip(np.arange(-2, width + 2), 0, width - 1)
    tile = pixels[ys[:, None], xs[None, :]].astype(np.int32)
    weighted = 54 * tile[..., 0] + 183 * tile[..., 1] + 19 * tile[..., 2]
    guide = (weighted + divisor // 2) // divisor
    center = (slice(2, 2 + end - start), slice(2, 2 + width))
    cy = guide[center]
    sy, sw = (np.zeros(cy.shape, np.int64) for _ in range(2))
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            area = (slice(2 + dy, 2 + dy + end - start), slice(2 + dx, 2 + dx + width))
            weight = WEIGHTS[dy + 2, dx + 2, np.abs(guide[area] - cy)].astype(np.int64)
            sw += weight
            sy += weight * (weighted[area] - weighted[center])
    # Q16 weights/amount and integer ties-to-even make CPU/GPU shifts identical.
    # Even 25 * 65536 * (65535 * 256) * 65536 fits in signed 64-bit.
    numerator = sy * round(strength * 65536)
    denominator = sw * (256 * 65536)
    whole, remainder = np.divmod(np.abs(numerator), denominator)
    whole += (2 * remainder > denominator) | ((2 * remainder == denominator) & (whole % 2 == 1))
    delta = (np.sign(numerator) * whole).astype(np.int32)
    rgb = tile[center]
    delta = np.clip(delta, -rgb.min(axis=-1), maximum - rgb.max(axis=-1))
    return (rgb + delta[..., None]).astype(pixels.dtype)


def reduce_luminance_noise(pixels, strength, *, use_gpu=True, use_compiled=True, chunk_rows=128):
    """Smooth local brightness grain; zero returns the exact original input."""
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Luminance noise strength must be finite and within [0, 1]")
    if pixels.dtype not in (np.uint8, np.uint16) or pixels.ndim != 3 or pixels.shape[2] != 3 or min(pixels.shape[:2]) < 1:
        raise ValueError("Luminance noise reduction requires nonempty H x W x 3 RGB8 or RGB16 pixels")
    if chunk_rows < 1:
        raise ValueError("Luminance noise chunk size must be positive")
    if strength == 0:
        return pixels
    pixels = np.ascontiguousarray(pixels)
    if use_gpu:
        from openraw_studio.raw.native.acceleration import disable_gpu, get_gpu

        gpu = get_gpu()
        if gpu is not None:
            try:
                return gpu.luminance(pixels, strength)
            except Exception:  # noqa: BLE001 - A driver failure must retain CPU rendering.
                disable_gpu()
    output = np.empty_like(pixels)
    def render_strip(start, end):
        part = None
        if use_compiled:
            from openraw_studio.raw.native.compiled_luminance import render_chunk

            part = render_chunk(pixels, start, end, strength, WEIGHTS)
        output[start:end] = part if part is not None else _reference_chunk(pixels, start, end, strength)
        return part is not None

    from openraw_studio.raw.native.cpu_chunks import render_chunks

    render_chunks(render_strip, height=pixels.shape[0], width=pixels.shape[1], chunk_rows=chunk_rows,
                  scratch_bytes=pixels.shape[1] * (min(pixels.shape[0], chunk_rows) + 4) * 16)
    return output
