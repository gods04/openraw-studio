"""Contracts for replaceable RAW processing backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageRef, RawInspection


@dataclass(frozen=True)
class RawRenderRequest:
    """Request for a RAW render or intermediate export."""

    source: ImageAsset
    recipe: Mapping[str, Any]
    output_path: Path
    max_dimension: int | None = None
    color_space: str = "ProPhoto RGB"


@runtime_checkable
class RawProcessor(Protocol):
    """Replaceable RAW backend interface."""

    def engine_info(self) -> EngineInfo:
        """Return backend identity and capability metadata."""

    def inspect(self, source: ImageAsset) -> RawInspection:
        """Read RAW metadata without modifying the source file."""

    def create_preview(
        self,
        source: ImageAsset,
        output_path: Path,
        max_dimension: int,
        recipe: Mapping[str, Any] | None = None,
    ) -> ImageRef:
        """Create a preview image for UI and analysis."""

    def render_base(self, request: RawRenderRequest) -> ImageRef:
        """Render a base image using RAW adjustments from the recipe."""

    def export_intermediate(self, request: RawRenderRequest) -> ImageRef:
        """Export an intermediate image for downstream engines."""
