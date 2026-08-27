"""Small, explicit color transforms for the OpenRAW Native baseline."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from openraw_studio.raw.native.demosaic import LinearRgbImage
from openraw_studio.raw.native.sensor import LinearSensorImage


class ColorTransformError(ValueError):
    """Raised when DNG color metadata cannot be applied safely."""


def apply_as_shot_neutral(
    sensor: LinearSensorImage,
    neutral: Sequence[float] | None,
) -> LinearSensorImage:
    """Apply DNG AsShotNeutral gains to normalized Bayer samples.

    DNG stores the camera's neutral response as R/G/B values. The green value
    is the reference, so each channel is multiplied by green / channel.
    """

    if neutral is None:
        return sensor
    values = _numeric_values(neutral, "AsShotNeutral")
    if len(values) != 3 or any(value <= 0.0 for value in values):
        raise ColorTransformError("AsShotNeutral must contain three positive values")

    pattern = _pattern_for(sensor.color_filter_array)
    gains = {"R": values[1] / values[0], "G": 1.0, "B": values[1] / values[2]}
    adjusted = tuple(
        _clamp01(sample * gains[pattern[row % 2][column % 2]])
        for row in range(sensor.height)
        for column in range(sensor.width)
        for sample in (sensor.sample_at(row, column),)
    )
    return replace(sensor, samples=adjusted, metadata=_with_metadata(sensor.metadata, "white_balance", "as-shot"))


def apply_camera_matrix(
    image: LinearRgbImage,
    matrix: Sequence[float] | None,
) -> LinearRgbImage:
    """Apply a DNG 3x3 camera matrix to linear RGB values.

    The matrix is a first native approximation. Full illuminant selection,
    chromatic adaptation, profiling, and gamut mapping remain future work.
    """

    if matrix is None:
        return image
    values = _numeric_values(matrix, "camera color matrix")
    if len(values) != 9:
        raise ColorTransformError("camera color matrix must contain nine values")

    transformed = []
    for red, green, blue in image.pixels:
        transformed.append(
            (
                _clamp01(values[0] * red + values[1] * green + values[2] * blue),
                _clamp01(values[3] * red + values[4] * green + values[5] * blue),
                _clamp01(values[6] * red + values[7] * green + values[8] * blue),
            )
        )
    return replace(image, pixels=tuple(transformed))


def _pattern_for(cfa: str) -> tuple[tuple[str, str], tuple[str, str]]:
    patterns = {
        "RGGB": (("R", "G"), ("G", "B")),
        "GRBG": (("G", "R"), ("B", "G")),
        "GBRG": (("G", "B"), ("R", "G")),
        "BGGR": (("B", "G"), ("G", "R")),
    }
    try:
        return patterns[cfa]
    except KeyError as exc:
        raise ColorTransformError(f"unsupported CFA pattern: {cfa}") from exc


def _with_metadata(metadata: Mapping[str, object] | None, key: str, value: object) -> dict[str, object]:
    result = dict(metadata or {})
    result[key] = value
    return result


def _numeric_values(values: Sequence[float], label: str) -> tuple[float, ...]:
    try:
        return tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ColorTransformError(f"{label} must contain numeric values") from exc


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
