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
from openraw_studio.raw.native.regions import oriented_size, sensor_region


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
        x, y, width, height = sensor_region(region, size, self.photo.orientation)
        tile = replace(self.photo, pixels=self.photo.pixels[y:y + height, x:x + width].copy())
        return tile.render(adjustments)[0]


def prepare_detail_photo(processor, source):
    metadata = processor._read_supported_nikon_34713(source)
    if metadata is not None:
        return NikonDetailPhoto(processor._decode_supported_nikon_34713(source, metadata))
    return LinearDetailPhoto(prepare_interactive_photo(processor, source, max_dimension=None))
