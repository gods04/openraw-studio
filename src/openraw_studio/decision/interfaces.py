"""Contracts for adaptive recipe decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageMetadata, ProcessingDecision, VisionAnalysis


@dataclass(frozen=True)
class DecisionRequest:
    """Inputs used to choose processing targets and constraints."""

    metadata: ImageMetadata
    analysis: VisionAnalysis
    processing_presets: Mapping[str, Mapping[str, Any]]
    creative_looks: Mapping[str, Mapping[str, Any]]
    default_processing_profile: str = "general"
    default_creative_look: str = "clean"
    user_constraints: Mapping[str, Any] = field(default_factory=dict)
    requested_look: str | None = None
    rationale_context: Sequence[str] = field(default_factory=tuple)


@runtime_checkable
class DecisionEngine(Protocol):
    """Create conservative computational presets from analysis outputs."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def decide(self, request: DecisionRequest) -> ProcessingDecision:
        """Choose profile, look, targets, constraints, and confidence."""
