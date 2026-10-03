"""MHC 5x5 Bayer reconstruction (Malvar, He and Cutler, ICASSP 2004).

Independent NumPy reference. The published filters are described in
https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/Demosaicing_ICASSP04.pdf
Reflection at active-crop edges retains the Bayer phase, even on tiny crops.
"""

import numpy as np

STANDARD_BAYER = ((0, 1, 1, 2), (1, 0, 2, 1), (1, 2, 0, 1), (2, 1, 1, 0))


def _reflect(indices, length):
    folded = indices % (2 * (length - 1))
    return np.minimum(folded, 2 * (length - 1) - folded)


def demosaic_chunk(source, crop, start, end, pattern, black, white, gains):
    """Return three calibrated camera planes with a bounded two-pixel halo."""
    left, top, width, height = crop
    rows = _reflect(np.arange(start - 2, end + 2), height) + top
    columns = _reflect(np.arange(-2, width + 2), width) + left
    positions = (rows[:, None] & 1) * 2 + (columns[None, :] & 1)
    levels = np.asarray(black, np.float32)[positions]
    values = source[rows[:, None], columns[None, :]].astype(np.float32)
    values = np.clip((values - levels) / (np.float32(white) - levels), 0, 1)
    values *= np.asarray(gains, np.float32)[np.asarray(pattern)[positions]]
    count = end - start

    def at(dy, dx):
        return values[2 + dy : 2 + dy + count, 2 + dx : 2 + dx + width]

    center = at(0, 0)
    horizontal = at(0, -1) + at(0, 1)
    vertical = at(-1, 0) + at(1, 0)
    far_x = at(0, -2) + at(0, 2)
    far_y = at(-2, 0) + at(2, 0)
    diagonal = ((at(-1, -1) + at(-1, 1)) + at(1, -1)) + at(1, 1)
    green = (4 * center + 2 * (horizontal + vertical) - (far_x + far_y)) / 8
    opposite = (6 * center + 2 * diagonal - 1.5 * (far_x + far_y)) / 8
    along_x = (5 * center + 4 * horizontal - diagonal - far_x + 0.5 * far_y) / 8
    along_y = (5 * center + 4 * vertical - diagonal - far_y + 0.5 * far_x) / 8
    phase = positions[2 : 2 + count, 2 : 2 + width]
    channel = np.asarray(pattern)[phase]
    red_horizontal = np.asarray(pattern)[phase ^ 1] == 0
    return [
        np.where(
            channel == 0,
            center,
            np.where(
                channel == 2, opposite, np.where(red_horizontal, along_x, along_y)
            ),
        ),
        np.where(channel == 1, center, green),
        np.where(
            channel == 2,
            center,
            np.where(
                channel == 0, opposite, np.where(red_horizontal, along_y, along_x)
            ),
        ),
    ]
