"""HE transform math; only the guarded profile adapter returns sensor pixels."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from .he import HeEntropyDecoder, HeFormatError, dequantize, precincts, read_header


def inverse_wavelet_53(low, high):
    """Integer LeGall 5/3 synthesis along the last axis, symmetric extension."""
    low = np.asarray(low, dtype=np.int64)
    high = np.asarray(high, dtype=np.int64)
    if (not low.ndim or low.ndim != high.ndim or low.shape[:-1] != high.shape[:-1]
            or not low.shape[-1] or low.shape[-1] - high.shape[-1] not in (0, 1)):
        raise HeFormatError("Invalid HE wavelet band dimensions")
    low_count, high_count = low.shape[-1], high.shape[-1]
    if not high_count:
        return low.copy()
    positions = np.arange(low_count)
    left = high[..., np.clip(positions - 1, 0, high_count - 1)]
    right = high[..., np.minimum(positions, high_count - 1)]
    even = low - ((left + right + 2) >> 2)
    odd = high + ((even[..., :high_count] + even[..., np.minimum(np.arange(high_count) + 1, low_count - 1)]) >> 1)
    result = np.empty((*low.shape[:-1], low_count + high_count), dtype=np.int64)
    result[..., ::2], result[..., 1::2] = even, odd
    return result


def horizontal_rows(precinct, width):
    """Reconstruct the eight component rows, excluding group padding."""
    if width < 64 or width % 8 or len(precinct.coefficients) != 26:
        raise HeFormatError("Invalid HE horizontal transform dimensions")
    count = width // 2
    bands = tuple(dequantize(c, g, t) for c, g, t in zip(
        precinct.coefficients, precinct.gcli, precinct.thresholds,
    ))
    rows = []
    cursor = 0
    for levels in (5, 5, 0, 5, 1, 1, 0, 1):
        size = count
        high_sizes = []
        for _ in range(levels):
            high_sizes.append(size // 2)
            size -= size // 2
        row = bands[cursor][:size].astype(np.int64)
        cursor += 1
        for high_size in reversed(high_sizes):
            row = inverse_wavelet_53(row, bands[cursor][:high_size])
            cursor += 1
        rows.append(row)
    return np.stack(rows)


def decode_component_planes(data, *, accelerated=True):
    """Decode four color-transform planes, NOT linear Bayer sensor samples.

    Color-transform inversion and Nikon nonlinearity are still required. Use
    the guarded profile adapter for rendering rather than these intermediates.
    """
    header = read_header(data)
    blocks = list(precincts(data, header))
    decoder = HeEntropyDecoder(header, accelerated=accelerated)
    rows = np.empty((8, header.height // 4, header.width // 2), dtype=np.int32)
    for index, block in blocks:
        rows[:, index, :] = horizontal_rows(decoder.decode(block), header.width)
    output = np.empty((4, header.height // 2, header.width // 2), dtype=np.int32)
    for component in (0, 1, 3):
        output[component] = inverse_wavelet_53(rows[component].T, rows[component + 4].T).T
    output[2, ::2] = rows[2]
    output[2, 1::2] = rows[6]
    return output


def _neighbor(plane, row_offset, column_offset):
    rows = np.clip(np.arange(plane.shape[0]) + row_offset, 0, plane.shape[0] - 1)
    columns = np.clip(np.arange(plane.shape[1]) + column_offset, 0, plane.shape[1] - 1)
    return plane[np.ix_(rows, columns)]


def reconstruct_nonlinear_bayer(components):
    """Invert the observed zero-chroma-exponent color lift into signed RGGB.

    These four-fractional-bit samples remain in the codec's nonlinear domain.
    They must NOT be fed to the RAW renderer as linear sensor values.
    """
    components = np.asarray(components)
    if (components.ndim != 3 or components.shape[0] != 4
            or min(components.shape[1:]) < 1 or components.size > 64_000_000):
        raise HeFormatError("Invalid HE color component dimensions")
    luma, red_difference, delta, blue_difference = components.astype(np.int64)
    first_luma = luma - ((delta + _neighbor(delta, 0, 1)
                         + _neighbor(delta, -1, 0) + _neighbor(delta, -1, 1)) >> 3)
    next_luma = _neighbor(first_luma, 1, 0)
    # Extend source components before reconstructing the virtual bottom row.
    next_luma[-1] = luma[-1] - ((delta[-1] + _neighbor(delta[-1:], 0, 1)[0]) >> 2)
    second_luma = delta + ((first_luma + _neighbor(first_luma, 0, -1)
                           + next_luma + _neighbor(next_luma, 0, -1)) >> 2)
    first_green = first_luma - ((red_difference + _neighbor(red_difference, 0, 1)
                                + blue_difference + _neighbor(blue_difference, -1, 0)) >> 3)
    second_green = second_luma - ((red_difference + _neighbor(red_difference, 1, 0)
                                  + blue_difference + _neighbor(blue_difference, 0, -1)) >> 3)
    red = red_difference + ((first_green + _neighbor(first_green, 0, -1)
                            + second_green + _neighbor(second_green, -1, 0)) >> 2)
    blue = blue_difference + ((first_green + _neighbor(first_green, 1, 0)
                              + second_green + _neighbor(second_green, 0, 1)) >> 2)
    height, width = luma.shape
    bayer = np.empty((height * 2, width * 2), dtype=np.int64)
    bayer[::2, ::2], bayer[::2, 1::2] = red, first_green
    bayer[1::2, ::2], bayer[1::2, 1::2] = second_green, blue
    return bayer


@lru_cache(maxsize=1)
def _zf_linearization_curve():
    """Computed two-sided quadratic, not an imported decoder lookup table.

    Limited to the observed Z f profile with black=1008, white=16383. Across
    the reference's full index domain the rounded result differs by <=1 DN.
    This is an approximation, not a claim of exact Nikon curve reproduction.
    """
    black, white = 1008, 16383
    pivot = 65535 * np.sqrt(black / white)
    distance = np.arange(65536, dtype=np.float64) - pivot
    below = black * (1 - (distance / pivot) ** 2)
    above = black + (white - black) * (distance / (65535 - pivot)) ** 2
    curve = np.clip(np.floor(np.where(distance < 0, below, above) + 0.5), 0, white).astype(np.uint16)
    curve.flags.writeable = False
    return curve


def linearize_zf_he_bayer(nonlinear):
    """Linear sensor samples for the validated Z f profile only."""
    values = np.asarray(nonlinear)
    flat = values.reshape(-1)
    result = np.empty(flat.shape, dtype=np.uint16)
    curve = _zf_linearization_curve()
    # Avoid multiple full-resolution int64 temporaries during linearization.
    for start in range(0, len(flat), 1 << 20):
        end = start + (1 << 20)
        indices = np.clip(flat[start:end] + 32768, 0, 65535).astype(np.uint16)
        result[start:end] = curve[indices]
    return result.reshape(values.shape)
