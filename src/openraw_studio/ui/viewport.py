"""Native-pixel viewport geometry independent of Tk and RAW decoding."""

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class DetailView:
    viewport: tuple[int, int]
    scale: int = 1
    offset: tuple[float, float] = (0.0, 0.0)
    anchor: tuple[float, float] = (0.5, 0.5)

    def region(self, size):
        if self.scale not in (1, 2) or min(self.viewport) < 1 or min(size) < 1:
            raise ValueError("Invalid native-pixel viewport")
        width = min(size[0], ceil(self.viewport[0] / self.scale))
        height = min(size[1], ceil(self.viewport[1] / self.scale))
        x = max(0, min(size[0] - width, round(size[0] * self.anchor[0] - width / 2 - self.offset[0] / self.scale)))
        y = max(0, min(size[1] - height, round(size[1] * self.anchor[1] - height / 2 - self.offset[1] / self.scale)))
        return x, y, width, height

    def display_size(self, region):
        return tuple(min(limit, length * self.scale) for limit, length in zip(self.viewport, region[2:]))
