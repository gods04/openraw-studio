"""Experimental HE transform math, not yet a supported RAW rendering path."""

from __future__ import annotations

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

    Color-transform inversion and Nikon nonlinearity are still required. Keep
    this research entry point out of the public renderer/support decision.
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
