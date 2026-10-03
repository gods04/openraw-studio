"""Local, luminance-preserving bilateral color-noise reduction on rendered RGB8/RGB16.

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
    unit = 257 if pixels.dtype == np.uint16 else 1
    maximum = 255 * unit
    divisor = 256 * unit
    height, width, _ = pixels.shape
    ys = np.clip(np.arange(start - 2, end + 2), 0, height - 1)
    xs = np.clip(np.arange(-2, width + 2), 0, width - 1)
    tile = pixels[ys[:, None], xs[None, :]].astype(np.int32)
    r, g, b = (tile[:, :, c] for c in range(3))
    weighted = 54 * r + 183 * g + 19 * b
    guide = (weighted + divisor // 2) // divisor
    u, v = r - g, b - g
    center = (slice(2, 2 + end - start), slice(2, 2 + width))
    cu, cv, cy = u[center], v[center], guide[center]
    accumulator_dtype = np.float64 if unit == 257 else np.float32
    su, sv, sw = (np.zeros(cu.shape, accumulator_dtype) for _ in range(3))
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            area = (slice(2 + dy, 2 + dy + end - start), slice(2 + dx, 2 + dx + width))
            nu, nv = u[area], v[area]
            difference = (np.abs(nu - cu) + np.abs(nv - cv) + unit // 2) // unit
            weight = (
                SPATIAL[dy + 2, dx + 2]
                * LIGHT[np.abs(guide[area] - cy)]
                * COLOR[difference]
            )
            sw += weight
            su += weight * nu.astype(accumulator_dtype)
            sv += weight * nv.astype(accumulator_dtype)
    u = cu + strength * (su / sw - cu)
    v = cv + strength * (sv / sw - cv)
    offset = (54 * u + 19 * v) / 256
    chroma = np.stack((u - offset, -offset, v - offset), axis=-1)
    luma = weighted[center] / 256.0
    luma = luma[:, :, None]
    # Contract the chroma towards the same luminance at the gamut boundary.
    # Per-channel clipping would instead change brightness and hue.
    bound = np.full_like(chroma, np.inf)
    np.divide(maximum - luma, chroma, out=bound, where=chroma > 0)
    np.divide(-luma, chroma, out=bound, where=chroma < 0)
    scale = np.minimum(1, bound.min(axis=-1, keepdims=True))
    return np.rint(np.clip(luma + chroma * scale, 0, maximum)).astype(pixels.dtype)


def reduce_color_noise(
    pixels, strength, *, use_gpu=True, use_compiled=True, chunk_rows=128
):
    """Return RGB8/RGB16 in the input dtype, or the input itself at zero strength."""
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Color noise strength must be finite and within [0, 1]")
    if (
        pixels.dtype not in (np.uint8, np.uint16)
        or pixels.ndim != 3
        or pixels.shape[2] != 3
        or min(pixels.shape[:2]) < 1
    ):
        raise ValueError(
            "Color noise reduction requires nonempty H x W x 3 RGB8 or RGB16 pixels"
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
    def render_strip(start, end):
        part = None
        if use_compiled:
            from openraw_studio.raw.native.compiled_chroma import render_chunk

            part = render_chunk(pixels, start, end, strength, SPATIAL, LIGHT, COLOR)
        output[start:end] = (
            part if part is not None else _reference_chunk(pixels, start, end, strength)
        )
        return part is not None

    from openraw_studio.raw.native.cpu_chunks import render_chunks

    render_chunks(render_strip, height=pixels.shape[0], width=pixels.shape[1], chunk_rows=chunk_rows,
                  scratch_bytes=(pixels.shape[1] + 4) * (min(pixels.shape[0], chunk_rows) + 4) * 20)
    return output
