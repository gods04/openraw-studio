"""Optional bounded-memory compilation of native bilinear Bayer interpolation."""

from __future__ import annotations

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
    last_error = "NUMBA_DISABLE_JIT disables Bayer compilation"
    njit = None


def _demosaic(source, crop, start, end, pattern, black, white, gains):
    left, top, width, height = crop
    output = np.empty((end - start, width, 3), dtype=np.float32)
    for row in range(start, end):
        for column in range(width):
            red = green = blue = np.float32(0)
            red_weight = green_weight = blue_weight = np.float32(0)
            # Preserve the reference convolution's accumulation order and apply
            # gains before interpolation, including crop edges and offset CFA.
            for dy in range(-1, 2):
                sy = row + dy
                if sy < 0 or sy >= height:
                    continue
                for dx in range(-1, 2):
                    sx = column + dx
                    if sx < 0 or sx >= width:
                        continue
                    position = ((sy + top) & 1) * 2 + ((sx + left) & 1)
                    channel = pattern[position]
                    if channel == 1 and dx != 0 and dy != 0:
                        continue
                    value = (np.float32(source[sy + top, sx + left]) - black[position]) / (white - black[position])
                    value = min(np.float32(1), max(np.float32(0), value)) * gains[channel]
                    if channel == 1:
                        weight = np.float32(4 if dx == 0 and dy == 0 else 1)
                        green += value * weight
                        green_weight += weight
                    else:
                        weight = np.float32((2 if dx == 0 else 1) * (2 if dy == 0 else 1))
                        if channel == 0:
                            red += value * weight
                            red_weight += weight
                        else:
                            blue += value * weight
                            blue_weight += weight
            output[row - start, column, 0] = red / red_weight if red_weight else np.float32(0)
            output[row - start, column, 1] = green / green_weight if green_weight else np.float32(0)
            output[row - start, column, 2] = blue / blue_weight if blue_weight else np.float32(0)
    return output


try:
    demosaic = njit(cache=True, nogil=True)(_demosaic) if njit is not None else None
except RuntimeError:
    demosaic = njit(nogil=True)(_demosaic) if njit is not None else None


def render_chunk(source, crop, start, end, pattern, black, white, gains):
    """Interpolate a validated fullres.py request, or return None for fallback."""
    global demosaic, last_error, cache_disabled_reason
    if demosaic is None:
        return None
    source = np.ascontiguousarray(source, dtype=np.uint16).view()
    left, top, width, height = crop
    if (source.ndim != 2 or left < 0 or top < 0 or width < 2 or height < 2
            or left + width > source.shape[1] or top + height > source.shape[0]
            or start < 0 or end <= start or end > height):
        raise ValueError("Bayer chunk must lie within the source and active crop")
    if len(pattern) != 4 or sorted(pattern) != [0, 1, 1, 2]:
        raise ValueError("Bayer chunk requires a four-position CFA")
    if len(black) != 4 or len(gains) != 3:
        raise ValueError("Bayer chunk requires four black levels and three gains")
    source.flags.writeable = False
    arguments = (
        source, tuple(crop), start, end, np.asarray(pattern, dtype=np.int64),
        np.asarray(black, dtype=np.float32), np.float32(white), np.asarray(gains, dtype=np.float32),
    )
    try:
        try:
            output = demosaic(*arguments)
        except OSError as cache_error:
            uncached = njit(nogil=True)(_demosaic)
            output = uncached(*arguments)
            demosaic = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        demosaic = None
        return None
    last_error = None
    return output
