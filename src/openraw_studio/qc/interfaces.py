"""Contracts for quality and artifact checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageRef, VisionAnalysis


@dataclass(frozen=True)
class QcFinding:
    """One quality-control finding."""

    code: str
    severity: str
    message: str
    confidence: float
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QcReport:
    """Quality-control output for a processed image."""

    passed: bool
    findings: Sequence[QcFinding] = field(default_factory=tuple)
    engine: EngineInfo | None = None


@runtime_checkable
class QcEngine(Protocol):
    """Evaluate processed output for technical and perceptual artifacts."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def evaluate(
        self,
        original: ImageRef,
        processed: ImageRef,
        analysis: VisionAnalysis,
        recipe: Mapping[str, Any],
    ) -> QcReport:
        """Return QC findings for the processed image."""
