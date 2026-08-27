"""Contracts for replaceable local model runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from openraw_studio.core.domain import EngineInfo


class LicenseStatus(str, Enum):
    """License review state for a model or runtime."""

    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ModelDescriptor:
    """A model identity record independent from one runtime package."""

    model_id: str
    task: str
    version: str
    source: str
    local_path: Path | None = None
    license_status: LicenseStatus = LicenseStatus.NEEDS_REVIEW
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class InferenceRuntime(Protocol):
    """Runtime capable of loading and running local models."""

    def engine_info(self) -> EngineInfo:
        """Return runtime identity."""

    def load(self, descriptor: ModelDescriptor) -> object:
        """Load a model handle."""


@runtime_checkable
class ModelRegistry(Protocol):
    """Resolve models without hardcoding paths into engines."""

    def get(self, model_id: str) -> ModelDescriptor:
        """Return a model descriptor by ID."""
