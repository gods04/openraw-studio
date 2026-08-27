"""Core domain types shared between processing engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


def _validate_unit_interval(value: float, field_name: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0")


class DistanceClass(str, Enum):
    """Relative size of a detected face in the source image."""

    CLOSE = "close"
    MID = "mid"
    FAR = "far"
    TINY = "tiny"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Point2D:
    """A normalized image-space point."""

    x: float
    y: float

    def __post_init__(self) -> None:
        _validate_unit_interval(self.x, "x")
        _validate_unit_interval(self.y, "y")


@dataclass(frozen=True)
class BoundingBox:
    """A normalized image-space bounding box."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        _validate_unit_interval(self.x, "x")
        _validate_unit_interval(self.y, "y")
        _validate_unit_interval(self.width, "width")
        _validate_unit_interval(self.height, "height")
        if self.x + self.width > 1.0:
            raise ValueError("bounding box extends beyond image width")
        if self.y + self.height > 1.0:
            raise ValueError("bounding box extends beyond image height")

    @property
    def area_fraction(self) -> float:
        return self.width * self.height


@dataclass(frozen=True)
class ImageAsset:
    """A source image file that must not be destructively modified."""

    path: Path
    checksum_sha256: str | None = None


@dataclass(frozen=True)
class ImageMetadata:
    """Camera and capture metadata used by analysis and decision stages."""

    width: int | None = None
    height: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens_model: str | None = None
    iso: int | None = None
    exposure_time: str | None = None
    aperture: float | None = None
    focal_length_mm: float | None = None
    captured_at: str | None = None
    orientation: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageRef:
    """Reference to an image artifact produced by a pipeline stage."""

    path: Path
    width: int
    height: int
    color_space: str
    role: str


@dataclass(frozen=True)
class EngineInfo:
    """Traceable engine identity saved into recipes."""

    name: str
    version: str
    backend: str | None = None
    capabilities: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawInspection:
    """RAW source inspection result."""

    source: ImageAsset
    metadata: ImageMetadata
    engine: EngineInfo


@dataclass(frozen=True)
class SceneClassification:
    """A scene label with confidence and optional evidence."""

    label: str
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_unit_interval(self.confidence, "confidence")


@dataclass(frozen=True)
class MaskRef:
    """Reference to a generated semantic mask artifact."""

    mask_id: str
    label: str
    path: Path | None
    confidence: float
    face_id: str | None = None

    def __post_init__(self) -> None:
        _validate_unit_interval(self.confidence, "confidence")


@dataclass(frozen=True)
class FaceObservation:
    """Face analysis result for one detected face."""

    face_id: str
    bounding_box: BoundingBox
    confidence: float
    distance_class: DistanceClass = DistanceClass.UNKNOWN
    landmarks: Sequence[Point2D] = field(default_factory=tuple)
    pose: Mapping[str, float] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_unit_interval(self.confidence, "confidence")


@dataclass(frozen=True)
class ImageQuality:
    """Quality signals that influence conservative or aggressive processing."""

    exposure_score: float | None = None
    highlight_clip_risk: float | None = None
    shadow_clip_risk: float | None = None
    noise_score: float | None = None
    sharpness_score: float | None = None
    notes: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class VisionAnalysis:
    """Complete image understanding output from the vision stage."""

    scenes: Sequence[SceneClassification] = field(default_factory=tuple)
    faces: Sequence[FaceObservation] = field(default_factory=tuple)
    masks: Sequence[MaskRef] = field(default_factory=tuple)
    quality: ImageQuality = field(default_factory=ImageQuality)
    engine: EngineInfo | None = None


@dataclass(frozen=True)
class ProcessingDecision:
    """Decision output used to create or update a processing recipe."""

    processing_profile: str
    creative_look: str
    confidence: float
    adjustments: Mapping[str, Any]
    constraints: Mapping[str, Any] = field(default_factory=dict)
    rationale: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validate_unit_interval(self.confidence, "confidence")
