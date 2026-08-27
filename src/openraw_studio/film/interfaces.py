"""Contracts for film profiles, grain, halation, bloom, and LUT handling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageRef


@dataclass(frozen=True)
class FilmOperationRequest:
    """Inputs for film simulation operations."""

    image: ImageRef
    recipe: Mapping[str, Any]
    output_path: Path
    export_width: int | None = None
    export_height: int | None = None


@runtime_checkable
class FilmEngine(Protocol):
    """Apply film profile behavior after technical processing."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def apply(self, request: FilmOperationRequest) -> ImageRef:
        """Apply film operations and return a new image artifact."""
