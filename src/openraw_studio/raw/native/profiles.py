"""Camera calibration profiles used by the native RAW renderer."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]

_LINEAR_SRGB_TO_XYZ_D65: Matrix3 = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)


@dataclass(frozen=True)
class CameraColorProfile:
    """A small, explicit camera calibration record."""

    make: str
    model: str
    xyz_to_camera: Matrix3
    source: str

    @property
    def camera_to_linear_srgb(self) -> Matrix3:
        return camera_profile_to_linear_srgb_matrix(self.xyz_to_camera)


# D65 XYZ-to-camera calibration data published for the Adobe DNG Converter
# profile and independently used by established RAW implementations.
_NIKON_D500 = CameraColorProfile(
    make="NIKON CORPORATION",
    model="NIKON D500",
    xyz_to_camera=(
        (0.8813, -0.3210, -0.1036),
        (-0.4703, 1.2868, 0.2021),
        (-0.1054, 0.1940, 0.6129),
    ),
    source="Adobe DNG Converter camera calibration",
)

_PROFILES = {
    (_NIKON_D500.make, _NIKON_D500.model): _NIKON_D500,
}

_NIKON_1_J5 = CameraColorProfile(
    make="NIKON CORPORATION",
    model="NIKON 1 J5",
    xyz_to_camera=(
        (0.7520, -0.2518, -0.0645),
        (-0.3844, 1.2102, 0.1945),
        (-0.0913, 0.2249, 0.6835),
    ),
    source="Adobe DNG Converter camera calibration",
)
_PROFILES[(_NIKON_1_J5.make, _NIKON_1_J5.model)] = _NIKON_1_J5

_NIKON_Z_F = CameraColorProfile(
    make="NIKON CORPORATION",
    model="NIKON Z F",
    xyz_to_camera=(
        (1.1607, -0.4491, -0.0977),
        (-0.4522, 1.2460, 0.2304),
        (-0.0458, 0.1519, 0.7616),
    ),
    source="Adobe DNG Converter camera calibration",
)
_PROFILES[(_NIKON_Z_F.make, _NIKON_Z_F.model)] = _NIKON_Z_F


def find_camera_color_profile(make: str | None, model: str | None) -> CameraColorProfile | None:
    """Return an exact camera profile without guessing model aliases."""

    normalized_make = (make or "").strip().upper()
    normalized_model = (model or "").strip().upper()
    return _PROFILES.get((normalized_make, normalized_model))


def camera_profile_to_linear_srgb_matrix(xyz_to_camera: Sequence[float] | Matrix3) -> Matrix3:
    """Build a neutral-preserving camera RGB to linear sRGB transform."""

    if len(xyz_to_camera) == 3 and all(isinstance(row, Sequence) for row in xyz_to_camera):
        values = tuple(float(value) for row in xyz_to_camera for value in row)  # type: ignore[union-attr]
    else:
        try:
            values = tuple(float(value) for value in xyz_to_camera)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("camera profile matrix must contain numeric values") from exc
    if len(values) != 9 or not all(math.isfinite(value) for value in values):
        raise ValueError("camera profile matrix must contain nine finite values")

    matrix: Matrix3 = (
        (values[0], values[1], values[2]),
        (values[3], values[4], values[5]),
        (values[6], values[7], values[8]),
    )
    camera_from_srgb = _matrix_product(matrix, _LINEAR_SRGB_TO_XYZ_D65)
    normalized_rows = []
    for row in camera_from_srgb:
        total = sum(row)
        if not math.isfinite(total) or total <= 1e-12:
            raise ValueError("camera profile matrix produces an invalid neutral response")
        normalized_rows.append(tuple(value / total for value in row))
    return _inverse_matrix3(tuple(normalized_rows))  # type: ignore[arg-type]


def _matrix_product(left: Matrix3, right: Matrix3) -> Matrix3:
    result = tuple(
        tuple(sum(left[row][index] * right[index][column] for index in range(3)) for column in range(3))
        for row in range(3)
    )
    return result  # type: ignore[return-value]


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
        raise ValueError("camera profile matrix must be invertible")
    inverse = tuple(tuple(value / determinant for value in row) for row in adjugate)
    return inverse  # type: ignore[return-value]
