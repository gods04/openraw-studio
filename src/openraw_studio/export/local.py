"""Local export writers for final derivative images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from openraw_studio.core.domain import EngineInfo, ImageRef
from openraw_studio.export.errors import ExportError
from openraw_studio.export.interfaces import ExportRequest, ExportResult


JPEG_SUFFIXES = {".jpg", ".jpeg"}


class LocalJpegExportEngine:
    """Write final JPEG derivatives while preserving RAW immutability."""

    def engine_info(self) -> EngineInfo:
        return EngineInfo(
            name="openraw-export",
            version="0.1.0",
            backend="local-pillow",
            capabilities={
                "jpeg": True,
                "quality": True,
                "source_passthrough": True,
                "recipe_sidecar": True,
            },
        )

    def supported_formats(self) -> tuple[str, ...]:
        return ("jpeg",)

    def export(self, request: ExportRequest) -> ExportResult:
        export_format = request.format.lower()
        if export_format not in {"jpeg", "jpg"}:
            raise ExportError(f"Unsupported export format: {request.format}")
        if request.output_path.suffix.lower() not in JPEG_SUFFIXES:
            raise ExportError("JPEG export path must end in .jpg or .jpeg")
        if not 1 <= request.quality <= 100:
            raise ExportError("JPEG quality must be between 1 and 100")
        if request.image.width <= 0 or request.image.height <= 0:
            raise ExportError("Export image dimensions must be positive")

        output_path = request.output_path
        if _same_path(request.image.path, output_path):
            if not output_path.exists():
                raise ExportError(f"Rendered image does not exist: {output_path}")
        else:
            _write_jpeg_from_existing_image(request.image.path, output_path, quality=request.quality)

        recipe_path = _write_recipe_sidecar(output_path, request.recipe) if request.write_recipe_sidecar else None
        exported = ImageRef(
            path=output_path,
            width=request.image.width,
            height=request.image.height,
            color_space="sRGB",
            role="export",
        )
        return ExportResult(
            exported=exported,
            recipe_path=recipe_path,
            metadata={
                "format": "jpeg",
                "quality": request.quality,
                "source_path": str(request.image.path),
                "source_role": request.image.role,
                "engine": self.engine_info().name,
            },
        )


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def _write_jpeg_from_existing_image(source_path: Path, output_path: Path, *, quality: int) -> None:
    if not source_path.exists():
        raise ExportError(f"Rendered image does not exist: {source_path}")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ExportError("Pillow is required for JPEG export") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source_path) as opened:
            encoded = opened.convert("RGB")
            encoded.save(output_path, format="JPEG", quality=quality, optimize=False, progressive=False)
    except OSError as exc:
        raise ExportError(f"Could not encode JPEG export: {exc}") from exc


def _write_recipe_sidecar(output_path: Path, recipe: Mapping[str, Any]) -> Path:
    recipe_path = output_path.with_name(f"{output_path.name}.recipe.json")
    recipe_path.write_text(json.dumps(recipe, indent=2, sort_keys=True), encoding="utf-8")
    return recipe_path
