"""Preview rendering helpers for OpenRAW Native."""

from __future__ import annotations

from pathlib import Path

from openraw_studio.raw.native.decoder import NativeRawDecoder
from openraw_studio.raw.native.color import apply_as_shot_neutral, apply_camera_matrix
from openraw_studio.raw.native.dng import DngMetadataError, DngMetadataReader
from openraw_studio.raw.native.demosaic import demosaic_simple
from openraw_studio.raw.native.nikon import (
    NIKON_COMPRESSED_RAW,
    NikonCompressionError,
    can_decode_nikon_34713_lossless,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.png import write_png
from openraw_studio.raw.native.sensor import normalize_sensor_data
from openraw_studio.raw.native.tone import PreviewRgbImage, tone_map_preview


NIKON_RAW_EXTENSIONS = {".nef", ".nrw"}


def render_png_preview(
    source_path: Path,
    output_path: Path,
    *,
    apply_color: bool = True,
    exposure: float = 0.0,
    contrast: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    saturation: float = 0.0,
    max_dimension: int | None = None,
) -> PreviewRgbImage:
    """Render the current simple native DNG pipeline to a PNG preview."""

    preview = render_preview_image(
        source_path,
        apply_color=apply_color,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        saturation=saturation,
        max_dimension=max_dimension,
    )
    write_png(preview, output_path)
    return preview


def render_ppm_preview(source_path: Path, output_path: Path) -> PreviewRgbImage:
    """Render the current simple native DNG pipeline to a binary PPM preview."""

    preview = render_preview_image(source_path)
    write_ppm(preview, output_path)
    return preview


def render_preview_image(
    source_path: Path,
    *,
    apply_color: bool = True,
    exposure: float = 0.0,
    contrast: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    saturation: float = 0.0,
    max_dimension: int | None = None,
) -> PreviewRgbImage:
    """Render a source RAW file into an 8-bit RGB preview image."""

    if preview := _render_nikon_34713_preview_image(
        source_path,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        saturation=saturation,
        max_dimension=max_dimension,
    ):
        return preview

    sensor = NativeRawDecoder().decode(source_path)
    linear_sensor = normalize_sensor_data(sensor)
    metadata = sensor.metadata or {}
    if apply_color:
        linear_sensor = apply_as_shot_neutral(linear_sensor, metadata.get("as_shot_neutral"))
    linear_rgb = demosaic_simple(linear_sensor)
    if apply_color:
        linear_rgb = apply_camera_matrix(linear_rgb, metadata.get("color_matrix_1"))
    preview = tone_map_preview(
        linear_rgb,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        saturation=saturation,
    )
    return resize_preview(preview, max_dimension=max_dimension)


def _render_nikon_34713_preview_image(
    source_path: Path,
    *,
    exposure: float,
    contrast: float,
    highlights: float,
    shadows: float,
    warmth: float,
    saturation: float,
    max_dimension: int | None,
) -> PreviewRgbImage | None:
    if source_path.suffix.lower() not in NIKON_RAW_EXTENSIONS:
        return None
    try:
        metadata = DngMetadataReader().read(source_path)
    except DngMetadataError:
        return None
    summary = metadata.as_dict()
    if _optional_int(summary.get("compression")) != NIKON_COMPRESSED_RAW:
        return None
    if not can_decode_nikon_34713_lossless(metadata):
        return None

    decoded = decode_nikon_34713_lossless(source_path, metadata)
    rendered = render_decoded_nikon_34713_image(
        decoded,
        max_dimension=max_dimension,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        saturation=saturation,
    )
    return PreviewRgbImage(
        width=rendered.width,
        height=rendered.height,
        pixels=_rgb_bytes_to_pixels(rendered.rgb_bytes),
        transfer=rendered.transfer,
    )


def _rgb_bytes_to_pixels(payload: bytes) -> tuple[tuple[int, int, int], ...]:
    if len(payload) % 3:
        raise NikonCompressionError("Nikon preview RGB payload is not divisible by three")
    return tuple((payload[index], payload[index + 1], payload[index + 2]) for index in range(0, len(payload), 3))


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, tuple):
        if len(value) != 1:
            return None
        value = value[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resize_preview(image: PreviewRgbImage, *, max_dimension: int | None) -> PreviewRgbImage:
    """Downsample an 8-bit preview with nearest-neighbor sampling."""

    if max_dimension is None:
        return image
    if max_dimension <= 0:
        raise ValueError("max_dimension must be greater than zero")
    longest = max(image.width, image.height)
    if longest <= max_dimension:
        return image

    scale = max_dimension / float(longest)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    pixels = tuple(
        image.pixel_at(min(image.height - 1, int(row / scale)), min(image.width - 1, int(column / scale)))
        for row in range(height)
        for column in range(width)
    )
    return PreviewRgbImage(width=width, height=height, pixels=pixels, transfer=image.transfer)


def write_ppm(image: PreviewRgbImage, output_path: Path) -> Path:
    """Write a binary PPM file."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"P6\n{image.width} {image.height}\n255\n".encode("ascii")
    payload = bytes(channel for pixel in image.pixels for channel in pixel)
    output_path.write_bytes(header + payload)
    return output_path
