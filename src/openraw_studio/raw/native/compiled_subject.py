"""Optional fused CPU mask projection and local exposure, without fastmath."""

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
    last_error = "NUMBA_DISABLE_JIT disables CPU subject compilation"
    njit = None


def _expose(pixels, mask, ix, iy, jx, jy, fx, fy, table, maximum):
    output = np.empty(pixels.shape, pixels.dtype)
    one = np.float32(1)
    for y in range(pixels.shape[0]):
        for x in range(pixels.shape[1]):
            top = np.float32(mask[iy[y], ix[x]]) * (one - fx[x]) + np.float32(mask[iy[y], jx[x]]) * fx[x]
            bottom = np.float32(mask[jy[y], ix[x]]) * (one - fx[x]) + np.float32(mask[jy[y], jx[x]]) * fx[x]
            weight = (top * (one - fy[y]) + bottom * fy[y]) / np.float32(255)
            peak = max(pixels[y, x, 0], pixels[y, x, 1], pixels[y, x, 2])
            gain = one + weight * (table[peak] - one)
            for channel in range(3):
                value = np.rint(np.float32(pixels[y, x, channel]) * gain)
                output[y, x, channel] = min(maximum, max(np.float32(0), value))
    return output


try:
    expose = njit(cache=True, nogil=True)(_expose) if njit is not None else None
except RuntimeError:
    expose = njit(nogil=True)(_expose) if njit is not None else None


def render(pixels, coordinates, table):
    global expose, last_error, cache_disabled_reason
    kernel = expose
    if kernel is None:
        return None
    pixels = np.ascontiguousarray(pixels).view()
    pixels.flags.writeable = False
    maximum = np.float32(np.iinfo(pixels.dtype).max)
    try:
        try:
            output = kernel(pixels, *coordinates, table, maximum)
        except OSError as cache_error:
            uncached = njit(nogil=True)(_expose)
            output = uncached(pixels, *coordinates, table, maximum)
            expose = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        expose = None
        return None
    last_error = None
    return output
