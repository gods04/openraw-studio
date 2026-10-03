"""Pixel rectangles in oriented, active-image coordinates."""

from __future__ import annotations


def oriented_size(size, orientation):
    return size[::-1] if orientation in (5, 6, 7, 8) else size


def sensor_region(region, size, orientation):
    """Map an oriented (x, y, width, height) rectangle back to active pixels."""
    x, y, width, height = region
    if any(not isinstance(value, int) for value in region):
        raise ValueError("Detail region must use integer pixel coordinates")
    display_width, display_height = oriented_size(size, orientation)
    if x < 0 or y < 0 or width < 1 or height < 1 or x + width > display_width or y + height > display_height:
        raise ValueError("Detail region exceeds active image bounds")
    sw, sh = size
    return {
        2: (sw - x - width, y, width, height),
        3: (sw - x - width, sh - y - height, width, height),
        4: (x, sh - y - height, width, height),
        5: (y, x, height, width),
        6: (y, sh - x - width, height, width),
        7: (sw - y - height, sh - x - width, height, width),
        8: (sw - y - height, x, height, width),
    }.get(orientation, region)


def region_with_halo(crop, region, *, radius=1):
    """Include active neighbors required by the reconstruction filter."""
    left, top, width, height = crop
    x, y, rw, rh = region
    x0, y0 = max(0, x - radius), max(0, y - radius)
    x1, y1 = min(width, x + rw + radius), min(height, y + rh + radius)
    return (left + x0, top + y0, x1 - x0, y1 - y0), (x - x0, y - y0, x - x0 + rw, y - y0 + rh)
