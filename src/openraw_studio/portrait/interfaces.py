"""Contracts for mask-aware and landmark-aware portrait processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageRef, VisionAnalysis


@dataclass(frozen=True)
class PortraitOperationRequest:
    """Inputs for portrait-specific operations."""

    image: ImageRef
    analysis: VisionAnalysis
    recipe: Mapping[str, Any]
    output_path: str


@runtime_checkable
class PortraitEngine(Protocol):
    """Apply controlled portrait edits from masks and landmarks."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def apply(self, request: PortraitOperationRequest) -> ImageRef:
        """Apply portrait operations and return a new image artifact."""
