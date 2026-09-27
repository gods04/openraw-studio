"""Source file helpers."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

RAW_EXTENSIONS = {
    ".3fr",
    ".arw",
    ".cr2",
    ".cr3",
    ".dng",
    ".erf",
    ".fff",
    ".iiq",
    ".kdc",
    ".mef",
    ".mos",
    ".mrw",
    ".nef",
    ".nrw",
    ".orf",
    ".pef",
    ".raf",
    ".raw",
    ".rw2",
    ".rwl",
    ".srw",
    ".x3f",
}


@contextmanager
def atomic_output_path(destination: str | Path) -> Iterator[Path]:
    """Yield a sibling temporary path and atomically publish it on success."""

    output_path = Path(destination)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.stem}.{uuid4().hex}.tmp{output_path.suffix}"
    )
    try:
        yield temporary_path
        if not temporary_path.is_file():
            raise FileNotFoundError(f"Atomic writer did not create temporary output: {temporary_path}")
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def is_supported_raw_path(path: str | Path) -> bool:
    """Return whether the path has a RAW-like extension."""

    return Path(path).suffix.lower() in RAW_EXTENSIONS


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute a SHA-256 checksum without modifying the file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_file_metadata(path: str | Path) -> dict[str, Any]:
    """Return basic filesystem metadata available before a RAW backend exists."""

    source = Path(path)
    stat = source.stat()
    return {
        "filename": source.name,
        "extension": source.suffix.lower(),
        "size_bytes": stat.st_size,
        "supported_raw_extension": is_supported_raw_path(source),
    }
