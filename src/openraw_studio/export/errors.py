"""Export-stage errors."""

from __future__ import annotations


class ExportError(RuntimeError):
    """Raised when a derivative image cannot be written."""
