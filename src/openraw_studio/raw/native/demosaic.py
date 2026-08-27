"""Simple demosaic algorithms for OpenRAW Native."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from openraw_studio.raw.native.sensor import LinearSensorImage


class DemosaicError(ValueError):
    """Raised when a sensor image cannot be demosaiced yet."""


@dataclass(frozen=True)
class LinearRgbImage:
    """Linear RGB image produced from normalized Bayer sensor data."""

    width: int
    height: int
    pixels: tuple[tuple[float, float, float], ...]
    source_color_filter_array: str

    def pixel_at(self, row: int, column: int) -> tuple[float, float, float]:
        if row < 0 or row >= self.height:
            raise IndexError("row outside image bounds")
        if column < 0 or column >= self.width:
            raise IndexError("column outside image bounds")
        return self.pixels[row * self.width + column]


BAYER_PATTERNS: Mapping[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "RGGB": (("R", "G"), ("G", "B")),
    "GRBG": (("G", "R"), ("B", "G")),
    "GBRG": (("G", "B"), ("R", "G")),
    "BGGR": (("B", "G"), ("G", "R")),
}


def demosaic_simple(sensor: LinearSensorImage) -> LinearRgbImage:
    """Convert Bayer sensor data to linear RGB using local color averages.

    This intentionally favors a small, deterministic baseline over image quality.
    It preserves known CFA samples and fills missing channels from neighboring
    samples of the same color.
    """

    pattern = BAYER_PATTERNS.get(sensor.color_filter_array)
    if pattern is None:
        raise DemosaicError(f"unsupported CFA pattern: {sensor.color_filter_array}")
    if len(sensor.samples) < sensor.width * sensor.height:
        raise DemosaicError("sensor sample payload is shorter than width*height")

    pixels = []
    for row in range(sensor.height):
        for column in range(sensor.width):
            values = {
                "R": _estimate_channel(sensor, pattern, row, column, "R"),
                "G": _estimate_channel(sensor, pattern, row, column, "G"),
                "B": _estimate_channel(sensor, pattern, row, column, "B"),
            }
            pixels.append((_clamp01(values["R"]), _clamp01(values["G"]), _clamp01(values["B"])))

    return LinearRgbImage(
        width=sensor.width,
        height=sensor.height,
        pixels=tuple(pixels),
        source_color_filter_array=sensor.color_filter_array,
    )


def _estimate_channel(
    sensor: LinearSensorImage,
    pattern: tuple[tuple[str, str], tuple[str, str]],
    row: int,
    column: int,
    channel: str,
) -> float:
    if _channel_at(pattern, row, column) == channel:
        return sensor.sample_at(row, column)

    for radius in (1, 2):
        values = [
            sensor.sample_at(candidate_row, candidate_column)
            for candidate_row in range(max(0, row - radius), min(sensor.height, row + radius + 1))
            for candidate_column in range(max(0, column - radius), min(sensor.width, column + radius + 1))
            if _channel_at(pattern, candidate_row, candidate_column) == channel
        ]
        if values:
            return sum(values) / float(len(values))
    return sensor.sample_at(row, column)


def _channel_at(pattern: tuple[tuple[str, str], tuple[str, str]], row: int, column: int) -> str:
    return pattern[row % 2][column % 2]


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
