"""Bounded native-resolution samples for Auto tone guards and noise advice."""

from dataclasses import dataclass, replace
from itertools import pairwise

import numpy as np

from openraw_studio.raw.native.malvar import _reflect
from openraw_studio.raw.native.chroma import reduce_color_noise
from openraw_studio.raw.native.luminance import reduce_luminance_noise
from openraw_studio.raw.native.noise import noise_radius
from openraw_studio.raw.native.nikon import (
    NikonDecodedPixelData,
    _render_crop,
    render_decoded_nikon_34713_image,
)


@dataclass(frozen=True)
class NativeAutoSamples:
    decoded: NikonDecodedPixelData
    locations: tuple[tuple[int, int], ...]
    core_size: int
    source_size: tuple[int, int]
    grid_count: int

    def render(self, adjustments):
        return self.render_tiles(adjustments).reshape(-1, 3)

    def render_tiles(self, adjustments):
        values = dict(adjustments)
        color = values.pop("color_noise", 0)
        luminance = values.pop("luminance_noise", 0)
        rendered = render_decoded_nikon_34713_image(
            self.decoded, quality="full", **values
        )
        span = self.core_size + 12
        pixels = np.frombuffer(rendered.rgb_bytes, np.uint8).reshape(-1, span, span, 3)
        radius = noise_radius(color_noise=color, luminance_noise=luminance)
        if radius == 0:
            return pixels[:, 6:-6, 6:-6]

        # Two RAW pixels support demosaic; up to four RGB pixels support both
        # noise stages. At real image edges, replicate RGB as the full render does.
        width, height = self.source_size
        offsets = np.arange(-radius, self.core_size + radius)
        tiles = np.stack(
            [
                pixels[index][
                    (np.clip(y + offsets, 0, height - 1) - y + 6)[:, None],
                    (np.clip(x + offsets, 0, width - 1) - x + 6)[None, :],
                ]
                for index, (x, y) in enumerate(self.locations)
            ]
        )
        span = self.core_size + 2 * radius
        filtered = reduce_luminance_noise(tiles.reshape(-1, span, 3), luminance)
        if luminance != 0 and color != 0:
            # Each stage replicates the actual image edge, not the first stage's
            # filtered artificial halo. Otherwise combined samples differ at edges.
            tiles = filtered.reshape(-1, span, span, 3)
            filtered = np.stack([
                tiles[index][
                    (np.clip(y + offsets, 0, height - 1) - y + radius)[:, None],
                    (np.clip(x + offsets, 0, width - 1) - x + radius)[None, :],
                ]
                for index, (x, y) in enumerate(self.locations)
            ]).reshape(-1, span, 3)
        filtered = reduce_color_noise(filtered, color)
        return filtered.reshape(-1, span, span, 3)[:, radius:-radius, radius:-radius]


def prepare_native_auto_samples(decoded):
    """Sample a fixed grid plus bright Bayer sites, without retaining the full RAW.

    This is a conservative sampled guard, not exhaustive full-image QC. Bright
    patches are deliberately overrepresented; only clipping/shadow loss is tested
    here, not scene classification or a target median/brightness.
    """
    left, top, width, height = _render_crop(decoded)
    raw = np.frombuffer(decoded.raw_bytes, dtype="<u2").reshape(
        decoded.height, decoded.width
    )
    cropped = raw[top : top + height, left : left + width]
    core = min(32, width, height)
    core -= core % 2
    locations = {}

    def add(x, y):
        x = max(0, min(width - core, int(x) - core // 2)) & ~1
        y = max(0, min(height - core, int(y) - core // 2)) & ~1
        locations[x, y] = None

    for y in np.linspace(core // 2, height - core // 2, 8):
        for x in np.linspace(core // 2, width - core // 2, 8):
            add(x, y)
    grid_count = len(locations)
    # Each CFA plane votes separately so a small colored light is not averaged
    # away by the Fit proxy. Selection uses decoded sensor data, not filenames/ISO.
    for row in range(2):
        for col in range(2):
            plane = cropped[row::2, col::2]
            ys = np.linspace(0, plane.shape[0], min(4, plane.shape[0]) + 1, dtype=int)
            xs = np.linspace(0, plane.shape[1], min(4, plane.shape[1]) + 1, dtype=int)
            for y0, y1 in pairwise(ys):
                for x0, x1 in pairwise(xs):
                    block = plane[y0:y1, x0:x1]
                    dy, dx = np.unravel_index(np.argmax(block), block.shape)
                    add(2 * (x0 + dx) + col, 2 * (y0 + dy) + row)
    return _pack_samples(decoded, cropped, locations, core, grid_count)


def prepare_native_noise_samples(decoded, tone_samples):
    """Keep a denser, non-overlapping grid for sparse noise-evidence retries.

    Reuse the tone guard's bright locations instead of scanning the RAW twice.
    Only a bounded atlas survives preparation, never the full decoded image.
    """
    left, top, width, height = _render_crop(decoded)
    core = tone_samples.core_size
    nx, ny = min(16, width // core), min(16, height // core)
    if core < 16 or nx * ny <= tone_samples.grid_count:
        return None
    locations = {
        (int(x) & ~1, int(y) & ~1): None
        for y in np.linspace(0, height - core, ny)
        for x in np.linspace(0, width - core, nx)
    }
    grid_count = len(locations)
    locations.update(dict.fromkeys(tone_samples.locations[tone_samples.grid_count :]))
    raw = np.frombuffer(decoded.raw_bytes, dtype="<u2").reshape(
        decoded.height, decoded.width
    )
    cropped = raw[top : top + height, left : left + width]
    return _pack_samples(decoded, cropped, locations, core, grid_count)


def _pack_samples(decoded, cropped, locations, core, grid_count):
    height, width = cropped.shape
    offsets = np.arange(-6, core + 6)
    tiles = [
        cropped[
            _reflect(y + offsets, height)[:, None],
            _reflect(x + offsets, width)[None, :],
        ]
        for x, y in locations
    ]
    atlas = np.stack(tiles).reshape(-1, core + 12)
    # _render_crop aligns the original crop and every sample to an even CFA phase.
    packed = replace(
        decoded,
        width=atlas.shape[1],
        height=atlas.shape[0],
        raw_bytes=atlas.astype("<u2", copy=False).tobytes(),
        orientation=1,
        compression_setup=replace(
            decoded.compression_setup,
            active_area=(0, 0, atlas.shape[1], atlas.shape[0]),
        ),
    )
    return NativeAutoSamples(
        packed, tuple(locations), core, (width, height), grid_count
    )
