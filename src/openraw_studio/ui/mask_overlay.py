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


def subject_overlay(image, subject, *, box=(0, 0, 1, 1), full_size=None, region=None):
    from openraw_studio.core.subject import decode_mask
    from openraw_studio.raw.native.subject import subject_weights
    from openraw_studio.vision.mask import project_selection

    if full_size is not None:
        weights = subject_weights(subject, full_size, region=region)
    else:
        mask = decode_mask(subject["mask_zlib"], subject["width"], subject["height"])
        weights = project_selection(mask.astype(np.float32) / 255,
                                    (image.height, image.width), box=box)
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    alpha = weights[..., None] * .4
    tint = np.array([230, 65, 155], dtype=np.float32)
    return Image.fromarray(np.rint(pixels * (1 - alpha) + tint * alpha).astype(np.uint8))
