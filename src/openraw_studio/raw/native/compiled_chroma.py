"""Cached sequential CPU kernel for rendered color-noise reduction."""

import numpy as np

last_error = None
cache_disabled_reason = None
try:
    from numba import config, njit
    from numba.core.errors import NumbaError
except (ImportError, OSError) as error:
    last_error = f"{type(error).__name__}: {error}"
    njit = None
    NumbaError = RuntimeError
if njit is not None and config.DISABLE_JIT:
    last_error = "NUMBA_DISABLE_JIT disables CPU chroma compilation"
    njit = None


def _filter(pixels, start, end, strength, spatial, light, color, unit=None):
    if unit is None:
        # Numba folds dtype equality; array.itemsize remains a runtime lookup.
        unit = 257 if pixels.dtype == np.dtype(np.uint16) else 1
    maximum = 255 * unit
    divisor = 256 * unit
    height, width, _ = pixels.shape
    output = np.empty((end - start, width, 3), pixels.dtype)
    # Calibrate each neighbor once per bounded chunk instead of repeating its
    # luma/chroma arithmetic in every 5x5 neighborhood.
    prepared = np.empty((end - start + 4, width + 4, 3), np.int32)
    for row in range(end - start + 4):
        yy = min(height - 1, max(0, start + row - 2))
        for col in range(width + 4):
            xx = min(width - 1, max(0, col - 2))
            r, g, b = (
                np.int32(pixels[yy, xx, 0]),
                np.int32(pixels[yy, xx, 1]),
                np.int32(pixels[yy, xx, 2]),
            )
            prepared[row, col, 0] = (54 * r + 183 * g + 19 * b + divisor // 2) // divisor
            prepared[row, col, 1] = r - g
            prepared[row, col, 2] = b - g
    for y in range(start, end):
        for x in range(width):
            r, g, b = (
                np.int32(pixels[y, x, 0]),
                np.int32(pixels[y, x, 1]),
                np.int32(pixels[y, x, 2]),
            )
            weighted = 54 * r + 183 * g + 19 * b
            luma = weighted / 256.0
            guide = prepared[y - start + 2, x + 2, 0]
            u = np.int32(prepared[y - start + 2, x + 2, 1])
            v = np.int32(prepared[y - start + 2, x + 2, 2])
            su = sv = sw = 0.0
            for dy in range(-2, 3):
                yy = y - start + dy + 2
                for dx in range(-2, 3):
                    xx = x + dx + 2
                    nl = prepared[yy, xx, 0]
                    nu, nv = (
                        np.int32(prepared[yy, xx, 1]),
                        np.int32(prepared[yy, xx, 2]),
                    )
                    difference = (abs(nu - u) + abs(nv - v) + unit // 2) // unit
                    weight = (
                        spatial[dy + 2, dx + 2]
                        * light[abs(nl - guide)]
                        * color[difference]
                    )
                    sw += weight
                    su += weight * nu
                    sv += weight * nv
            u += strength * (su / sw - u)
            v += strength * (sv / sw - v)
            offset = (54 * u + 19 * v) / 256.0
            chroma = (u - offset, -offset, v - offset)
            scale = 1.0
            for c in chroma:
                if c > 0:
                    scale = min(scale, (maximum - luma) / c)
                elif c < 0:
                    scale = min(scale, -luma / c)
            for channel in range(3):
                output[y - start, x, channel] = np.rint(
                    min(float(maximum), max(0.0, luma + scale * chroma[channel]))
                )
    return output


try:
    chroma = njit(cache=True, nogil=True)(_filter) if njit is not None else None
except RuntimeError:
    chroma = njit(nogil=True)(_filter) if njit is not None else None


def render_chunk(pixels, start, end, strength, spatial, light, color):
    global chroma, last_error, cache_disabled_reason
    if chroma is None:
        return None
    if (
        pixels.ndim != 3
        or pixels.shape[2] != 3
        or pixels.dtype not in (np.uint8, np.uint16)
        or not 0 <= start < end <= pixels.shape[0]
        or pixels.shape[1] < 1
        or spatial.shape != (5, 5)
        or light.shape != (256,)
        or color.shape != (1021,)
    ):
        raise ValueError("Chroma chunk requires valid RGB8 or RGB16 bounds and range tables")
    pixels = np.ascontiguousarray(pixels).view()
    pixels.flags.writeable = False
    try:
        try:
            output = chroma(pixels, start, end, strength, spatial, light, color)
        except OSError as cache_error:
            uncached = njit(nogil=True)(_filter)
            output = uncached(pixels, start, end, strength, spatial, light, color)
            chroma = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        chroma = None
        return None
    last_error = None
    return output
