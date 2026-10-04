"""Portable, image-bound local subject edits. Model inference is never replayed."""

from __future__ import annotations

import base64
from functools import lru_cache
import math
from pathlib import Path
from collections.abc import Mapping
import zlib

import numpy as np

from openraw_studio.core.files import sha256_file


def decode_mask(data, width, height):
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 960
            or not 1 <= height <= 960 or not isinstance(data, str) or len(data) > 2_000_000):
        raise ValueError("Invalid subject mask dimensions or payload")
    return _decode_mask(data, width, height)


@lru_cache(maxsize=4)
def _decode_mask(data, width, height):
    try:
        packed = base64.b64decode(data, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(packed, width * height + 1)
        if len(raw) != width * height or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Invalid subject mask size")
    except (ValueError, zlib.error) as error:
        raise ValueError("Invalid subject mask payload") from error
    return np.frombuffer(raw, np.uint8).reshape(height, width)


def clean_subject(value):
    if value is None:
        return None
    keys = {"version", "source_sha256", "width", "height", "mask_zlib", "exposure", "enabled"}
    if not isinstance(value, Mapping) or value.get("version") not in ("subject.v1", "subject.v2"):
        raise ValueError("Unsupported subject edit")
    fields = ("exposure", "warmth", "tint") if value["version"] == "subject.v2" else ("exposure",)
    if set(value) != keys | set(fields):
        raise ValueError("Unsupported subject edit")
    checksum = value["source_sha256"]
    if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise ValueError("Subject edit requires a source SHA256")
    if type(value["enabled"]) is not bool:
        raise ValueError("Subject enabled flag must be boolean")
    numbers = {}
    for key in fields:
        if type(value[key]) not in (int, float):
            raise ValueError(f"Subject {key} must be a number")
        try:
            number = float(value[key])
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Invalid subject {key}") from error
        if not math.isfinite(number) or not -1 <= number <= 1:
            raise ValueError(f"Subject {key} must be within [-1, 1]")
        numbers[key] = round(number, 4)
    decode_mask(value["mask_zlib"], value["width"], value["height"])
    return {**value, **numbers}


def subject_with_color(subject, warmth, tint):
    """Upgrade only when color is used; existing v1 recipes keep their shape."""
    subject = clean_subject(subject)
    if subject is None:
        raise ValueError("Subject color requires a selection")
    colored = clean_subject({**subject, "version": "subject.v2", "warmth": warmth, "tint": tint})
    return subject if subject["version"] == "subject.v1" and warmth == tint == 0 else colored


@lru_cache(maxsize=16)
def _source_hash(path, size, mtime_ns):
    return sha256_file(path)


def source_hash(source):
    path = Path(source).resolve()
    stat = path.stat()
    return _source_hash(str(path), stat.st_size, stat.st_mtime_ns)


def validate_subject_source(subject, source):
    if subject is not None and clean_subject(subject)["source_sha256"] != source_hash(source):
        raise ValueError("Subject mask belongs to a different RAW photo")


def make_subject(weights, source):
    weights = np.asarray(weights, dtype=np.float32)
    if weights.ndim != 2 or not weights.size or max(weights.shape) > 960 or not np.isfinite(weights).all():
        raise ValueError("Invalid subject selection")
    if weights.min() < 0 or weights.max() > 1:
        raise ValueError("Subject weights must lie within [0, 1]")
    pixels = np.rint(weights * 255).astype(np.uint8)
    if not pixels.any():
        raise ValueError("Subject selection is empty")
    return {
        "version": "subject.v1", "source_sha256": source_hash(source),
        "width": pixels.shape[1], "height": pixels.shape[0],
        "mask_zlib": base64.b64encode(zlib.compress(pixels.tobytes(), 6)).decode("ascii"),
        "exposure": 0.0, "enabled": True,
    }


def subject_for_person(person, source):
    if person.evidence.status != "ready" or person.probabilities is None:
        return None
    weights = person.selection if person.selection is not None else person.probabilities
    weights = np.clip((weights - .8) / .2, 0, 1)
    weights = weights * weights * (3 - 2 * weights)
    return make_subject(weights, source) if np.any(weights >= .5) else None


def global_adjustments(values):
    """Image-bound selections must not be pasted into a different photo."""
    return {key: value for key, value in values.items() if key != "subject"}
