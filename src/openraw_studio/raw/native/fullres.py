"""Chunked full-resolution Bayer rendering for native RAW exports."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from openraw_studio.raw.native.profiles import Matrix3
from openraw_studio.raw.native.tonal import apply_tonal_regions_array


@dataclass(frozen=True)
class FullResolutionRgbImage:
    """Packed 8-bit RGB output from the full-resolution Bayer path."""

    width: int
    height: int
    rgb_bytes: bytes


def render_bayer_full_resolution_rgb8(
    raw_bytes: bytes,
    *,
    source_width: int,
    source_height: int,
    crop: tuple[int, int, int, int],
    cfa_pattern: tuple[int, ...] | None,
    black_levels: tuple[int, int, int, int],
    white_level: int,
    channel_gains: tuple[float, float, float],
    camera_to_linear_srgb: Matrix3 | None,
    contrast: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    saturation: float = 0.0,
    chunk_rows: int = 256,
    use_gpu: bool = True,
    use_compiled: bool = True,
    highlight_ceiling: float | None = None,
    demosaic: str = "bilinear",
) -> FullResolutionRgbImage:
    """Render Bayer using GPU, compiled CPU chunks, or the NumPy fallback.

    Disable both use_gpu and use_compiled to select the reference implementation.
    """

    np = _numpy()
    if source_width <= 1 or source_height <= 1:
        raise ValueError("full-resolution Bayer rendering needs an image larger than 1 x 1")
    if len(raw_bytes) != source_width * source_height * 2:
        raise ValueError("full-resolution Bayer buffer size does not match its dimensions")
    if white_level <= 0:
        raise ValueError("white_level must be greater than zero")
    if chunk_rows <= 0:
        raise ValueError("chunk_rows must be greater than zero")
    if highlight_ceiling is not None and (not math.isfinite(highlight_ceiling) or highlight_ceiling < 0):
        raise ValueError("highlight_ceiling must be finite and non-negative")
    if len(black_levels) != 4:
        raise ValueError("full-resolution Bayer rendering needs four position black levels")
    if len(channel_gains) != 3 or any(
        not math.isfinite(float(gain)) or gain < 0.0 for gain in channel_gains
    ):
        raise ValueError("full-resolution Bayer channel gains must be three finite non-negative values")

    left, top, width, height = crop
    if left < 0 or top < 0 or width <= 1 or height <= 1:
        raise ValueError("full-resolution Bayer crop is invalid")
    if left + width > source_width or top + height > source_height:
        raise ValueError("full-resolution Bayer crop exceeds the source image")

    pattern = _validated_cfa(cfa_pattern)
    from openraw_studio.raw.native.malvar import STANDARD_BAYER

    if demosaic not in ("bilinear", "malvar") or (demosaic == "malvar" and pattern not in STANDARD_BAYER):
        raise ValueError("MHC requires a standard Bayer layout and a known interpolation method")
    matrix = camera_to_linear_srgb or (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    if len(matrix) != 3 or any(
        len(row) != 3 or any(not math.isfinite(float(value)) for value in row)
        for row in matrix
    ):
        raise ValueError("camera_to_linear_srgb must be a finite 3 x 3 matrix")
    if any(level < 0 or level >= white_level for level in black_levels):
        raise ValueError("Bayer black levels must be between zero and white_level")
    if use_gpu:
        from openraw_studio.raw.native.acceleration import color_parameters, disable_gpu, get_gpu

        gpu = get_gpu()
        if gpu is not None:
            try:
                params = color_parameters(
                    matrix,
                    channel_gains,
                    contrast=contrast,
                    highlights=highlights,
                    shadows=shadows,
                    saturation=saturation,
                    highlight_ceiling=highlight_ceiling,
                )
                args = (raw_bytes, source_width, crop, pattern, black_levels, white_level, params)
                rendered = gpu.bayer(*args, method=demosaic) if demosaic != "bilinear" else gpu.bayer(*args)
                return FullResolutionRgbImage(width, height, rendered.tobytes())
            except Exception:
                disable_gpu()
    source = np.frombuffer(raw_bytes, dtype="<u2").reshape(source_height, source_width)
    output = np.empty((height, width, 3), dtype=np.uint8)
    if use_compiled:
        from openraw_studio.raw.native import compiled_bayer, compiled_tone
        from openraw_studio.raw.native.acceleration import color_parameters

        # Interpolation already applies channel gains, just as the reference does.
        params = color_parameters(
            matrix, (1, 1, 1), contrast=contrast, highlights=highlights,
            shadows=shadows, saturation=saturation, highlight_ceiling=highlight_ceiling,
        )

    for core_start in range(0, height, chunk_rows):
        core_end = min(height, core_start + chunk_rows)
        camera = None
        if use_compiled:
            args = (source, crop, core_start, core_end, pattern, black_levels, white_level, channel_gains)
            camera = compiled_bayer.render_chunk(*args, method=demosaic) if demosaic != "bilinear" else compiled_bayer.render_chunk(*args)
        if camera is not None:
            rendered = compiled_tone.render(camera, params)
            if rendered is not None:
                output[core_start:core_end] = rendered
                continue
            camera_planes = [camera[:, :, channel] for channel in range(3)]
        else:
            if demosaic == "malvar":
                from openraw_studio.raw.native.malvar import demosaic_chunk

                camera_planes = demosaic_chunk(source, crop, core_start, core_end, pattern, black_levels, white_level, channel_gains)
            else:
                camera_planes = _demosaic_numpy(
                    np, source, crop, core_start, core_end, pattern, black_levels, white_level, channel_gains,
                )

        if highlight_ceiling is not None:
            for plane in camera_planes:
                np.minimum(plane, highlight_ceiling, out=plane)
        red, green, blue = _camera_to_output_planes(np, camera_planes, matrix)
        _tone_encode_planes(
            np,
            (red, green, blue),
            contrast=contrast,
            highlights=highlights,
            shadows=shadows,
            saturation=saturation,
        )
        rgb = np.stack((red, green, blue), axis=2)
        rgb *= 255.0
        np.rint(rgb, out=rgb)
        output[core_start:core_end] = rgb.astype(np.uint8)

    return FullResolutionRgbImage(width=width, height=height, rgb_bytes=output.tobytes())


def _demosaic_numpy(np, source, crop, start, end, pattern, black_levels, white_level, channel_gains):
    """Retain the original sparse-convolution path as a reference and fallback."""
    left, top, width, height = crop
    halo_start, halo_end = max(0, start - 1), min(height, end + 1)
    chunk = source[top + halo_start : top + halo_end, left : left + width]
    core_offset, core_count = start - halo_start, end - start
    camera_planes = []
    for channel_code, kernel in ((0, _RED_BLUE_KERNEL), (1, _GREEN_KERNEL), (2, _RED_BLUE_KERNEL)):
        sparse = np.zeros(chunk.shape, dtype=np.float32)
        mask = np.zeros(chunk.shape, dtype=np.float32)
        for position, sample_channel in enumerate(pattern):
            if sample_channel != channel_code:
                continue
            row_parity, column_parity = divmod(position, 2)
            first_row = (row_parity - ((top + halo_start) & 1)) & 1
            first_column = (column_parity - (left & 1)) & 1
            black_level = black_levels[position]
            samples = chunk[first_row::2, first_column::2].astype(np.float32)
            samples -= float(black_level)
            samples /= float(max(1, white_level - black_level))
            np.clip(samples, 0.0, 1.0, out=samples)
            samples *= float(channel_gains[channel_code])
            sparse[first_row::2, first_column::2] = samples
            mask[first_row::2, first_column::2] = 1.0
        interpolated = _normalized_convolution(np, sparse, mask, kernel)
        camera_planes.append(interpolated[core_offset : core_offset + core_count])
    return camera_planes


_RED_BLUE_KERNEL = (
    (-1, -1, 1.0),
    (-1, 0, 2.0),
    (-1, 1, 1.0),
    (0, -1, 2.0),
    (0, 0, 4.0),
    (0, 1, 2.0),
    (1, -1, 1.0),
    (1, 0, 2.0),
    (1, 1, 1.0),
)
_GREEN_KERNEL = (
    (-1, 0, 1.0),
    (0, -1, 1.0),
    (0, 0, 4.0),
    (0, 1, 1.0),
    (1, 0, 1.0),
)


def _normalized_convolution(np: Any, sparse: Any, mask: Any, kernel: tuple[tuple[int, int, float], ...]) -> Any:
    height, width = sparse.shape
    weighted = np.zeros_like(sparse)
    weights = np.zeros_like(sparse)
    for row_offset, column_offset, weight in kernel:
        destination_top = max(0, -row_offset)
        destination_bottom = min(height, height - row_offset)
        destination_left = max(0, -column_offset)
        destination_right = min(width, width - column_offset)
        if destination_top >= destination_bottom or destination_left >= destination_right:
            continue
        source_top = destination_top + row_offset
        source_bottom = destination_bottom + row_offset
        source_left = destination_left + column_offset
        source_right = destination_right + column_offset
        destination = (
            slice(destination_top, destination_bottom),
            slice(destination_left, destination_right),
        )
        source = (
            slice(source_top, source_bottom),
            slice(source_left, source_right),
        )
        weighted[destination] += sparse[source] * weight
        weights[destination] += mask[source] * weight
    np.divide(weighted, weights, out=weighted, where=weights > 0.0)
    return weighted


def _camera_to_output_planes(np: Any, camera_planes: list[Any], matrix: Matrix3) -> tuple[Any, Any, Any]:
    camera_red, camera_green, camera_blue = camera_planes
    return tuple(
        (camera_red * matrix[row][0])
        + (camera_green * matrix[row][1])
        + (camera_blue * matrix[row][2])
        for row in range(3)
    )  # type: ignore[return-value]


def _tone_encode_planes(
    np: Any,
    planes: tuple[Any, Any, Any],
    *,
    contrast: float,
    highlights: float,
    shadows: float,
    saturation: float,
) -> None:
    contrast_factor = 1.0 + _clamp(contrast, -1.0, 1.0) * 0.75
    highlight_value = _clamp(highlights, -1.0, 1.0)
    shadow_value = _clamp(shadows, -1.0, 1.0)
    for channel in planes:
        channel -= 0.18
        channel *= contrast_factor
        channel += 0.18
        apply_tonal_regions_array(channel, highlights=highlight_value, shadows=shadow_value)
        np.clip(channel, 0.0, 1.0, out=channel)
        np.power(channel, 1.0 / 2.2, out=channel)

    saturation_factor = 1.0 + _clamp(saturation, -1.0, 1.0) * 0.75
    if saturation_factor != 1.0:
        red, green, blue = planes
        luma = ((54.0 * red) + (183.0 * green) + (19.0 * blue)) / 256.0
        for channel in planes:
            channel -= luma
            channel *= saturation_factor
            channel += luma
            np.clip(channel, 0.0, 1.0, out=channel)


def _validated_cfa(pattern: tuple[int, ...] | None) -> tuple[int, int, int, int]:
    if pattern is None or len(pattern) < 4:
        return 0, 1, 1, 2
    result = tuple(int(value) for value in pattern[:4])
    if sorted(result) != [0, 1, 1, 2]:
        raise ValueError(f"unsupported 2 x 2 Bayer CFA pattern: {result}")
    return result  # type: ignore[return-value]


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as exc:
        raise RuntimeError("NumPy is required for full-resolution native RAW export") from exc
    return numpy


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))
