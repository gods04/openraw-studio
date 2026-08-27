"""Contracts for local image understanding models and heuristics."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from openraw_studio.core.domain import (
    EngineInfo,
    FaceObservation,
    ImageMetadata,
    ImageRef,
    MaskRef,
    SceneClassification,
    VisionAnalysis,
)


@runtime_checkable
class FaceDetector(Protocol):
    """Detect faces and landmarks without editing the image."""

    def engine_info(self) -> EngineInfo:
        """Return model/backend identity."""

    def detect_faces(self, image: ImageRef) -> Sequence[FaceObservation]:
        """Return detected faces with confidence and optional landmarks."""


@runtime_checkable
class Segmenter(Protocol):
    """Generate semantic masks for people, skin, hair, clothing, or background."""

    def engine_info(self) -> EngineInfo:
        """Return model/backend identity."""

    def segment(self, image: ImageRef, faces: Sequence[FaceObservation]) -> Sequence[MaskRef]:
        """Return mask references with confidence."""


@runtime_checkable
class SceneClassifier(Protocol):
    """Classify scene semantics and lighting conditions."""

    def engine_info(self) -> EngineInfo:
        """Return model/backend identity."""

    def classify(self, image: ImageRef, metadata: ImageMetadata) -> Sequence[SceneClassification]:
        """Return scene candidates with confidence."""


@runtime_checkable
class VisionEngine(Protocol):
    """Complete image-understanding stage."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def analyze(self, image: ImageRef, metadata: ImageMetadata) -> VisionAnalysis:
        """Analyze image content without applying visual edits."""
