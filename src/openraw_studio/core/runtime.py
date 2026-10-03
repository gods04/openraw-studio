"""Process-local runtime setup for the standalone desktop bundle."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _usable_cache(root):
    """Check the atomic replacement Numba needs, not just directory creation."""
    probes = []
    try:
        cache = root / "OpenRAW Studio" / "numba"
        cache.mkdir(parents=True, exist_ok=True)
        cache = cache.resolve()
        # Reserve both names; cleanup must never touch somebody else's file.
        for suffix in (".nbi", ".nbi.tmp"):
            with tempfile.NamedTemporaryFile(dir=cache, prefix=".openraw-cache-", suffix=suffix, delete=False) as probe:
                probes.append(Path(probe.name))
                probe.write(b"OpenRAW cache probe")
        probes[0].write_bytes(b"old")
        os.replace(probes[1], probes[0])
        if probes[0].read_bytes() == b"OpenRAW cache probe":
            return cache
    except (OSError, RuntimeError):
        pass
    finally:
        for probe in probes:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
    return None


def configure_numba_cache():
    """Choose a tested per-user cache before importing Numba in a bundle."""
    if not getattr(sys, "frozen", False):
        return
    if not os.environ.get("NUMBA_CACHE_DIR"):
        local = os.environ.get("LOCALAPPDATA")
        cache = _usable_cache(Path(local)) if local else None
        if cache is None:
            try:
                cache = _usable_cache(Path.home() / ".cache")
            except (OSError, RuntimeError):
                return
        if cache is not None:
            os.environ["NUMBA_CACHE_DIR"] = str(cache)
    if os.environ.get("NUMBA_CACHE_DIR"):
        os.environ.setdefault("NUMBA_CACHE_LOCATOR_CLASSES", ",".join((
            "openraw_studio.core.kernel_cache.FrozenKernelCacheLocator",
            "UserProvidedCacheLocator", "InTreeCacheLocator", "UserWideCacheLocator",
            "IPythonCacheLocator", "ZipCacheLocator",
        )))
    # If neither private location works, checked in-memory JIT remains available.
