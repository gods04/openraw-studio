"""Local export writers for final derivative images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from openraw_studio.core.domain import EngineInfo, ImageRef
from openraw_studio.export.errors import ExportError
from openraw_studio.export.formats import normalize_export_format, validate_export_quality
from openraw_studio.export.interfaces import ExportRequest, ExportResult
from openraw_studio.export.metadata import build_jpeg_exif, build_tiff_info


JPEG_SUFFIXES = {".jpg", ".jpeg"}
TIFF_SUFFIXES = {".tif", ".tiff"}


class LocalImageExportEngine:
    """Write local JPEG/TIFF derivatives while preserving RAW immutability."""

    def engine_info(self) -> EngineInfo:
        return EngineInfo(
            name="openraw-export",
            version="0.2.0",
            backend="local-pillow",
            capabilities={
                "jpeg": True,
                "tiff": "rgb8-deflate",
                "jpeg_quality": True,
                "photographic_metadata": "safe-capture-no-gps-v0.1",
                "source_passthrough": True,
                "recipe_sidecar": True,
            },
        )

    def supported_formats(self) -> tuple[str, ...]:
        return ("jpeg", "tiff")

    def export(self, request: ExportRequest) -> ExportResult:
        try:
            export_format = normalize_export_format(request.format)
            quality = validate_export_quality(request.quality)
        except ValueError as exc:
            raise ExportError(str(exc)) from exc
        suffix = request.output_path.suffix.lower()
        if export_format == "jpeg" and suffix not in JPEG_SUFFIXES:
            raise ExportError("JPEG export path must end in .jpg or .jpeg")
        if export_format == "tiff" and suffix not in TIFF_SUFFIXES:
            raise ExportError("TIFF export path must end in .tif or .tiff")
        if request.image.width <= 0 or request.image.height <= 0:
            raise ExportError("Export image dimensions must be positive")

        output_path = request.output_path
        if _same_path(request.image.path, output_path):
            if not output_path.exists():
                raise ExportError(f"Rendered image does not exist: {output_path}")
        else:
            _write_from_existing_image(
                request.image.path,
                output_path,
                export_format=export_format,
                quality=quality,
                recipe=request.recipe,
            )

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
                "format": export_format,
                "quality": quality if export_format == "jpeg" else None,
                "bit_depth": 8,
                "compression": "jpeg" if export_format == "jpeg" else "tiff_deflate",
                "metadata_policy": "safe-capture-no-gps-v0.1",
                "source_path": str(request.image.path),
                "source_role": request.image.role,
                "engine": self.engine_info().name,
            },
        )


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def _write_from_existing_image(
    source_path: Path,
    output_path: Path,
    *,
    export_format: str,
    quality: int,
    recipe: Mapping[str, Any],
) -> None:
    if not source_path.exists():
        raise ExportError(f"Rendered image does not exist: {source_path}")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ExportError("Pillow is required for local image export") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source_path) as opened:
            encoded = opened.convert("RGB")
            if export_format == "jpeg":
                encoded.save(
                    output_path,
                    format="JPEG",
                    quality=quality,
                    optimize=False,
                    progressive=False,
                    exif=build_jpeg_exif(recipe),
                )
            else:
                encoded.save(
                    output_path,
                    format="TIFF",
                    compression="tiff_deflate",
                    tiffinfo=build_tiff_info(recipe),
                )
    except (OSError, TypeError, ValueError) as exc:
        raise ExportError(f"Could not encode {export_format.upper()} export: {exc}") from exc


def _write_recipe_sidecar(output_path: Path, recipe: Mapping[str, Any]) -> Path:
    recipe_path = output_path.with_name(f"{output_path.name}.recipe.json")
    recipe_path.write_text(json.dumps(recipe, indent=2, sort_keys=True), encoding="utf-8")
    return recipe_path


# Backward-compatible public name for existing callers.
LocalJpegExportEngine = LocalImageExportEngine
