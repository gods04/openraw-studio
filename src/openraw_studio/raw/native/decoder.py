"""Native RAW decoder contracts.

This module is intentionally small for now. The first native engine milestone is
to own the product pipeline shape before implementing full camera-format
decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openraw_studio.raw.native.dng import DngMetadataReader


@dataclass(frozen=True)
class RawSensorData:
    """Linear sensor data produced by a decoder."""

    source_path: Path
    width: int
    height: int
    color_filter_array: str
    raw_bytes: bytes = b""
    bits_per_sample: int | None = None
    samples_per_pixel: int | None = None
    black_level: int | None = None
    white_level: int | None = None
    metadata: Mapping[str, Any] | None = None


class NativeRawDecoder:
    """OpenRAW-owned RAW decoder foundation."""

    def __init__(self, *, dng_reader: DngMetadataReader | None = None) -> None:
        self._dng_reader = dng_reader or DngMetadataReader()

    def decode(self, source_path: Path) -> RawSensorData:
        if source_path.suffix.lower() != ".dng":
            raise NotImplementedError("OpenRAW native decoding currently starts with DNG files.")

        pixel_data = self._dng_reader.read_pixel_data(source_path)
        dng_summary = self._dng_reader.read(source_path).as_dict()
        cfa = _cfa_pattern_name(pixel_data.cfa_pattern)
        return RawSensorData(
            source_path=source_path,
            width=pixel_data.width,
            height=pixel_data.height,
            color_filter_array=cfa,
            raw_bytes=pixel_data.raw_bytes,
            bits_per_sample=pixel_data.bits_per_sample,
            samples_per_pixel=pixel_data.samples_per_pixel,
            black_level=int(pixel_data.black_level) if isinstance(pixel_data.black_level, int | float) else None,
            white_level=int(pixel_data.white_level) if isinstance(pixel_data.white_level, int | float) else None,
            metadata={
                "byte_order": pixel_data.byte_order,
                "strip_count": len(pixel_data.strip_offsets),
                "rows_per_strip": pixel_data.rows_per_strip,
                "cfa_pattern": pixel_data.cfa_pattern,
                "as_shot_neutral": dng_summary.get("as_shot_neutral"),
                "color_matrix_1": dng_summary.get("color_matrix_1"),
                "color_matrix_2": dng_summary.get("color_matrix_2"),
            },
        )


def _cfa_pattern_name(pattern: tuple[int, ...] | None) -> str:
    if pattern == (0, 1, 1, 2):
        return "RGGB"
    if pattern == (1, 0, 2, 1):
        return "GRBG"
    if pattern == (1, 2, 0, 1):
        return "GBRG"
    if pattern == (2, 1, 1, 0):
        return "BGGR"
    return "unknown"
