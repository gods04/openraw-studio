"""Sensor normalization for OpenRAW Native."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any, Mapping

from openraw_studio.raw.native.decoder import RawSensorData


class SensorNormalizationError(ValueError):
    """Raised when sensor data cannot be normalized yet."""


@dataclass(frozen=True)
class LinearSensorImage:
    """Black/white-level normalized linear sensor image."""

    width: int
    height: int
    color_filter_array: str
    samples: tuple[float, ...]
    black_level: int
    white_level: int
    source_bit_depth: int
    metadata: Mapping[str, Any] | None = None

    def sample_at(self, row: int, column: int) -> float:
        if row < 0 or row >= self.height:
            raise IndexError("row outside image bounds")
        if column < 0 or column >= self.width:
            raise IndexError("column outside image bounds")
        return self.samples[row * self.width + column]


def normalize_sensor_data(sensor: RawSensorData) -> LinearSensorImage:
    """Normalize 16-bit single-sample RAW data into 0.0-1.0 linear values."""

    if sensor.bits_per_sample != 16:
        raise SensorNormalizationError("OpenRAW normalization currently supports only 16-bit sensor data")
    if sensor.samples_per_pixel != 1:
        raise SensorNormalizationError("OpenRAW normalization currently supports only single-sample Bayer data")
    if sensor.black_level is None:
        raise SensorNormalizationError("missing black level")
    if sensor.white_level is None:
        raise SensorNormalizationError("missing white level")
    if sensor.white_level <= sensor.black_level:
        raise SensorNormalizationError("white level must be greater than black level")

    expected_samples = sensor.width * sensor.height
    expected_bytes = expected_samples * 2
    if len(sensor.raw_bytes) < expected_bytes:
        raise SensorNormalizationError(
            f"sensor payload is shorter than expected: {len(sensor.raw_bytes)} < {expected_bytes}"
        )

    endian = _endian_from_metadata(sensor.metadata)
    values = struct.unpack(endian + f"{expected_samples}H", sensor.raw_bytes[:expected_bytes])
    denominator = float(sensor.white_level - sensor.black_level)
    normalized = tuple(_clamp01((value - sensor.black_level) / denominator) for value in values)
    return LinearSensorImage(
        width=sensor.width,
        height=sensor.height,
        color_filter_array=sensor.color_filter_array,
        samples=normalized,
        black_level=sensor.black_level,
        white_level=sensor.white_level,
        source_bit_depth=sensor.bits_per_sample,
        metadata={
            "source_path": str(sensor.source_path),
            "byte_order": (sensor.metadata or {}).get("byte_order", "little"),
            "sample_count": expected_samples,
        },
    )


def _endian_from_metadata(metadata: Mapping[str, Any] | None) -> str:
    byte_order = (metadata or {}).get("byte_order", "little")
    if byte_order == "little":
        return "<"
    if byte_order == "big":
        return ">"
    raise SensorNormalizationError(f"unsupported byte order: {byte_order}")


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
