"""Optional compilation of OpenRAW's own sequential Nikon entropy decoder."""

from __future__ import annotations

import numpy as np

last_error = None
cache_disabled_reason = None

try:
    from numba import njit
    from numba.core.errors import NumbaError
except (ImportError, OSError) as error:
    last_error = f"{type(error).__name__}: {error}"
    njit = None
    NumbaError = RuntimeError


def _decode(payload, width, height, table, initial, maximum):
    output = np.empty(width * height, dtype=np.uint16)
    vertical = initial.copy()
    byte_pos = 0
    bit_buffer = 0
    bit_count = 0
    prefix_bits = 8
    while (1 << prefix_bits) < len(table):
        prefix_bits += 1
    prefix_mask = len(table) - 1
    for row in range(height):
        even = vertical[row & 1, 0]
        odd = vertical[row & 1, 1]
        for column in range(width):
            while bit_count < prefix_bits and byte_pos < len(payload):
                bit_buffer = (bit_buffer << 8) | int(payload[byte_pos])
                byte_pos += 1
                bit_count += 8
            if bit_count <= 0:
                return output, 1
            prefix = (
                (bit_buffer << (prefix_bits - bit_count))
                if bit_count < prefix_bits
                else (bit_buffer >> (bit_count - prefix_bits))
            ) & prefix_mask
            packed = table[prefix]
            length = packed & 15
            if length <= 0:
                return output, 2
            if length > bit_count:
                return output, 1
            bit_count -= length
            bit_buffer &= (1 << bit_count) - 1
            category = packed >> 4
            diff = 0
            if category == 16:
                diff = -32768
            elif category:
                while bit_count < category:
                    if byte_pos >= len(payload):
                        return output, 1
                    bit_buffer = (bit_buffer << 8) | int(payload[byte_pos])
                    byte_pos += 1
                    bit_count += 8
                diff = (bit_buffer >> (bit_count - category)) & ((1 << category) - 1)
                bit_count -= category
                bit_buffer &= (1 << bit_count) - 1
                if diff < (1 << (category - 1)):
                    diff -= (1 << category) - 1
            if column & 1:
                odd += diff
                sample = odd
            else:
                even += diff
                sample = even
            if column < 2:
                vertical[row & 1, column] = sample
            output[row * width + column] = min(max(sample, 0), maximum)
    return output, 0


# Cache machine code across launches and release Python's GIL during decoding.
try:
    decode = njit(cache=True, nogil=True)(_decode) if njit is not None else None
except RuntimeError:
    # Frozen bundles may not provide a writable/source-backed cache locator.
    decode = njit(nogil=True)(_decode) if njit is not None else None


def decode_samples(payload, width, height, table, initial, maximum):
    global decode, last_error, cache_disabled_reason
    if decode is None:
        return None
    arguments = (
        np.frombuffer(payload, dtype=np.uint8),
        width,
        height,
        np.asarray(table, dtype=np.int64),
        np.asarray(initial, dtype=np.int64),
        maximum,
    )
    try:
        try:
            output, error = decode(*arguments)
        except OSError as cache_error:
            if njit is None:
                raise
            # Read-only/redirected Windows cache folders must not disable JIT.
            uncached = njit(nogil=True)(_decode)
            output, error = uncached(*arguments)
            decode = uncached
            cache_disabled_reason = f"{type(cache_error).__name__}: {cache_error}"
    except (NumbaError, OSError, RuntimeError) as error:
        # Unsupported compiler/cache environments retain the reference decoder.
        last_error = f"{type(error).__name__}: {error}"
        decode = None
        return None
    last_error = None
    if error:
        raise ValueError(
            "Nikon compressed bitstream ended early"
            if error == 1
            else "invalid Nikon Huffman prefix"
        )
    return output
