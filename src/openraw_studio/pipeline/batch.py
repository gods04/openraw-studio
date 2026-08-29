"""Batch processing helpers built on the single-photo pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openraw_studio.core.files import is_supported_raw_path
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.support import NativeSupportReport, inspect_native_support


MAX_BATCH_SOURCES = 200
ProgressCallback = Callable[[int, int, "BatchItemResult"], None]


@dataclass(frozen=True)
class BatchSource:
    """One RAW-like source found during folder discovery."""

    path: Path
    support: NativeSupportReport

    @property
    def can_render(self) -> bool:
        return self.support.can_render

    @property
    def can_preview(self) -> bool:
        return self.support.can_preview or self.support.can_render

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "can_preview": self.can_preview,
            "can_render": self.can_render,
            "support": self.support.as_dict(),
        }


@dataclass(frozen=True)
class BatchItemResult:
    """Result for one source in a batch run."""

    source_path: Path
    status: str
    message: str
    preview_path: Path | None = None
    export_path: Path | None = None
    recipe_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "status": self.status,
            "message": self.message,
            "preview_path": str(self.preview_path) if self.preview_path is not None else None,
            "export_path": str(self.export_path) if self.export_path is not None else None,
            "recipe_path": str(self.recipe_path) if self.recipe_path is not None else None,
        }


@dataclass(frozen=True)
class BatchResult:
    """Summary for a folder or multi-source batch run."""

    output_dir: Path
    items: Sequence[BatchItemResult] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def exported(self) -> int:
        return sum(1 for item in self.items if item.status == "exported")

    @property
    def previewed(self) -> int:
        return sum(1 for item in self.items if item.status == "previewed")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")

    @property
    def processed(self) -> int:
        return self.exported + self.previewed

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "total": self.total,
            "processed": self.processed,
            "exported": self.exported,
            "previewed": self.previewed,
            "skipped": self.skipped,
            "failed": self.failed,
            "items": [item.as_dict() for item in self.items],
        }


def discover_batch_sources(folder: str | Path, *, limit: int = MAX_BATCH_SOURCES) -> tuple[BatchSource, ...]:
    """Find RAW-like files in one folder and attach current Native support reports."""

    source_dir = Path(folder)
    if not source_dir.exists():
        raise FileNotFoundError(f"Folder does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Path is not a folder: {source_dir}")

    candidates = sorted(
        (path for path in source_dir.iterdir() if path.is_file() and is_supported_raw_path(path)),
        key=lambda path: path.name.lower(),
    )
    return tuple(BatchSource(path=path, support=inspect_native_support(path)) for path in candidates[:limit])


def run_batch_export(
    sources: Sequence[str | Path],
    output_dir: str | Path,
    *,
    overrides: Mapping[str, Any] | None = None,
    processing_profile: str | None = None,
    creative_look: str | None = None,
    auto_strength: float = 0.5,
    preview_only: bool = False,
    progress_callback: ProgressCallback | None = None,
    pipeline: LocalPhotoPipeline | None = None,
) -> BatchResult:
    """Process supported sources one by one through the normal local pipeline."""

    destination = Path(output_dir)
    local_pipeline = pipeline or LocalPhotoPipeline()
    items: list[BatchItemResult] = []
    normalized_sources = tuple(Path(source) for source in sources)

    for index, source in enumerate(normalized_sources, start=1):
        support = inspect_native_support(source)
        if not support.can_render and not (preview_only and support.can_preview):
            item = BatchItemResult(
                source_path=source,
                status="skipped",
                message=support.reason,
            )
        else:
            item = _process_batch_item(
                local_pipeline,
                source,
                destination,
                overrides=overrides or {},
                processing_profile=processing_profile,
                creative_look=creative_look,
                auto_strength=auto_strength,
                preview_only=preview_only,
            )
        items.append(item)
        if progress_callback is not None:
            progress_callback(index, len(normalized_sources), item)

    return BatchResult(output_dir=destination, items=tuple(items))


def _process_batch_item(
    pipeline: LocalPhotoPipeline,
    source: Path,
    output_dir: Path,
    *,
    overrides: Mapping[str, Any],
    processing_profile: str | None,
    creative_look: str | None,
    auto_strength: float,
    preview_only: bool,
) -> BatchItemResult:
    try:
        result = pipeline.process(
            PipelineRequest(
                source_path=source,
                output_dir=output_dir,
                processing_profile=processing_profile,
                creative_look=creative_look,
                auto_strength=auto_strength,
                overrides=overrides,
                preview_only=preview_only,
            )
        )
    except (BackendUnavailableError, PipelineError, OSError, ValueError) as exc:
        return BatchItemResult(source_path=source, status="failed", message=str(exc))

    recipe_path = result.diagnostics.get("recipe_path")
    if preview_only:
        message = "Preview rendered"
        if result.preview is not None and result.preview.color_space == "embedded-jpeg":
            message = "Embedded preview extracted"
        return BatchItemResult(
            source_path=source,
            status="previewed",
            message=message,
            preview_path=result.preview.path if result.preview is not None else None,
            recipe_path=Path(recipe_path) if isinstance(recipe_path, str) else None,
        )

    export_path = result.exports[0].path if result.exports else None
    return BatchItemResult(
        source_path=source,
        status="exported" if export_path is not None else "failed",
        message="JPEG exported" if export_path is not None else "No export was produced",
        preview_path=result.preview.path if result.preview is not None else None,
        export_path=export_path,
        recipe_path=Path(recipe_path) if isinstance(recipe_path, str) else None,
    )
