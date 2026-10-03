"""Process-local runtime setup for the standalone desktop bundle."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_numba_cache():
    """Use a writable, resolved cache path before importing Numba in a bundle."""
    if not getattr(sys, "frozen", False) or os.environ.get("NUMBA_CACHE_DIR"):
        return
    try:
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local else Path.home() / ".cache"
        cache = root / "OpenRAW Studio" / "numba"
        cache.mkdir(parents=True, exist_ok=True)
        # Windows app virtualization can otherwise redirect a rename's two
        # paths differently. Resolve the existing directory before cache writes.
        resolved = cache.resolve()
    except (OSError, RuntimeError):
        return  # The decoder's checked in-memory JIT fallback remains available.
    os.environ["NUMBA_CACHE_DIR"] = str(resolved)
