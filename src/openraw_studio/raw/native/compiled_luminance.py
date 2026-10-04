"""Cached sequential CPU kernel for rendered luminance-noise reduction."""

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
    last_error = "NUMBA_DISABLE_JIT disables CPU luminance compilation"
    njit = None


def _filter(pixels, start, end, amount, weights, unit=None):
    if unit is None:
        # Numba folds dtype equality; array.itemsize remains a runtime lookup.
        unit = 257 if pixels.dtype == np.dtype(np.uint16) else 1
    maximum = 255 * unit
    divisor = 256 * unit
    height, width, _ = pixels.shape
    output = np.empty((end - start, width, 3), pixels.dtype)
    prepared = np.empty((end - start + 4, width + 4), np.int32)
    # Rounded 0..255 guides are shared by all overlapping 5x5 neighborhoods.
    guides = np.empty(prepared.shape, np.uint8)
    for row in range(end - start + 4):
        yy = min(height - 1, max(0, start + row - 2))
        for col in range(width + 4):
            xx = min(width - 1, max(0, col - 2))
            prepared[row, col] = 54 * np.int32(pixels[yy, xx, 0]) + 183 * np.int32(pixels[yy, xx, 1]) + 19 * np.int32(pixels[yy, xx, 2])
            guides[row, col] = (prepared[row, col] + divisor // 2) // divisor
    for y in range(start, end):
        for x in range(width):
            weighted = prepared[y - start + 2, x + 2]
            guide = np.int32(guides[y - start + 2, x + 2])
            sy = sw = np.int64(0)
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    nw = prepared[y - start + dy + 2, x + dx + 2]
                    distance = abs(np.int32(guides[y - start + dy + 2, x + dx + 2]) - guide)
                    weight = np.int64(weights[dy + 2, dx + 2, distance])
                    sw += weight
                    sy += weight * (np.int64(nw) - weighted)
            numerator, denominator = sy * amount, sw * (256 * 65536)
            delta = abs(numerator) // denominator
            remainder = abs(numerator) % denominator
            if 2 * remainder > denominator or (2 * remainder == denominator and delta % 2 == 1):
                delta += 1
            if numerator < 0:
                delta = -delta
            highest, lowest = 0, maximum
            for channel in range(3):
                value = np.int32(pixels[y, x, channel])
                highest = max(highest, value)
                lowest = min(lowest, value)
            delta = min(maximum - highest, max(-lowest, delta))
            for channel in range(3):
                output[y - start, x, channel] = np.int32(pixels[y, x, channel]) + delta
    return output


try:
    luminance = njit(cache=True, nogil=True)(_filter) if njit is not None else None
except RuntimeError:
    luminance = njit(nogil=True)(_filter) if njit is not None else None


def render_chunk(pixels, start, end, strength, weights):
    global luminance, last_error, cache_disabled_reason
    kernel = luminance
    if kernel is None:
        return None
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype not in (np.uint8, np.uint16) or not 0 <= start < end <= pixels.shape[0] or pixels.shape[1] < 1 or weights.shape != (5, 5, 256):
        raise ValueError("Luminance chunk requires valid RGB8 or RGB16 bounds and range tables")
    pixels = np.ascontiguousarray(pixels).view()
    pixels.flags.writeable = False
    amount = round(strength * 65536)
    try:
        try:
            output = kernel(pixels, start, end, amount, weights)
        except OSError as cache_error:
            uncached = njit(nogil=True)(_filter)
            output = uncached(pixels, start, end, amount, weights)
            luminance = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        luminance = None
        return None
    last_error = None
    return output
