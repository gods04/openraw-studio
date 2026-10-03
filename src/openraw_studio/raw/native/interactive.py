"""Prepared, scene-linear display proxies for disk-free slider rendering."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from openraw_studio.raw.native.acceleration import color_parameters, render_tone
from openraw_studio.raw.native.nikon import _apply_exif_orientation, _render_crop


@dataclass
class InteractivePhoto:
    pixels: np.ndarray
    matrix: np.ndarray
    gains: tuple[float, float, float]
    orientation: int = 1
    linear_saturation: bool = False

    def resized(self, max_dimension):
        """Derive an unedited scene-linear proxy, retaining color and orientation."""
        if max_dimension < 1:
            raise ValueError("Proxy dimension must be positive")
        return replace(self, pixels=_resize_linear(self.pixels, max_dimension))

    def render(self, adjustments):
        warmth = np.clip(adjustments.get("warmth", 0), -1, 1)
        tint = np.clip(adjustments.get("tint", 0), -1, 1)
        exposure = 2.0 ** np.clip(adjustments.get("exposure", 0), -4, 4)
        gains = (
            self.gains[0] * (1 + warmth * 0.12) * (1 + tint * 0.08) * exposure,
            self.gains[1] * (1 + warmth * 0.03) * (1 - tint * 0.12) * exposure,
            self.gains[2] * (1 - warmth * 0.12) * (1 + tint * 0.08) * exposure,
        )
        params = color_parameters(
            self.matrix,
            gains,
            linear_saturation=self.linear_saturation,
            highlight_ceiling=None if self.linear_saturation else min(gains),
            **{
                k: adjustments.get(k, 0)
                for k in ("contrast", "highlights", "shadows", "saturation")
            },
        )
        rgb, backend = render_tone(self.pixels, params)
        image = _apply_exif_orientation(Image.fromarray(rgb), self.orientation)
        return image, backend


def _resize_linear(pixels, max_dimension):
    height, width = pixels.shape[:2]
    scale = min(1, max_dimension / max(width, height))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if size == (width, height):
        return np.ascontiguousarray(pixels, dtype=np.float32)
    return np.stack(
        [
            np.asarray(
                Image.fromarray(pixels[:, :, channel]).resize(
                    size, Image.Resampling.BOX
                )
            )
            for channel in range(3)
        ],
        axis=-1,
    ).astype(np.float32)


def prepare_interactive_photo(processor, source: Path, *, max_dimension=960):
    metadata = processor._read_supported_nikon_34713(source)
    if metadata is not None:
        decoded = processor._decode_supported_nikon_34713(source, metadata)
        left, top, width, height = _render_crop(decoded)
        raw = np.frombuffer(decoded.raw_bytes, dtype="<u2").reshape(
            decoded.height, decoded.width
        )
        cropped = raw[top : top + height, left : left + width]
        cfa = decoded.cfa_pattern or (0, 1, 1, 2)
        channels = []
        for channel in range(3):
            planes = []
            for pos, sample_channel in enumerate(cfa):
                if channel == sample_channel:
                    row, col = divmod(pos, 2)
                    black = decoded.black_levels[pos]
                    plane = cropped[row::2, col::2].astype(np.float32)
                    planes.append(
                        np.clip((plane - black) / (decoded.white_level - black), 0, 1)
                    )
            channels.append(sum(planes) / len(planes))
        pixels = _resize_linear(np.stack(channels, axis=-1), max_dimension)
        matrix = (
            decoded.camera_profile.camera_to_linear_srgb
            if decoded.camera_profile
            else np.eye(3)
        )
        gains = decoded.white_balance.gains if decoded.white_balance else (1, 1, 1)
        return InteractivePhoto(
            pixels, np.asarray(matrix, dtype=np.float32), gains, decoded.orientation
        )

    from openraw_studio.raw.native.color import (
        apply_as_shot_neutral,
        apply_camera_matrix,
    )
    from openraw_studio.raw.native.decoder import NativeRawDecoder
    from openraw_studio.raw.native.demosaic import demosaic_simple
    from openraw_studio.raw.native.sensor import normalize_sensor_data

    sensor = NativeRawDecoder().decode(source)
    linear = normalize_sensor_data(sensor)
    meta = sensor.metadata or {}
    linear = apply_as_shot_neutral(linear, meta.get("as_shot_neutral"))
    rgb = apply_camera_matrix(demosaic_simple(linear), meta.get("color_matrix_1"))
    pixels = np.asarray(rgb.pixels, dtype=np.float32).reshape(rgb.height, rgb.width, 3)
    return InteractivePhoto(
        _resize_linear(pixels, max_dimension),
        np.eye(3, dtype=np.float32),
        (1, 1, 1),
        linear_saturation=True,
    )
