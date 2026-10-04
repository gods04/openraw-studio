"""Optional fused CPU subject-color kernel; exact fallback, no fastmath."""

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
    last_error = "NUMBA_DISABLE_JIT disables CPU subject color compilation"
    njit = None


def _balance(pixels, mask, ix, iy, jx, jy, fx, fy, gains, maximum):
    output = np.empty(pixels.shape, pixels.dtype)
    one, zero = np.float32(1), np.float32(0)
    ceiling = maximum - maximum / np.float32(255)
    for y in range(pixels.shape[0]):
        for x in range(pixels.shape[1]):
            top = np.float32(mask[iy[y], ix[x]]) * (one - fx[x]) + np.float32(mask[iy[y], jx[x]]) * fx[x]
            bottom = np.float32(mask[jy[y], ix[x]]) * (one - fx[x]) + np.float32(mask[jy[y], jx[x]]) * fx[x]
            weight = (top * (one - fy[y]) + bottom * fy[y]) / np.float32(255)
            r, g, b = np.float32(pixels[y, x, 0]), np.float32(pixels[y, x, 1]), np.float32(pixels[y, x, 2])
            red, green, blue = r * gains[0], g * gains[1], b * gains[2]
            luma = r * np.float32(.2126) + g * np.float32(.7152) + b * np.float32(.0722)
            target = red * np.float32(.2126) + green * np.float32(.7152) + blue * np.float32(.0722)
            scale = luma / max(target, np.float32(1e-6))
            dr, dg, db = red * scale - r, green * scale - g, blue * scale - b
            limit = one
            for value, delta in ((r, dr), (g, dg), (b, db)):
                if delta > zero:
                    limit = min(limit, max(ceiling - value, zero) / max(delta, np.float32(1e-6)))
            amount = limit * weight
            for channel, value, delta in ((0, r, dr), (1, g, dg), (2, b, db)):
                output[y, x, channel] = min(maximum, max(zero, np.rint(value + delta * amount)))
    return output


try:
    balance = njit(cache=True, nogil=True)(_balance) if njit is not None else None
except RuntimeError:
    balance = njit(nogil=True)(_balance) if njit is not None else None


def render(pixels, coordinates, gains):
    global balance, last_error, cache_disabled_reason
    if balance is None:
        return None
    pixels = np.ascontiguousarray(pixels).view()
    pixels.flags.writeable = False
    maximum = np.float32(np.iinfo(pixels.dtype).max)
    try:
        try:
            output = balance(pixels, *coordinates, gains, maximum)
        except OSError as cache_error:
            uncached = njit(nogil=True)(_balance)
            output = uncached(pixels, *coordinates, gains, maximum)
            balance = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        balance = None
        return None
    last_error = None
    return output
