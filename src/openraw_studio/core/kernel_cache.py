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
    "compiled_he_transform", "compiled_luminance", "compiled_tone", "compiled_subject", "compiled_subject_color",
})
_REQUIRED = {name + ".py" for name in _KERNELS} | {"__init__.py", "malvar.py", "he_cpu.py"}
_DECISION_PACKAGE = "openraw_studio.decision"
_DECISION_KERNELS = frozenset({"compiled_metrics"})
_DECISION_REQUIRED = {name + ".py" for name in _DECISION_KERNELS}
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


def _bundle_identity(root, required=_REQUIRED):
    paths = sorted(root.rglob("*.py"))
    if not required.issubset({path.name for path in paths if path.parent == root}):
        raise ValueError("Incomplete kernel source bundle")
    digest = hashlib.sha256(_runtime_identity())
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError("Kernel source must remain inside its bundle")
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class FrozenKernelCacheLocator:
    """Numba's locator protocol, restricted to our reviewed frozen kernels.

    Kernel helpers currently live in their defining files. Bundled source in the
    same package participates in invalidation, including wrapper constants.
    Numba still checks its own version, CPU features, signatures, and bytecode.
    """

    def __init__(self, py_func, py_file, root, required=_REQUIRED):
        self._py_file = str(py_file)
        self._lineno = py_func.__code__.co_firstlineno
        self._stamp = _bundle_identity(root, required)
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
        if not bundle:
            return None
        if name in {_PACKAGE + "." + item for item in _KERNELS}:
            package, required = _PACKAGE, _REQUIRED
        elif name in {_DECISION_PACKAGE + "." + item for item in _DECISION_KERNELS}:
            package, required = _DECISION_PACKAGE, _DECISION_REQUIRED
        else:
            return None
        try:
            bundle_root = Path(bundle).resolve()
            root = bundle_root.joinpath(*package.split(".")).resolve()
            path = Path(py_file).resolve()
            if (
                not root.is_relative_to(bundle_root)
                or path != root / (name.rsplit(".", 1)[-1] + ".py")
                or path != Path(py_func.__code__.co_filename).resolve()
            ):
                return None
            locator = cls(py_func, path, root, required)
            locator.ensure_cache_path()
            return locator
        except (OSError, RuntimeError, ValueError):
            # The next standard locator retains normal frozen-cache behavior.
            return None
