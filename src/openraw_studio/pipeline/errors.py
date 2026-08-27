"""Structured pipeline errors."""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Base exception for user-facing pipeline failures."""

    code = "pipeline_error"


class SourceFileError(PipelineError):
    """Raised when the requested source file cannot be processed."""

    code = "source_file_error"


class BackendUnavailableError(PipelineError):
    """Raised when an image render needs a backend that is unavailable."""

    code = "backend_unavailable"
