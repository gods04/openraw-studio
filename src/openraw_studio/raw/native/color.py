"""Small, explicit color transforms for the OpenRAW Native baseline."""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Mapping, Sequence

from openraw_studio.raw.native.demosaic import LinearRgbImage
from openraw_studio.raw.native.profiles import Matrix3, camera_profile_to_linear_srgb_matrix
from openraw_studio.raw.native.sensor import LinearSensorImage


class ColorTransformError(ValueError):
    """Raised when DNG color metadata cannot be applied safely."""


_D50_WHITE = (0.96422, 1.0, 0.82521)
_D65_WHITE = (0.95047, 1.0, 1.08883)
_BRADFORD: Matrix3 = (
    (0.8951, 0.2664, -0.1614),
    (-0.7502, 1.7135, 0.0367),
    (0.0389, -0.0685, 1.0296),
)
_BRADFORD_INVERSE: Matrix3 = (
    (0.9869929, -0.1470543, 0.1599627),
    (0.4323053, 0.5183603, 0.0492912),
    (-0.0085287, 0.0400428, 0.9684867),
)
_XYZ_D65_TO_LINEAR_SRGB: Matrix3 = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)


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
    """Convert DNG camera RGB to linear sRGB through CIE XYZ.

    DNG ColorMatrix1 maps XYZ to reference camera native space, so rendering
    must invert it. The one-illuminant baseline adapts the matrix white to D50
    with Bradford, then adapts D50 to the D65 white used by sRGB. Dual-
    illuminant interpolation, ForwardMatrix, and gamut mapping remain future
    work.
    """

    if matrix is None:
        return image
    values = _numeric_values(matrix, "camera color matrix")
    if len(values) != 9:
        raise ColorTransformError("camera color matrix must contain nine values")
    camera_to_srgb = _camera_to_linear_srgb_matrix(_matrix3(values))

    transformed = []
    for red, green, blue in image.pixels:
        output = _matrix_vector(camera_to_srgb, (red, green, blue))
        transformed.append(
            (
                _clamp01(output[0]),
                _clamp01(output[1]),
                _clamp01(output[2]),
            )
        )
    return replace(image, pixels=tuple(transformed))


def _camera_to_linear_srgb_matrix(xyz_to_camera: Matrix3) -> Matrix3:
    camera_to_xyz = _inverse_matrix3(xyz_to_camera)
    source_white = _normalize_white(_matrix_vector(camera_to_xyz, (1.0, 1.0, 1.0)))
    source_to_d50 = _chromatic_adaptation(source_white, _D50_WHITE)
    d50_to_d65 = _chromatic_adaptation(_D50_WHITE, _D65_WHITE)
    camera_to_d50 = _matrix_product(source_to_d50, camera_to_xyz)
    return _matrix_product(_XYZ_D65_TO_LINEAR_SRGB, _matrix_product(d50_to_d65, camera_to_d50))


def _chromatic_adaptation(source_white: tuple[float, float, float], target_white: tuple[float, float, float]) -> Matrix3:
    source_cone = _matrix_vector(_BRADFORD, source_white)
    target_cone = _matrix_vector(_BRADFORD, target_white)
    if any(abs(value) < 1e-12 for value in source_cone):
        raise ColorTransformError("camera color matrix produces an invalid white point")
    scale: Matrix3 = (
        (target_cone[0] / source_cone[0], 0.0, 0.0),
        (0.0, target_cone[1] / source_cone[1], 0.0),
        (0.0, 0.0, target_cone[2] / source_cone[2]),
    )
    return _matrix_product(_BRADFORD_INVERSE, _matrix_product(scale, _BRADFORD))


def _matrix3(values: Sequence[float]) -> Matrix3:
    return (
        (values[0], values[1], values[2]),
        (values[3], values[4], values[5]),
        (values[6], values[7], values[8]),
    )


def _matrix_product(left: Matrix3, right: Matrix3) -> Matrix3:
    rows = tuple(
        tuple(sum(left[row][index] * right[index][column] for index in range(3)) for column in range(3))
        for row in range(3)
    )
    return rows  # type: ignore[return-value]


def _matrix_vector(matrix: Matrix3, vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[row][column] * vector[column] for column in range(3)) for row in range(3))  # type: ignore[return-value]


def _inverse_matrix3(matrix: Matrix3) -> Matrix3:
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    adjugate: Matrix3 = (
        ((e * i) - (f * h), (c * h) - (b * i), (b * f) - (c * e)),
        ((f * g) - (d * i), (a * i) - (c * g), (c * d) - (a * f)),
        ((d * h) - (e * g), (b * g) - (a * h), (a * e) - (b * d)),
    )
    determinant = (a * adjugate[0][0]) + (b * adjugate[1][0]) + (c * adjugate[2][0])
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        raise ColorTransformError("camera color matrix must be invertible")
    inverse = tuple(tuple(value / determinant for value in row) for row in adjugate)
    return inverse  # type: ignore[return-value]


def _normalize_white(white: tuple[float, float, float]) -> tuple[float, float, float]:
    if not all(math.isfinite(value) and value > 0.0 for value in white) or abs(white[1]) < 1e-12:
        raise ColorTransformError("camera color matrix produces an invalid white point")
    return white[0] / white[1], 1.0, white[2] / white[1]


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
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ColorTransformError(f"{label} must contain numeric values") from exc
    if not all(math.isfinite(value) for value in result):
        raise ColorTransformError(f"{label} must contain finite values")
    return result


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
