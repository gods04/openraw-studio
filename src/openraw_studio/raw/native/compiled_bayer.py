"""Optional bounded-memory compilation of native Bayer interpolation."""

from __future__ import annotations

import numpy as np

from openraw_studio.raw.native.malvar import STANDARD_BAYER

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


def _sample(source, crop, y, x, pattern, black, white, gains):
    left, top, width, height = crop
    y, x = abs(y), abs(x)
    if y >= height:
        y = abs(2 * height - 2 - y)
    if x >= width:
        x = abs(2 * width - 2 - x)
    position = ((y + top) & 1) * 2 + ((x + left) & 1)
    value = (np.float32(source[y + top, x + left]) - black[position]) / (white - black[position])
    return min(np.float32(1), max(np.float32(0), value)) * gains[pattern[position]]


if njit is not None:
    _sample = njit(inline="always")(_sample)


def _malvar(source, crop, start, end, pattern, black, white, gains):
    left, top, width, _height = crop
    output = np.empty((end - start, width, 3), dtype=np.float32)
    for y in range(start, end):
        for x in range(width):
            center = _sample(source, crop, y, x, pattern, black, white, gains)
            horizontal = (_sample(source, crop, y, x-1, pattern, black, white, gains)
                          + _sample(source, crop, y, x+1, pattern, black, white, gains))
            vertical = (_sample(source, crop, y-1, x, pattern, black, white, gains)
                        + _sample(source, crop, y+1, x, pattern, black, white, gains))
            far_x = (_sample(source, crop, y, x-2, pattern, black, white, gains)
                     + _sample(source, crop, y, x+2, pattern, black, white, gains))
            far_y = (_sample(source, crop, y-2, x, pattern, black, white, gains)
                     + _sample(source, crop, y+2, x, pattern, black, white, gains))
            diagonal = ((_sample(source, crop, y-1, x-1, pattern, black, white, gains)
                         + _sample(source, crop, y-1, x+1, pattern, black, white, gains))
                        + _sample(source, crop, y+1, x-1, pattern, black, white, gains)
                        + _sample(source, crop, y+1, x+1, pattern, black, white, gains))
            position = ((y + top) & 1) * 2 + ((x + left) & 1)
            channel = pattern[position]
            if channel == 1:
                along_x = (np.float32(5)*center + np.float32(4)*horizontal - diagonal - far_x + np.float32(.5)*far_y) / np.float32(8)
                along_y = (np.float32(5)*center + np.float32(4)*vertical - diagonal - far_y + np.float32(.5)*far_x) / np.float32(8)
                red_horizontal = pattern[position ^ 1] == 0
                red, green, blue = (along_x if red_horizontal else along_y), center, (along_y if red_horizontal else along_x)
            else:
                green = (np.float32(4)*center + np.float32(2)*(horizontal + vertical) - (far_x + far_y)) / np.float32(8)
                opposite = (np.float32(6)*center + np.float32(2)*diagonal - np.float32(1.5)*(far_x + far_y)) / np.float32(8)
                red, blue = (center if channel == 0 else opposite), (center if channel == 2 else opposite)
            output[y-start, x, 0] = red
            output[y-start, x, 1] = green
            output[y-start, x, 2] = blue
    return output


try:
    demosaic = njit(cache=True, nogil=True)(_demosaic) if njit is not None else None
except RuntimeError:
    demosaic = njit(nogil=True)(_demosaic) if njit is not None else None

try:
    malvar_demosaic = njit(cache=True, nogil=True)(_malvar) if njit is not None else None
except RuntimeError:
    malvar_demosaic = njit(nogil=True)(_malvar) if njit is not None else None


def render_chunk(source, crop, start, end, pattern, black, white, gains, *, method="bilinear"):
    """Interpolate a validated fullres.py request, or return None for fallback."""
    global demosaic, malvar_demosaic, last_error, cache_disabled_reason
    if method not in ("bilinear", "malvar") or (method == "malvar" and tuple(pattern) not in STANDARD_BAYER):
        raise ValueError("MHC requires a standard Bayer layout and a known interpolation method")
    kernel = malvar_demosaic if method == "malvar" else demosaic
    implementation = _malvar if method == "malvar" else _demosaic
    if kernel is None:
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
            output = kernel(*arguments)
        except OSError as cache_error:
            uncached = njit(nogil=True)(implementation)
            output = uncached(*arguments)
            if method == "malvar":
                malvar_demosaic = uncached
            else:
                demosaic = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        if method == "malvar":
            malvar_demosaic = None
        else:
            demosaic = None
        return None
    last_error = None
    return output
