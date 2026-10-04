"""Display-only inspection of the selection used by Auto color protection."""

import numpy as np
from PIL import Image


def person_overlay(image, analysis, *, box=(0, 0, 1, 1)):
    if analysis is None:
        return image
    core = analysis.core_mask((image.height, image.width), box=box)
    if core is None or not core.any():
        return image
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    tint = np.array([230, 65, 155], dtype=np.float32)
    pixels[core] = pixels[core] * .60 + tint * .40
    return Image.fromarray(np.rint(pixels).astype(np.uint8))
