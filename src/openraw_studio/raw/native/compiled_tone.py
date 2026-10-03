"""Optional fused CPU rendering of scene-linear preview pixels."""

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
    last_error = "NUMBA_DISABLE_JIT disables CPU tone compilation"
    njit = None


def _tone_region(value, params):
    value = (value - np.float32(.18)) * params[12] + np.float32(.18)
    if params[13] < 0 and value > 1:
        amount = -params[13] * np.float32(.3)
        return np.float32(1) - amount * amount / (
            amount + (np.float32(1) - np.float32(2) * amount) * (value - np.float32(1))
        )
    position = min(np.float32(1), max(np.float32(0), value))
    inverse = np.float32(1) - position
    return value + (
        params[13] * np.float32(.3) * position * position
        + params[14] * np.float32(1.2) * position * inverse * inverse
    )


if njit is not None:
    _tone_region = njit(inline="always")(_tone_region)


def _tone_into(pixels, params, output, maximum):
    for row in range(pixels.shape[0]):
        for column in range(pixels.shape[1]):
            camera_r = pixels[row, column, 0] * params[9]
            camera_g = pixels[row, column, 1] * params[10]
            camera_b = pixels[row, column, 2] * params[11]
            if params[17] >= 0:
                camera_r = min(camera_r, params[17])
                camera_g = min(camera_g, params[17])
                camera_b = min(camera_b, params[17])
            red = _tone_region(camera_r * params[0] + camera_g * params[1] + camera_b * params[2], params)
            green = _tone_region(camera_r * params[3] + camera_g * params[4] + camera_b * params[5], params)
            blue = _tone_region(camera_r * params[6] + camera_g * params[7] + camera_b * params[8], params)
            if params[16]:
                luma = red * np.float32(.2126) + green * np.float32(.7152) + blue * np.float32(.0722)
                red = luma + (red - luma) * params[15]
                green = luma + (green - luma) * params[15]
                blue = luma + (blue - luma) * params[15]
            red = min(np.float32(1), max(np.float32(0), red)) ** np.float32(1 / 2.2)
            green = min(np.float32(1), max(np.float32(0), green)) ** np.float32(1 / 2.2)
            blue = min(np.float32(1), max(np.float32(0), blue)) ** np.float32(1 / 2.2)
            if not params[16]:
                luma = (red * np.float32(54) + green * np.float32(183) + blue * np.float32(19)) / np.float32(256)
                red = luma + (red - luma) * params[15]
                green = luma + (green - luma) * params[15]
                blue = luma + (blue - luma) * params[15]
            for channel, value in enumerate((red, green, blue)):
                output[row, column, channel] = np.rint(
                    min(np.float32(1), max(np.float32(0), value)) * maximum
                )
    return output


if njit is not None:
    _tone_into = njit(inline="always")(_tone_into)


def _tone(pixels, params):
    return _tone_into(pixels, params, np.empty(pixels.shape, np.uint8), np.float32(255))


def _tone16(pixels, params):
    return _tone_into(pixels, params, np.empty(pixels.shape, np.uint16), np.float32(65535))


# No fastmath or Numba parallel pool: callers may schedule independent export
# strips while preview/Auto kernels remain sequential.
try:
    tone = njit(cache=True, nogil=True)(_tone) if njit is not None else None
except RuntimeError:
    tone = njit(nogil=True)(_tone) if njit is not None else None


try:
    tone16 = njit(cache=True, nogil=True)(_tone16) if njit is not None else None
except RuntimeError:
    tone16 = njit(nogil=True)(_tone16) if njit is not None else None


def render(pixels, params, *, bit_depth=8):
    """Quantize the processed float pixels once, or return None for fallback."""
    global tone, tone16, last_error, cache_disabled_reason
    if bit_depth not in (8, 16):
        raise ValueError("Tone output bit depth must be 8 or 16")
    kernel, implementation = (tone, _tone) if bit_depth == 8 else (tone16, _tone16)
    if kernel is None:
        return None
    pixels = np.ascontiguousarray(pixels, dtype=np.float32).view()
    params = np.ascontiguousarray(params, dtype=np.float32).view()
    if pixels.ndim != 3 or pixels.shape[2] != 3 or params.shape != (18,):
        raise ValueError("CPU tone rendering requires H x W x 3 pixels and 18 parameters")
    # A single signature covers both shared read-only proxies and writable inputs
    # without changing the caller's flags or copying the ordinary float32 proxy.
    pixels.flags.writeable = False
    params.flags.writeable = False
    try:
        try:
            output = kernel(pixels, params)
        except OSError as cache_error:
            uncached = njit(nogil=True)(implementation)
            output = uncached(pixels, params)
            if bit_depth == 8:
                tone = uncached
            else:
                tone16 = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        if bit_depth == 8:
            tone = None
        else:
            tone16 = None
        return None
    last_error = None
    return output
