"""Optional compilation of OpenRAW's experimental HE packet bit loops."""

from __future__ import annotations

import numpy as np

last_error = None
cache_disabled_reason = None

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
def _read_bits(data, position, count):
    end = position + count
    if count < 0 or end > len(data) * 8:
        raise ValueError("Truncated HE bitstream")
    value = 0
    while position < end:
        value = (value << 1) | ((int(data[position // 8]) >> (7 - position % 8)) & 1)
        position += 1
    return value, end


@register_jitable
def _finish(data, position):
    remaining = len(data) * 8 - position
    if remaining > 7:
        raise ValueError("Unexpected HE substream data")
    value, _ = _read_bits(data, position, remaining)
    if value:
        raise ValueError("Nonzero HE substream padding")


def _decode_packet(significance, codes, data, signs, groups, thresholds, previous):
    total = groups.sum()
    lengths = np.empty(total, dtype=np.uint8)
    coefficients = np.zeros(total * 4, dtype=np.int32)
    sig_pos = code_pos = data_pos = sign_pos = offset = 0
    for band in range(len(groups)):
        threshold = thresholds[band]
        for group in range(groups[band]):
            if group % 8 == 0:
                insignificant, sig_pos = _read_bits(significance, sig_pos, 1)
            residual = 0
            if not insignificant:
                while True:
                    bit, code_pos = _read_bits(codes, code_pos, 1)
                    if not bit:
                        break
                    residual += 1
                    if residual > 31:
                        raise ValueError("HE unary code exceeds the supported range")
            baseline = max(threshold, int(previous[offset + group]))
            distance = baseline - threshold
            if residual > 2 * distance:
                depth = threshold + residual
            elif residual % 2:
                depth = baseline - (residual + 1) // 2
            else:
                depth = baseline + residual // 2
            if depth > 15 or depth < 0:
                raise ValueError("HE GCLI value is outside the supported coefficient range")
            lengths[offset + group] = depth
            start = (offset + group) * 4
            for plane in range(depth - 1, threshold - 1, -1):
                nibble, data_pos = _read_bits(data, data_pos, 4)
                for channel in range(4):
                    coefficients[start + channel] |= ((nibble >> (3 - channel)) & 1) << plane
            for channel in range(4):
                if coefficients[start + channel]:
                    sign, sign_pos = _read_bits(signs, sign_pos, 1)
                    if sign:
                        coefficients[start + channel] *= -1
        offset += groups[band]
    _finish(significance, sig_pos)
    _finish(codes, code_pos)
    _finish(data, data_pos)
    _finish(signs, sign_pos)
    return lengths, coefficients


try:
    decode = njit(cache=True, nogil=True)(_decode_packet) if njit is not None else None
except RuntimeError:
    decode = njit(nogil=True)(_decode_packet) if njit is not None else None


def decode_packet(streams, groups, thresholds, previous):
    """Return None only when compilation is unavailable, never on corrupt data."""
    global decode, last_error, cache_disabled_reason
    if decode is None:
        return None
    arguments = (
        *(np.frombuffer(stream, dtype=np.uint8) for stream in streams),
        np.asarray(groups, dtype=np.int64),
        np.asarray(thresholds, dtype=np.int64),
        np.concatenate(previous),
    )
    try:
        try:
            result = decode(*arguments)
        except OSError as cache_error:
            uncached = njit(nogil=True)(_decode_packet)
            result = uncached(*arguments)
            decode = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        last_error = f"{type(error).__name__}: {error}"
        decode = None
        return None
    last_error = None
    return result
