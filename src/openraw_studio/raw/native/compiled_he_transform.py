"""Optional JIT for OpenRAW's HE synthesis math, with NumPy fallback at callers."""

from __future__ import annotations

import numpy as np

try:
    from numba import njit
    from numba.core.errors import NumbaError
    from numba.extending import register_jitable
except (ImportError, OSError):
    njit = None
    NumbaError = RuntimeError

    def register_jitable(function):
        return function


@register_jitable
def _dequantize(values, depths, threshold, size):
    result = np.empty(size, np.int64)
    for index in range(size):
        value = np.int64(values[index])
        term = abs(value)
        magnitude = term
        shift = max(1, int(depths[index // 4]) - threshold + 1)
        term >>= shift
        while term:
            magnitude += term
            term >>= shift
        result[index] = (-magnitude if value < 0 else magnitude) * 16
    return result


@register_jitable
def _inverse_row(low, high):
    count = len(low)
    if not len(high):
        return low.copy()
    output = np.empty(count + len(high), np.int64)
    for column in range(count):
        left, right = max(0, column - 1), min(column, len(high) - 1)
        output[2 * column] = low[column] - ((high[left] + high[right] + 2) >> 2)
    for column in range(len(high)):
        right = min(column + 1, count - 1)
        output[2 * column + 1] = high[column] + ((output[2 * column] + output[2 * right]) >> 1)
    return output


def _horizontal(values, depths, thresholds, groups, width):
    output = np.empty((8, width // 2), np.int32)
    band = offset = group_offset = 0
    for row, levels in enumerate((5, 5, 0, 5, 1, 1, 0, 1)):
        sizes = np.empty(5, np.int64)
        size = width // 2
        for level in range(levels):
            sizes[level] = size // 2
            size -= size // 2
        low = _dequantize(values[offset:], depths[group_offset:], thresholds[band], size)
        offset += groups[band] * 4
        group_offset += groups[band]
        band += 1
        for level in range(levels - 1, -1, -1):
            high = _dequantize(values[offset:], depths[group_offset:], thresholds[band], sizes[level])
            low = _inverse_row(low, high)
            offset += groups[band] * 4
            group_offset += groups[band]
            band += 1
        output[row] = low
    return output


def _linear_color(components, curve):
    height, width = components.shape[1:]
    first_luma = np.empty((height, width), np.int64)
    first_green = np.empty_like(first_luma)
    second_green = np.empty_like(first_luma)
    for row in range(height):
        up = max(0, row - 1)
        for column in range(width):
            right = min(width - 1, column + 1)
            delta = (np.int64(components[2, row, column]) + components[2, row, right]
                     + components[2, up, column] + components[2, up, right])
            first_luma[row, column] = np.int64(components[0, row, column]) - (delta >> 3)
            chroma = (np.int64(components[1, row, column]) + components[1, row, right]
                      + components[3, row, column] + components[3, up, column])
            first_green[row, column] = first_luma[row, column] - (chroma >> 3)
    for row in range(height):
        down = min(height - 1, row + 1)
        for column in range(width):
            left = max(0, column - 1)
            if row + 1 < height:
                below, below_left = first_luma[down, column], first_luma[down, left]
            else:
                # Reconstruct the virtual row from extended source components.
                right, left_right = min(width - 1, column + 1), min(width - 1, left + 1)
                below = np.int64(components[0, row, column]) - (
                    (np.int64(components[2, row, column]) + components[2, row, right]) >> 2)
                below_left = np.int64(components[0, row, left]) - (
                    (np.int64(components[2, row, left]) + components[2, row, left_right]) >> 2)
            luma = np.int64(components[2, row, column]) + (
                (first_luma[row, column] + first_luma[row, left] + below + below_left) >> 2)
            chroma = (np.int64(components[1, row, column]) + components[1, down, column]
                      + components[3, row, column] + components[3, row, left])
            second_green[row, column] = luma - (chroma >> 3)
    output = np.empty((height * 2, width * 2), np.uint16)
    for row in range(height):
        up, down = max(0, row - 1), min(height - 1, row + 1)
        for column in range(width):
            left, right = max(0, column - 1), min(width - 1, column + 1)
            red = np.int64(components[1, row, column]) + (
                (first_green[row, column] + first_green[row, left]
                 + second_green[row, column] + second_green[up, column]) >> 2)
            blue = np.int64(components[3, row, column]) + (
                (first_green[row, column] + first_green[down, column]
                 + second_green[row, column] + second_green[row, right]) >> 2)
            output[2 * row, 2 * column] = curve[min(65535, max(0, red + 32768))]
            output[2 * row, 2 * column + 1] = curve[min(65535, max(0, first_green[row, column] + 32768))]
            output[2 * row + 1, 2 * column] = curve[min(65535, max(0, second_green[row, column] + 32768))]
            output[2 * row + 1, 2 * column + 1] = curve[min(65535, max(0, blue + 32768))]
    return output


_FUNCTIONS = {"horizontal": _horizontal, "color": _linear_color}
kernels = {}
last_errors = {}
cache_disabled_reasons = {}
for _name, _function in _FUNCTIONS.items():
    try:
        kernels[_name] = njit(cache=True, nogil=True)(_function) if njit is not None else None
    except RuntimeError:
        kernels[_name] = njit(nogil=True)(_function) if njit is not None else None


def _run(name, *args):
    kernel = kernels[name]
    if kernel is None:
        return None
    try:
        try:
            result = kernel(*args)
        except OSError as cache_error:
            kernel = njit(nogil=True)(_FUNCTIONS[name])
            result = kernel(*args)
            kernels[name] = kernel
            cache_disabled_reasons[name] = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_errors[name] = f"{type(error).__name__}: {error}"
        kernels[name] = None
        return None
    last_errors[name] = None
    return result


def horizontal_rows(precinct, width):
    if kernels["horizontal"] is None:
        return None
    return _run(
        "horizontal", np.concatenate(precinct.coefficients), np.concatenate(precinct.gcli),
        np.asarray(precinct.thresholds, dtype=np.int64),
        np.array([len(band) for band in precinct.gcli], dtype=np.int64), width,
    )


def linear_color(components, curve):
    return _run("color", components, curve)
