"""Lightweight preset references used by decision engines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openraw_studio.core.recipe import (
    CREATIVE_LOOK_SCHEMA_VERSION,
    PROCESSING_PRESET_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class PresetReference:
    """Reference to one versioned preset file."""

    preset_id: str
    schema_version: str
    path: Path


@dataclass(frozen=True)
class PresetBundle:
    """Processing preset and creative look selected for a recipe."""

    processing: PresetReference
    creative_look: PresetReference

    def validate_versions(self) -> None:
        if self.processing.schema_version != PROCESSING_PRESET_SCHEMA_VERSION:
            raise ValueError("unsupported processing preset schema version")
        if self.creative_look.schema_version != CREATIVE_LOOK_SCHEMA_VERSION:
            raise ValueError("unsupported creative look schema version")
