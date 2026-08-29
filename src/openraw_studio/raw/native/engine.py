"""OpenRAW Native RAW processor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageMetadata, ImageRef, RawInspection
from openraw_studio.core.files import sha256_file, source_file_metadata
from openraw_studio.core.image_info import read_image_size
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawRenderRequest
from openraw_studio.raw.native.dng import DngMetadataError, DngMetadataReader
from openraw_studio.raw.native.jpeg import write_jpeg
from openraw_studio.raw.native.pipeline import build_native_render_plan
from openraw_studio.raw.native.preview import render_png_preview, render_preview_image


NIKON_RAW_EXTENSIONS = {".nef", ".nrw"}


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
                "preview": "simple-png-dng-and-nikon-embedded-jpeg-v0.1",
                "base_render": "preview-derived-jpeg-dng-v0.1",
                "jpeg_export": "pillow-jpeg-v0.1",
                "white_balance": "dng-as-shot-neutral-v0.1",
                "camera_color_matrix": "dng-color-matrix-1-v0.1",
                "tone_adjustments": "exposure-contrast-warmth-v0.1",
                "dng_metadata": True,
                "nikon_nef_metadata": True,
                "nikon_nrw_metadata": True,
                "nikon_embedded_jpeg_preview": True,
                "dng_uncompressed_strips": True,
                "dng_uncompressed_tiles": True,
                "recipe_planning": True,
                "owned_by_openraw": True,
            },
        )

    def inspect(self, source: ImageAsset) -> RawInspection:
        metadata = source_file_metadata(source.path)
        metadata["checksum_sha256"] = source.checksum_sha256 or sha256_file(source.path)
        metadata["native_engine_status"] = "foundation"
        suffix = source.path.suffix.lower()
        if suffix in {".dng", *NIKON_RAW_EXTENSIONS}:
            try:
                raw_metadata = self._dng_reader.read(source.path).as_dict()
            except DngMetadataError as exc:
                metadata["raw_parse_error"] = str(exc)
                if suffix == ".dng":
                    metadata["dng_parse_error"] = str(exc)
            else:
                if suffix == ".dng":
                    metadata["dng"] = raw_metadata
                    metadata["raw_format"] = "dng"
                else:
                    metadata["nikon_raw"] = raw_metadata
                    metadata["raw_format"] = "nikon-nef" if suffix == ".nef" else "nikon-nrw"
                metadata["raw_container"] = "tiff"
                metadata.update(_image_metadata_from_tiff_summary(raw_metadata))
        return RawInspection(
            source=source,
            metadata=ImageMetadata(
                width=metadata.get("width"),
                height=metadata.get("height"),
                camera_make=metadata.get("camera_make"),
                camera_model=metadata.get("camera_model"),
                lens_model=metadata.get("lens_model"),
                iso=_optional_int(metadata.get("iso")),
                exposure_time=_optional_str(metadata.get("exposure_time")),
                aperture=_optional_float(metadata.get("aperture")),
                focal_length_mm=_optional_float(metadata.get("focal_length_mm")),
                captured_at=_optional_str(metadata.get("captured_at")),
                orientation=_optional_str(metadata.get("orientation")),
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
        if source.path.suffix.lower() in NIKON_RAW_EXTENSIONS:
            return self._create_nikon_embedded_preview(source, output_path)
        if output_path.suffix.lower() != ".png":
            raise RawProcessingError("OpenRAW Native preview currently writes PNG files; output path must end in .png")
        try:
            adjustments = _recipe_render_adjustments(recipe)
            preview = render_png_preview(
                source.path,
                output_path,
                exposure=adjustments.exposure,
                contrast=adjustments.contrast,
                warmth=adjustments.warmth,
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
        if request.source.path.suffix.lower() in NIKON_RAW_EXTENSIONS:
            raise RawProcessingError("OpenRAW Native can import Nikon RAW metadata, but NEF/NRW export rendering is not implemented yet")
        plan = build_native_render_plan(
            request.source.path,
            request.output_path,
            request.recipe,
            max_dimension=request.max_dimension,
        )
        if request.output_path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise RawProcessingError("OpenRAW Native export currently writes JPEG files; output path must end in .jpg")
        try:
            adjustments = _recipe_render_adjustments(request.recipe)
            rendered = render_preview_image(
                plan.source_path,
                exposure=adjustments.exposure,
                contrast=adjustments.contrast,
                warmth=adjustments.warmth,
            )
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

    def _create_nikon_embedded_preview(self, source: ImageAsset, output_path: Path) -> ImageRef:
        if output_path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise RawProcessingError(
                "OpenRAW Native Nikon embedded previews currently write JPEG files; output path must end in .jpg"
            )
        try:
            preview = self._dng_reader.read_embedded_jpeg_preview(source.path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(preview.data)
            width, height = read_image_size(output_path)
        except (DngMetadataError, OSError, ValueError) as exc:
            raise RawProcessingError(f"OpenRAW Native Nikon preview failed: {exc}") from exc

        return ImageRef(
            path=output_path,
            width=width or preview.width or 0,
            height=height or preview.height or 0,
            color_space="embedded-jpeg",
            role="preview",
        )


def _image_metadata_from_tiff_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "width": summary.get("width"),
        "height": summary.get("height"),
        "camera_make": summary.get("make"),
        "camera_model": summary.get("unique_camera_model") or summary.get("model"),
        "lens_model": summary.get("lens_model"),
        "iso": summary.get("iso"),
        "exposure_time": summary.get("exposure_time"),
        "aperture": summary.get("aperture"),
        "focal_length_mm": summary.get("focal_length_mm"),
        "captured_at": summary.get("captured_at"),
        "orientation": summary.get("orientation"),
    }


@dataclass(frozen=True)
class RenderAdjustments:
    exposure: float = 0.0
    contrast: float = 0.0
    warmth: float = 0.0


def _recipe_render_adjustments(recipe: Mapping[str, Any] | None) -> RenderAdjustments:
    adjustments = (recipe or {}).get("adjustments", {})
    raw = adjustments.get("raw", {}) if isinstance(adjustments, Mapping) else {}
    if not isinstance(raw, Mapping):
        raw = {}
    return RenderAdjustments(
        exposure=_bounded_float(raw.get("exposure", 0.0), minimum=-4.0, maximum=4.0),
        contrast=_bounded_float(raw.get("contrast", 0.0), minimum=-1.0, maximum=1.0),
        warmth=_bounded_float(raw.get("warmth", 0.0), minimum=-1.0, maximum=1.0),
    )


def _bounded_float(value: Any, *, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
