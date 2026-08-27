"""Preview rendering helpers for OpenRAW Native."""

from __future__ import annotations

from pathlib import Path

from openraw_studio.raw.native.decoder import NativeRawDecoder
from openraw_studio.raw.native.color import apply_as_shot_neutral, apply_camera_matrix
from openraw_studio.raw.native.demosaic import demosaic_simple
from openraw_studio.raw.native.png import write_png
from openraw_studio.raw.native.sensor import normalize_sensor_data
from openraw_studio.raw.native.tone import PreviewRgbImage, tone_map_preview


def render_png_preview(
    source_path: Path,
    output_path: Path,
    *,
    apply_color: bool = True,
    exposure: float = 0.0,
    max_dimension: int | None = None,
) -> PreviewRgbImage:
    """Render the current simple native DNG pipeline to a PNG preview."""

    preview = render_preview_image(
        source_path,
        apply_color=apply_color,
        exposure=exposure,
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
    max_dimension: int | None = None,
) -> PreviewRgbImage:
    """Render a source RAW file into an 8-bit RGB preview image."""

    sensor = NativeRawDecoder().decode(source_path)
    linear_sensor = normalize_sensor_data(sensor)
    metadata = sensor.metadata or {}
    if apply_color:
        linear_sensor = apply_as_shot_neutral(linear_sensor, metadata.get("as_shot_neutral"))
    linear_rgb = demosaic_simple(linear_sensor)
    if apply_color:
        linear_rgb = apply_camera_matrix(linear_rgb, metadata.get("color_matrix_1"))
    preview = tone_map_preview(linear_rgb, exposure=exposure)
    return resize_preview(preview, max_dimension=max_dimension)


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
