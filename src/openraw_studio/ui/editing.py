"""Bounded edit history and private, atomic desktop edit sessions."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from openraw_studio.core.files import atomic_output_path

ADJUSTMENT_KEYS = (
    "exposure",
    "contrast",
    "highlights",
    "shadows",
    "warmth",
    "tint",
    "saturation",
    "color_noise",
    "luminance_noise",
)


def clean_adjustments(values):
    result = {}
    for key in ADJUSTMENT_KEYS:
        value = float(values.get(key, 0))
        if not math.isfinite(value):
            raise ValueError("Adjustment must be finite")
        limit = 2 if key == "exposure" else 1
        minimum = 0 if key in ("color_noise", "luminance_noise") else -limit
        result[key] = round(max(minimum, min(limit, value)), 4)
    return result


class EditHistory:
    def __init__(self, values=None, *, limit=100):
        self.limit = max(2, limit)
        self.reset(values or {})

    def reset(self, values):
        self._states = [clean_adjustments(values)]
        self._index = 0

    @property
    def current(self):
        return dict(self._states[self._index])

    @property
    def can_undo(self):
        return self._index > 0

    @property
    def can_redo(self):
        return self._index < len(self._states) - 1

    def commit(self, values):
        values = clean_adjustments(values)
        if values == self.current:
            return
        self._states = self._states[: self._index + 1] + [values]
        self._states = self._states[-self.limit :]
        self._index = len(self._states) - 1

    def undo(self):
        self._index = max(0, self._index - 1)
        return self.current

    def redo(self):
        self._index = min(len(self._states) - 1, self._index + 1)
        return self.current


class SessionStore:
    def __init__(self, root=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
        self.root = (
            Path(root) if root is not None else base / "OpenRAW Studio" / "edits"
        )

    def _path(self, source):
        key = hashlib.sha256(str(Path(source).resolve()).encode("utf-8")).hexdigest()
        return self.root / f"{key}.json"

    @staticmethod
    def _identity(source):
        stat = Path(source).stat()
        return {
            "path": str(Path(source).resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    def save(self, source, adjustments):
        data = {
            "version": 1,
            "source": self._identity(source),
            "adjustments": clean_adjustments(adjustments),
        }
        with atomic_output_path(self._path(source)) as destination:
            destination.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, source):
        try:
            data = json.loads(self._path(source).read_text(encoding="utf-8"))
            if data.get("version") != 1 or data.get("source") != self._identity(source):
                return None
            return clean_adjustments(data["adjustments"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
