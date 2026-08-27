"""RAW backend errors."""

from __future__ import annotations


class RawProcessingError(RuntimeError):
    """Raised when a RAW backend fails to inspect or render an image."""
