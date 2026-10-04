"""Prepared native detail renderers; no export files are needed for inspection."""

from dataclasses import dataclass, replace

from PIL import Image

from openraw_studio.raw.native.interactive import (
    InteractivePhoto,
    prepare_interactive_photo,
)
from openraw_studio.raw.native.nikon import (
    NikonDecodedPixelData,
    _render_crop,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.regions import oriented_size, region_with_halo, sensor_region
from openraw_studio.raw.native.noise import noise_radius
from openraw_studio.core.subject import global_adjustments


@dataclass
class NikonDetailPhoto:
    decoded: NikonDecodedPixelData

    @property
    def size(self):
        return oriented_size(_render_crop(self.decoded)[2:], self.decoded.orientation)

    def render_region(self, adjustments, region):
        rendered = render_decoded_nikon_34713_image(
            self.decoded, quality="full", region=region, **adjustments
        )
        return Image.frombytes("RGB", (rendered.width, rendered.height), rendered.rgb_bytes)


@dataclass
class LinearDetailPhoto:
    photo: InteractivePhoto

    @property
    def size(self):
        return oriented_size(self.photo.pixels.shape[1::-1], self.photo.orientation)

    def render_region(self, adjustments, region):
        size = self.photo.pixels.shape[1::-1]
        source_region = sensor_region(region, size, self.photo.orientation)
        radius = noise_radius(color_noise=adjustments.get("color_noise", 0), luminance_noise=adjustments.get("luminance_noise", 0))
        crop, core = region_with_halo((0, 0, *size), source_region, radius=radius)
        x, y, width, height = crop
        tile = replace(self.photo, pixels=self.photo.pixels[y:y + height, x:x + width].copy(), orientation=1, native_size=(width, height))
        from openraw_studio.raw.native.nikon import _apply_exif_orientation

        image = _apply_exif_orientation(tile.render(global_adjustments(adjustments))[0].crop(core), self.photo.orientation)
        if adjustments.get("subject") is not None:
            import numpy as np
            from openraw_studio.raw.native.subject import apply_subject
            image = Image.fromarray(apply_subject(np.asarray(image), adjustments["subject"], full_size=self.size, region=region))
        return image


def prepare_detail_photo(processor, source):
    metadata = processor._read_supported_nikon_34713(source)
    if metadata is not None:
        return NikonDetailPhoto(processor._decode_supported_nikon_34713(source, metadata))
    return LinearDetailPhoto(prepare_interactive_photo(processor, source, max_dimension=None))
