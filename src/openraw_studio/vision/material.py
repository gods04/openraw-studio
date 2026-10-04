"""Optional garment-color corroboration; never a skin or identity classifier."""

from dataclasses import dataclass
import hashlib
import json
import os

import numpy as np
from PIL import Image


MATERIALS = {
    'White': ('a person wearing white clothing', 'a white shirt or white trousers'),
    'Gray': ('a person wearing light gray clothing', 'a light gray shirt or gray trousers'),
    'Black': ('a person wearing black clothing', 'a black shirt or dark trousers'),
    'Pink': ('a person wearing pink clothing', 'a pink shirt or pink trousers'),
    'Red': ('a person wearing red clothing', 'a red shirt or red trousers'),
    'Orange': ('a person wearing orange clothing', 'an orange shirt or orange trousers'),
    'Yellow': ('a person wearing yellow clothing', 'a yellow shirt or yellow trousers'),
    'Green': ('a person wearing green clothing', 'a green shirt or green trousers'),
    'Blue': ('a person wearing blue clothing', 'a blue shirt or blue trousers'),
    'Purple': ('a person wearing purple clothing', 'a purple shirt or purple trousers'),
    'Brown': ('a person wearing brown clothing', 'a brown shirt or brown trousers'),
    'Beige': ('a person wearing beige clothing', 'a beige shirt or cream trousers'),
    'Patterned': ('a person wearing colorful patterned clothing', 'a multicolored shirt'),
    'Bare': ("a person's bare arm or bare torso", 'a close-up of a face'),
}


def prompt_digest():
    return hashlib.sha256(json.dumps(MATERIALS, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class MaterialEvidence:
    status: str
    label: str = 'Unknown'
    score: float = 0
    margin: float = 0


def evidence_from_scores(scores):
    scores = np.asarray(scores, np.float32).reshape(-1)
    if scores.shape != (len(MATERIALS),) or not np.isfinite(scores).all() or np.max(np.abs(scores)) > 1.001:
        raise ValueError('Invalid material cosine scores')
    best = int(np.argmax(scores))
    neutral = float(max(scores[0], scores[1]))
    margin = neutral - float(max(scores[2:]))
    ready = best < 2 and neutral >= .24 and margin >= .02
    return MaterialEvidence('ready' if ready else 'uncertain', tuple(MATERIALS)[best], float(scores[best]), margin)


def subject_crop(image, weights):
    if not isinstance(image, Image.Image) or weights.shape != (image.height, image.width):
        raise ValueError('Subject color crop must match its oriented image')
    ys, xs = np.nonzero(weights >= .9)
    if not len(xs):
        return None
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return image.crop(box) if min(box[2] - box[0], box[3] - box[1]) >= 64 else None


def analyze_material(image, weights):
    from openraw_studio.vision import scene
    if any(os.environ.get(key, 'auto').lower() == 'off' for key in ('OPENRAW_SCENE', 'OPENRAW_PERSON', 'OPENRAW_MATERIAL')):
        return MaterialEvidence('disabled')
    crop = subject_crop(image, weights)
    if crop is None:
        return MaterialEvidence('insufficient-resolution')
    folder = scene.model_directory()
    if not (folder / 'manifest.json').is_file():
        return MaterialEvidence('not-installed')
    with scene._lock:
        try:
            if scene._classifier is None or scene._classifier.folder != folder:
                scene._classifier = scene.LocalSceneClassifier(folder)
            return scene._classifier.classify_material(crop)
        except Exception:  # Optional corroboration must never block editing.
            return MaterialEvidence('unavailable')
