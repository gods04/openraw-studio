"""OpenRAW Native RAW processor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageMetadata, ImageRef, RawInspection
from openraw_studio.core.files import sha256_file, source_file_metadata
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawRenderRequest
from openraw_studio.raw.native.dng import DngMetadataError, DngMetadataReader
from openraw_studio.raw.native.jpeg import write_jpeg
from openraw_studio.raw.native.pipeline import build_native_render_plan
from openraw_studio.raw.native.preview import render_png_preview, render_preview_image


class NativeRawProcessor:
    """OpenRAW-owned RAW processor foundation.

    The class already participates in the application pipeline and records
    product-level engine identity. Pixel rendering is intentionally not faked.
    """

    def __init__(self, *, dng_reader: DngMetadataReader | None = None) -> None:
        self._dng_reader = dng_reader or DngMetadataReader()

    def engine_info(self) -> EngineInfo:
        return EngineInfo(
            name="openraw-native",
            version="0.1.0",
            backend="native-foundation",
            capabilities={
                "metadata": "filesystem-and-dng-v0.1",
                "preview": "simple-png-dng-v0.1",
                "base_render": "preview-derived-jpeg-dng-v0.1",
                "jpeg_export": "pillow-jpeg-v0.1",
                "white_balance": "dng-as-shot-neutral-v0.1",
                "camera_color_matrix": "dng-color-matrix-1-v0.1",
                "dng_metadata": True,
                "dng_uncompressed_strips": True,
                "recipe_planning": True,
                "owned_by_openraw": True,
            },
        )

    def inspect(self, source: ImageAsset) -> RawInspection:
        metadata = source_file_metadata(source.path)
        metadata["checksum_sha256"] = source.checksum_sha256 or sha256_file(source.path)
        metadata["native_engine_status"] = "foundation"
        if source.path.suffix.lower() == ".dng":
            try:
                dng_metadata = self._dng_reader.read(source.path).as_dict()
            except DngMetadataError as exc:
                metadata["dng_parse_error"] = str(exc)
            else:
                metadata["dng"] = dng_metadata
                metadata.update(_camera_metadata_from_dng(dng_metadata))
        return RawInspection(
            source=source,
            metadata=ImageMetadata(
                width=metadata.get("width"),
                height=metadata.get("height"),
                camera_make=metadata.get("camera_make"),
                camera_model=metadata.get("camera_model"),
                raw=metadata,
            ),
            engine=self.engine_info(),
        )

    def create_preview(
        self,
        source: ImageAsset,
        output_path: Path,
        max_dimension: int,
        recipe: Mapping[str, Any] | None = None,
    ) -> ImageRef:
        if output_path.suffix.lower() != ".png":
            raise RawProcessingError("OpenRAW Native preview currently writes PNG files; output path must end in .png")
        try:
            preview = render_png_preview(
                source.path,
                output_path,
                exposure=_recipe_exposure(recipe),
                max_dimension=max_dimension,
            )
        except (DngMetadataError, NotImplementedError, ValueError) as exc:
            raise RawProcessingError(f"OpenRAW Native preview failed: {exc}") from exc
        return ImageRef(
            path=output_path,
            width=preview.width,
            height=preview.height,
            color_space="preview-rgb",
            role="preview",
        )

    def render_base(self, request: RawRenderRequest) -> ImageRef:
        plan = build_native_render_plan(
            request.source.path,
            request.output_path,
            request.recipe,
            max_dimension=request.max_dimension,
        )
        if request.output_path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise RawProcessingError("OpenRAW Native export currently writes JPEG files; output path must end in .jpg")
        try:
            rendered = render_preview_image(plan.source_path, exposure=_recipe_exposure(request.recipe))
            write_jpeg(rendered, plan.output_path)
        except (DngMetadataError, NotImplementedError, RuntimeError, ValueError, OSError) as exc:
            raise RawProcessingError(f"OpenRAW Native export failed: {exc}") from exc
        return ImageRef(
            path=plan.output_path,
            width=rendered.width,
            height=rendered.height,
            color_space=request.color_space,
            role="export",
        )

    def export_intermediate(self, request: RawRenderRequest) -> ImageRef:
        return self.render_base(request)

def _camera_metadata_from_dng(dng: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "width": dng.get("width"),
        "height": dng.get("height"),
        "camera_make": dng.get("make"),
        "camera_model": dng.get("unique_camera_model") or dng.get("model"),
    }


def _recipe_exposure(recipe: Mapping[str, Any] | None) -> float:
    adjustments = (recipe or {}).get("adjustments", {})
    raw = adjustments.get("raw", {}) if isinstance(adjustments, Mapping) else {}
    value = raw.get("exposure", 0.0) if isinstance(raw, Mapping) else 0.0
    try:
        return max(-4.0, min(4.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
