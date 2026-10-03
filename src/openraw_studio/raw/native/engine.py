"""OpenRAW Native RAW processor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Mapping

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageMetadata, ImageRef, RawInspection
from openraw_studio.core.files import atomic_output_path, sha256_file, source_file_metadata
from openraw_studio.core.image_info import read_image_size
from openraw_studio.export.metadata import build_jpeg_exif, build_tiff_info
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawRenderRequest
from openraw_studio.raw.native.dng import DngMetadataError, DngMetadataReader
from openraw_studio.raw.native.jpeg import write_jpeg
from openraw_studio.raw.native.nikon import (
    NIKON_COMPRESSED_RAW,
    NikonDecodedPixelData,
    can_decode_nikon_34713_lossless,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_to_file,
)
from openraw_studio.raw.native.pipeline import build_native_render_plan
from openraw_studio.raw.native.preview import render_png_preview, render_preview_image
from openraw_studio.raw.native.tiff import write_tiff_rgb8


NIKON_RAW_EXTENSIONS = {".nef", ".nrw"}


class NativeRawProcessor:
    """OpenRAW-owned RAW processor foundation.

    The class already participates in the application pipeline and records
    product-level engine identity. Pixel rendering is intentionally not faked.
    """

    def __init__(self, *, dng_reader: DngMetadataReader | None = None) -> None:
        self._dng_reader = dng_reader or DngMetadataReader()
        self._nikon_34713_cache: tuple[Path, int, int, NikonDecodedPixelData] | None = None
        self._decode_lock = threading.Lock()

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
                "tiff_export": "pillow-rgb8-deflate-v0.1",
                "derivative_metadata": "safe-capture-no-gps-v0.1",
                "white_balance": "dng-as-shot-neutral-v0.1",
                "camera_color_matrix": "dng-color-matrix-1-to-linear-srgb-v0.2",
                "tone_adjustments": "exposure-contrast-highlights-shadows-temperature-tint-saturation-v0.1",
                "shadow_curve": "black-anchored-v0.2",
                "nikon_highlights": "neutral-white-balance-ceiling-v0.1",
                "packed_bayer_strips": "12-14-bit-row-aligned-v0.1",
                "nikon_full_resolution_export": "chunked-bilinear-v0.1",
                "nikon_fast_preview": "numpy-block-vectorized-v0.2",
                "nikon_in_memory_decode_cache": "single-source-stat-validated-v0.1",
                "nikon_decode_acceleration": "optional-numba-native-v0.1",
                "render_acceleration": "auto-opencl-cpu-fallback-v0.1",
                "interactive_preview": "scene-linear-proxy-v0.1",
                "dng_metadata": True,
                "nikon_nef_metadata": True,
                "nikon_nrw_metadata": True,
                "nikon_embedded_jpeg_preview": True,
                "nikon_makernote_compression_summary": True,
                "nikon_as_shot_white_balance": "maker-note-0x000c-v0.1",
                "nikon_camera_color": "exact-profile-d500-v0.1",
                "nikon_black_level": "maker-note-0x003d-with-border-fallback-v0.1",
                "nikon_orientation": "exif-orientation-v0.1",
                "nikon_34713_lossless_sensor_decode": True,
                "nikon_12bit_d20_nonsplit_sensor_decode": True,
                "nikon_guarded_tiff_sensor_decode": True,
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
        suffix = source.path.suffix.lower()
        if suffix in NIKON_RAW_EXTENSIONS and output_path.suffix.lower() in {".jpg", ".jpeg"}:
            return self._create_nikon_embedded_preview(source, output_path)
        if output_path.suffix.lower() != ".png":
            raise RawProcessingError("OpenRAW Native preview currently writes PNG files; output path must end in .png")
        try:
            adjustments = _recipe_render_adjustments(recipe)
            if metadata := self._read_supported_nikon_34713(source.path):
                decoded = self._decode_supported_nikon_34713(source.path, metadata)
                width, height = render_decoded_nikon_34713_to_file(
                    decoded,
                    output_path,
                    exposure=adjustments.exposure,
                    contrast=adjustments.contrast,
                    highlights=adjustments.highlights,
                    shadows=adjustments.shadows,
                    warmth=adjustments.warmth,
                    tint=adjustments.tint,
                    saturation=adjustments.saturation,
                    max_dimension=max_dimension,
                )
                return ImageRef(
                    path=output_path,
                    width=width,
                    height=height,
                    color_space="openraw-nikon-34713-rgb",
                    role="preview",
                )
            preview = render_png_preview(
                source.path,
                output_path,
                exposure=adjustments.exposure,
                contrast=adjustments.contrast,
                highlights=adjustments.highlights,
                shadows=adjustments.shadows,
                warmth=adjustments.warmth,
                tint=adjustments.tint,
                saturation=adjustments.saturation,
                max_dimension=max_dimension,
            )
        except (DngMetadataError, NotImplementedError, ValueError) as exc:
            prefix = "OpenRAW Native Nikon sensor preview failed" if suffix in NIKON_RAW_EXTENSIONS else "OpenRAW Native preview failed"
            raise RawProcessingError(f"{prefix}: {exc}") from exc
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
        output_suffix = request.output_path.suffix.lower()
        if output_suffix not in {".jpg", ".jpeg", ".tif", ".tiff"}:
            raise RawProcessingError("OpenRAW Native export path must end in .jpg, .jpeg, .tif, or .tiff")
        try:
            adjustments = _recipe_render_adjustments(request.recipe)
            jpeg_exif = build_jpeg_exif(request.recipe) if output_suffix in {".jpg", ".jpeg"} else None
            tiff_info = build_tiff_info(request.recipe) if output_suffix in {".tif", ".tiff"} else None
            if metadata := self._read_supported_nikon_34713(request.source.path):
                decoded = self._decode_supported_nikon_34713(request.source.path, metadata)
                width, height = render_decoded_nikon_34713_to_file(
                    decoded,
                    plan.output_path,
                    exposure=adjustments.exposure,
                    contrast=adjustments.contrast,
                    highlights=adjustments.highlights,
                    shadows=adjustments.shadows,
                    warmth=adjustments.warmth,
                    tint=adjustments.tint,
                    saturation=adjustments.saturation,
                    max_dimension=request.max_dimension,
                    jpeg_quality=request.quality,
                    jpeg_exif=jpeg_exif,
                    tiffinfo=tiff_info,
                    quality="full",
                )
                return ImageRef(
                    path=plan.output_path,
                    width=width,
                    height=height,
                    color_space=request.color_space,
                    role="export",
                )
            rendered = render_preview_image(
                plan.source_path,
                exposure=adjustments.exposure,
                contrast=adjustments.contrast,
                highlights=adjustments.highlights,
                shadows=adjustments.shadows,
                warmth=adjustments.warmth,
                tint=adjustments.tint,
                saturation=adjustments.saturation,
            )
            if output_suffix in {".jpg", ".jpeg"}:
                write_jpeg(rendered, plan.output_path, quality=request.quality, exif=jpeg_exif)
            else:
                write_tiff_rgb8(rendered, plan.output_path, tiffinfo=tiff_info)
        except (DngMetadataError, NotImplementedError, RuntimeError, ValueError, OSError) as exc:
            prefix = (
                "OpenRAW Native Nikon export failed"
                if request.source.path.suffix.lower() in NIKON_RAW_EXTENSIONS
                else "OpenRAW Native export failed"
            )
            raise RawProcessingError(f"{prefix}: {exc}") from exc
        return ImageRef(
            path=plan.output_path,
            width=rendered.width,
            height=rendered.height,
            color_space=request.color_space,
            role="export",
        )

    def export_intermediate(self, request: RawRenderRequest) -> ImageRef:
        return self.render_base(request)

    def _read_supported_nikon_34713(self, source_path: Path) -> Any | None:
        if source_path.suffix.lower() not in NIKON_RAW_EXTENSIONS:
            return None
        try:
            metadata = self._dng_reader.read(source_path)
        except DngMetadataError:
            return None
        summary = metadata.as_dict()
        if _optional_int(summary.get("compression")) != NIKON_COMPRESSED_RAW:
            return None
        if not can_decode_nikon_34713_lossless(metadata, source_path):
            return None
        return metadata

    def _decode_supported_nikon_34713(self, source_path: Path, metadata: Any) -> NikonDecodedPixelData:
        with self._decode_lock:
            return self._decode_supported_nikon_34713_locked(source_path, metadata)

    def _decode_supported_nikon_34713_locked(self, source_path: Path, metadata: Any) -> NikonDecodedPixelData:
        resolved = source_path.expanduser().resolve()
        stat = resolved.stat()
        cache = self._nikon_34713_cache
        if cache is not None:
            cached_path, cached_mtime_ns, cached_size, decoded = cache
            if cached_path == resolved and cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
                return decoded

        decoded = decode_nikon_34713_lossless(resolved, metadata)
        self._nikon_34713_cache = (resolved, stat.st_mtime_ns, stat.st_size, decoded)
        return decoded

    def _create_nikon_embedded_preview(self, source: ImageAsset, output_path: Path) -> ImageRef:
        if output_path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise RawProcessingError(
                "OpenRAW Native Nikon embedded previews currently write JPEG files; output path must end in .jpg"
            )
        try:
            preview = self._dng_reader.read_embedded_jpeg_preview(source.path)
            with atomic_output_path(output_path) as temporary_path:
                temporary_path.write_bytes(preview.data)
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
    highlights: float = 0.0
    shadows: float = 0.0
    warmth: float = 0.0
    tint: float = 0.0
    saturation: float = 0.0


def _recipe_render_adjustments(recipe: Mapping[str, Any] | None) -> RenderAdjustments:
    adjustments = (recipe or {}).get("adjustments", {})
    raw = adjustments.get("raw", {}) if isinstance(adjustments, Mapping) else {}
    if not isinstance(raw, Mapping):
        raw = {}
    return RenderAdjustments(
        exposure=_bounded_float(raw.get("exposure", 0.0), minimum=-4.0, maximum=4.0),
        contrast=_bounded_float(raw.get("contrast", 0.0), minimum=-1.0, maximum=1.0),
        highlights=_bounded_float(raw.get("highlights", 0.0), minimum=-1.0, maximum=1.0),
        shadows=_bounded_float(raw.get("shadows", 0.0), minimum=-1.0, maximum=1.0),
        warmth=_bounded_float(raw.get("warmth", 0.0), minimum=-1.0, maximum=1.0),
        tint=_bounded_float(raw.get("tint", 0.0), minimum=-1.0, maximum=1.0),
        saturation=_bounded_float(raw.get("saturation", 0.0), minimum=-1.0, maximum=1.0),
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
