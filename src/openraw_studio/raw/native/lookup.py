"""Vectorized construction of the native Nikon preview's fixed-point tables."""

import math
from array import array

from openraw_studio.raw.native.tonal import apply_tonal_regions_array


def build_color_luts(
    np, *, black_levels, fallback_black_level, white_level, channel_scales,
    exposure_scale, contrast_factor, highlights, shadows, matrix,
):
    fixed_scale = 65535.0
    samples = np.arange(65536, dtype=np.float64)
    ceiling = min(channel_scales)
    source_luts = []
    for source_channel, (black, scale) in enumerate(zip(black_levels, channel_scales)):
        if not math.isfinite(black) or black < 0 or black >= white_level:
            black = float(fallback_black_level)
        values = np.clip((samples - black) / max(1.0, white_level - black), 0, 1)
        values = np.minimum(values * scale, ceiling) * exposure_scale
        # The reference first stores array('f'), then uses Python doubles for
        # matrix multiplication and ties-to-even rounding. Preserve that boundary.
        values = values.astype(np.float32).astype(np.float64)
        channels = []
        for output_channel in range(3):
            entries = np.rint(values * matrix[output_channel][source_channel] * fixed_scale).astype(np.int32)
            lut = array("i")
            lut.frombytes(entries.tobytes())
            channels.append(lut)
        source_luts.append(tuple(channels))

    output = np.arange(4 * 65535 + 1, dtype=np.float64) / fixed_scale
    output = (output - .18) * contrast_factor + .18
    apply_tonal_regions_array(output, highlights=highlights, shadows=shadows)
    encoded = np.rint(np.clip(output, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    return source_luts[0], source_luts[1], source_luts[2], encoded.tobytes()
