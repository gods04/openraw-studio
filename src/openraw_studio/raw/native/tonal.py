"""Shared tonal-region curves before display encoding."""


def apply_tonal_regions(value: float, *, highlights: float, shadows: float) -> float:
    if highlights == 0 and shadows == 0:
        return value
    if highlights < 0 and value > 1:
        amount = -highlights * 0.3
        # Meet x - amount*x*x at x=1 with the same value and slope,
        # then approach display white without flattening the over-range signal.
        return 1 - amount * amount / (amount + (1 - 2 * amount) * (value - 1))
    position = max(0, min(1, value))
    return (
        value
        + shadows * 1.2 * position * (1 - position) ** 2
        + highlights * 0.3 * position**2
    )


def apply_tonal_regions_array(values, *, highlights: float, shadows: float) -> None:
    """Apply the same curve in-place to floating-point NumPy samples."""
    import numpy as np

    if highlights == 0 and shadows == 0:
        return
    position = np.clip(values, 0, 1)
    if highlights < 0:
        over_white = values > 1
        excess = values[over_white] - 1
    values += (
        highlights * 0.3 * position**2 + shadows * 1.2 * position * (1 - position) ** 2
    )
    if highlights < 0 and excess.size:
        amount = -highlights * 0.3
        values[over_white] = 1 - amount * amount / (amount + (1 - 2 * amount) * excess)
