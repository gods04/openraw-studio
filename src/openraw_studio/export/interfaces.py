"""Contracts for derivative image and sidecar recipe export."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageRef


@dataclass(frozen=True)
class ExportRequest:
    """Request to write a derivative file and optional sidecar recipe."""

    image: ImageRef
    recipe: Mapping[str, Any]
    output_path: Path
    format: str = "jpeg"
    quality: int = 92
    write_recipe_sidecar: bool = True


@dataclass(frozen=True)
class ExportResult:
    """Exported artifacts."""

    exported: ImageRef
    recipe_path: Path | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExportEngine(Protocol):
    """Write final image derivatives without touching the source RAW."""

    def engine_info(self) -> EngineInfo:
        """Return engine identity."""

    def export(self, request: ExportRequest) -> ExportResult:
        """Write the requested derivative and sidecar recipe."""

    def supported_formats(self) -> Sequence[str]:
        """Return export formats supported by this engine."""
