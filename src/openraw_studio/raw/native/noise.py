"""Shared ordering and spatial footprint of rendered noise controls."""

from openraw_studio.raw.native.chroma import reduce_color_noise
from openraw_studio.raw.native.luminance import reduce_luminance_noise


def noise_radius(*, color_noise=0, luminance_noise=0):
    return 2 * (int(color_noise != 0) + int(luminance_noise != 0))


def reduce_noise(pixels, *, color_noise=0, luminance_noise=0, use_gpu=True, use_compiled=True):
    # Color advice measures brightness-preserving changes after luminance filtering.
    pixels = reduce_luminance_noise(pixels, luminance_noise, use_gpu=use_gpu, use_compiled=use_compiled)
    return reduce_color_noise(pixels, color_noise, use_gpu=use_gpu, use_compiled=use_compiled)
