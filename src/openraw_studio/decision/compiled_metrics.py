"""Optional fused Auto guard counts, retaining NumPy's float32 thresholds."""

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
    last_error = "NUMBA_DISABLE_JIT disables Auto metric compilation"
    njit = None


def _counts(pixels, luma, usable, headroom, detail, margin):
    clipped_count = new_count = lost_count = crushed_count = 0
    soft_count = 0.0
    clip = np.float32(254 / 255)
    black, near_black = np.float32(2 / 255), np.float32(4 / 255)
    scale, zero, one = np.float32(255 / 2), np.float32(0), np.float32(1)
    for i in range(len(luma)):
        clipped = new = False
        for c in range(3):
            if pixels[i, c] >= clip:
                clipped = True
                new = new or headroom[i, c]
                lost_count += int(detail[i, c])
        clipped_count += int(clipped)
        new_count += int(new)
        if usable[i]:
            crushed_count += int(luma[i] <= black)
            if margin:
                soft_count += np.float64(min(one, max(zero, (near_black - luma[i]) * scale)))
    count = len(luma)
    crushed = crushed_count / count
    return clipped_count / count, crushed, max(crushed, soft_count / count), new_count / count, lost_count


try:
    counts = njit(cache=True, nogil=True)(_counts) if njit is not None else None
except RuntimeError:
    counts = njit(nogil=True)(_counts) if njit is not None else None


def _numpy_counts(pixels, luma, usable, headroom, detail, margin):
    clipped = pixels >= 254 / 255
    new = headroom & clipped
    crushed = float(np.mean(usable & (luma <= 2 / 255)))
    risk = crushed
    if margin:
        near_black = np.clip((4 / 255 - luma[usable]) * (255 / 2), 0, 1)
        risk = max(crushed, float(near_black.sum(dtype=np.float64) / len(luma)))
    return (
        float(np.mean(clipped[:, 0] | clipped[:, 1] | clipped[:, 2])),
        crushed, risk,
        float(np.mean(new[:, 0] | new[:, 1] | new[:, 2])),
        int(np.count_nonzero(detail & clipped)),
    )


def measure_counts(pixels, luma, usable, headroom, detail, margin):
    """Measure normalized finite float32 RGB/luma and same-domain bool masks."""
    global counts, last_error, cache_disabled_reason
    if (
        pixels.ndim != 2 or pixels.shape[1] != 3 or not len(pixels)
        or pixels.dtype != np.float32 or luma.dtype != np.float32
        or luma.shape != (len(pixels),) or usable.shape != luma.shape
        or headroom.shape != pixels.shape or detail.shape != pixels.shape
        or any(value.dtype != np.bool_ for value in (usable, headroom, detail))
    ):
        raise ValueError("Auto counts require nonempty float32 RGB/luma and matching bool masks")
    if counts is not None:
        # Views keep caller ownership/flags intact and share one JIT signature.
        arrays = tuple(np.ascontiguousarray(value).view() for value in (pixels, luma, usable, headroom, detail))
        for value in arrays:
            value.flags.writeable = False
        try:
            try:
                result = counts(*arrays, margin)
            except OSError as cache_error:
                uncached = njit(nogil=True)(_counts)
                result = uncached(*arrays, margin)
                counts = uncached
                cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
        except (NumbaError, OSError, RuntimeError) as error:
            last_error = f"{type(error).__name__}: {error}"
            counts = None
        else:
            last_error = None
            return result
    return _numpy_counts(pixels, luma, usable, headroom, detail, margin)
