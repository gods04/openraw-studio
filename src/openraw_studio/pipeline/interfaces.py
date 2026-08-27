"""Contracts for end-to-end processing orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from openraw_studio.core.domain import ImageRef


@dataclass(frozen=True)
class PipelineRequest:
    """End-to-end processing request."""

    source_path: Path
    output_dir: Path
    processing_profile: str | None = None
    creative_look: str | None = None
    auto_strength: float = 0.5
    dry_run: bool = False
    preview_only: bool = False
    overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineResult:
    """End-to-end processing result."""

    recipe: Mapping[str, Any]
    exports: Sequence[ImageRef] = field(default_factory=tuple)
    preview: ImageRef | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PhotoPipeline(Protocol):
    """Coordinate engines without absorbing their responsibilities."""

    def process(self, request: PipelineRequest) -> PipelineResult:
        """Run the processing pipeline for one source image."""
