"""Contracts for the OpenRAW-owned render pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageMetadata, ImageRef


@dataclass(frozen=True)
class SensorImage:
    """RAW sensor-domain data decoded from a camera file."""

    source: ImageAsset
    width: int
    height: int
    cfa_pattern: str | None = None
    black_level: float | None = None
    white_level: float | None = None
    metadata: ImageMetadata = field(default_factory=ImageMetadata)
    data_ref: Path | None = None


@dataclass(frozen=True)
class LinearImage:
    """Linear RGB image before tone and creative rendering."""

    width: int
    height: int
    color_space: str
    data_ref: Path | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkingImage:
    """Image in OpenRAW's working render space."""

    width: int
    height: int
    color_space: str
    data_ref: Path | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderRequest:
    """Request to render a preview or final export from an OpenRAW recipe."""

    source: ImageAsset
    recipe: Mapping[str, Any]
    output_path: Path
    mode: str = "export"
    max_dimension: int | None = None
    output_color_space: str = "sRGB"


@dataclass(frozen=True)
class RenderResult:
    """Output from an OpenRAW render."""

    image: ImageRef
    recipe: Mapping[str, Any]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class RawDecoder(Protocol):
    """Decode a RAW file into sensor-domain data."""

    def engine_info(self) -> EngineInfo:
        """Return decoder identity and capability metadata."""

    def decode(self, source: ImageAsset) -> SensorImage:
        """Decode RAW source data without modifying the original file."""


@runtime_checkable
class Demosaicer(Protocol):
    """Convert sensor CFA data into a linear RGB image."""

    def engine_info(self) -> EngineInfo:
        """Return demosaicer identity."""

    def demosaic(self, sensor: SensorImage) -> LinearImage:
        """Demosaic sensor-domain data."""


@runtime_checkable
class CameraColorConverter(Protocol):
    """Convert camera linear RGB into OpenRAW's working color space."""

    def engine_info(self) -> EngineInfo:
        """Return converter identity."""

    def convert(self, image: LinearImage, metadata: ImageMetadata) -> WorkingImage:
        """Apply camera profile and working color-space conversion."""


@runtime_checkable
class ToneMapper(Protocol):
    """Apply base exposure, highlight, shadow, and tone behavior."""

    def engine_info(self) -> EngineInfo:
        """Return tone mapper identity."""

    def apply(self, image: WorkingImage, recipe: Mapping[str, Any]) -> WorkingImage:
        """Apply recipe-driven tone rendering."""


@runtime_checkable
class RenderEngine(Protocol):
    """OpenRAW-owned rendering pipeline from RAW source to image artifact."""

    def engine_info(self) -> EngineInfo:
        """Return render engine identity."""

    def render(self, request: RenderRequest) -> RenderResult:
        """Render a preview or final derivative from an OpenRAW recipe."""
