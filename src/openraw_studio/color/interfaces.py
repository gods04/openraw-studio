"""Contracts for scene-aware color processing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageRef, VisionAnalysis


@dataclass(frozen=True)
class ColorOperationRequest:
    """Inputs for color transforms and skin protection."""

    image: ImageRef
    analysis: VisionAnalysis
    recipe: Mapping[str, Any]
    output_path: Path


@runtime_checkable
class ColorEngine(Protocol):
    """Apply scene-aware color transforms."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def apply(self, request: ColorOperationRequest) -> ImageRef:
        """Apply color operations and return a new image artifact."""
