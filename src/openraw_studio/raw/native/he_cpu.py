"""Optional prebuilt CPU kernels; no external RAW decoder or NumPy C ABI."""

from __future__ import annotations

import os

import numpy as np

import_error = None
try:
    if os.environ.get("OPENRAW_HE_AOT") == "off":
        raise ImportError("disabled by OPENRAW_HE_AOT=off")
    from . import _he_cpu as extension
except (ImportError, OSError) as error:
    extension = None
    import_error = f"{type(error).__name__}: {error}"


def decode_packet(streams, groups, thresholds, previous):
    prior = np.concatenate(previous)
    if not 0 < prior.size <= 16_000_000:
        raise ValueError("Unsupported HE packet size")
    lengths = np.empty(prior.size, np.uint8)
    values = np.empty(prior.size * 4, np.int32)
    extension.decode_packet(
        *streams, np.asarray(groups, np.int64), np.asarray(thresholds, np.int64),
        prior, lengths, values,
    )
    return lengths, values


def horizontal_rows(precinct, width):
    if not 64 <= width <= 65528 or width % 8:
        raise ValueError("Unsupported HE width")
    output = np.empty((8, width // 2), np.int32)
    extension.horizontal(
        np.concatenate(precinct.coefficients), np.concatenate(precinct.gcli),
        np.asarray(precinct.thresholds, np.int64),
        np.array([len(band) for band in precinct.gcli], np.int64), output,
    )
    return output


def linear_color(components, curve):
    if components.ndim != 3 or components.shape[0] != 4 or min(components.shape) < 1 or components.size > 64_000_000:
        raise ValueError("Unsupported HE component dimensions")
    height, width = components.shape[1:]
    output = np.empty((height * 2, width * 2), np.uint16)
    # Row tiles are not contiguous across planes. Copy only the bounded tile.
    extension.linear_color(np.ascontiguousarray(components), curve, output)
    return output
