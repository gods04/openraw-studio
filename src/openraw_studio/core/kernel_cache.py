"""Content-checked cache identity for source-backed, frozen native kernels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile

import llvmlite
import numba
from numba import config
import numpy as np


_PACKAGE = "openraw_studio.raw.native"
_KERNELS = frozenset({
    "compiled_bayer", "compiled_chroma", "compiled_decode", "compiled_he",
    "compiled_he_transform", "compiled_luminance", "compiled_tone",
})
_REQUIRED = {name + ".py" for name in _KERNELS} | {"__init__.py", "malvar.py", "he_cpu.py"}
_CACHE_POLICY = "openraw-native-v1"


def _runtime_identity():
    # Resolved flags include configuration-file settings, not only environment
    # variables. Cache location/logging do not change compiled code.
    ignored = {"CACHE_DIR", "CACHE_LOCATOR_CLASSES", "DEBUG_CACHE"}
    flags = {
        name: repr(value) for name, value in vars(config).items()
        if name.isupper() and not name.startswith("_") and name not in ignored
    }
    return json.dumps((
        _CACHE_POLICY, sys.version, sys.platform, sys.byteorder,
        np.__version__, numba.__version__, llvmlite.__version__, flags,
    ), sort_keys=True).encode("utf-8")


def _bundle_identity(root):
    paths = sorted(root.rglob("*.py"))
    if not _REQUIRED.issubset({path.name for path in paths if path.parent == root}):
        raise ValueError("Incomplete native source bundle")
    digest = hashlib.sha256(_runtime_identity())
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError("Native source must remain inside its bundle")
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class FrozenKernelCacheLocator:
    """Numba's locator protocol, restricted to our reviewed frozen kernels.

    Kernel helpers currently live in their defining files. The complete native
    Python package also participates in invalidation, including wrapper constants.
    Numba still checks its own version, CPU features, signatures, and bytecode.
    """

    def __init__(self, py_func, py_file, root):
        self._py_file = str(py_file)
        self._lineno = py_func.__code__.co_firstlineno
        self._stamp = _bundle_identity(root)
        self._cache_path = str(Path(config.CACHE_DIR) / (_CACHE_POLICY + "-" + self._stamp))

    def get_cache_path(self):
        return self._cache_path

    def get_source_stamp(self):
        return self._stamp

    def get_disambiguator(self):
        return str(self._lineno)

    def ensure_cache_path(self):
        path = Path(self._cache_path)
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass

    @classmethod
    def from_function(cls, py_func, py_file):
        if not getattr(sys, "frozen", False) or not config.CACHE_DIR:
            return None
        bundle = getattr(sys, "_MEIPASS", None)
        name = getattr(py_func, "__module__", "")
        if not bundle or name not in {_PACKAGE + "." + item for item in _KERNELS}:
            return None
        try:
            bundle_root = Path(bundle).resolve()
            root = (bundle_root / "openraw_studio" / "raw" / "native").resolve()
            path = Path(py_file).resolve()
            if (
                not root.is_relative_to(bundle_root)
                or path != root / (name.rsplit(".", 1)[-1] + ".py")
                or path != Path(py_func.__code__.co_filename).resolve()
            ):
                return None
            locator = cls(py_func, path, root)
            locator.ensure_cache_path()
            return locator
        except (OSError, RuntimeError, ValueError):
            # The next standard locator retains normal frozen-cache behavior.
            return None
