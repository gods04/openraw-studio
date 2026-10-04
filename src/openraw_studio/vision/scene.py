"""Local, replaceable scene evidence; scores are not calibrated probabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import threading

import numpy as np
from PIL import Image


MODEL_ID = "clip-vit-b32-scene-v1"
# Text describes content, not adjustment presets or desired finished colors.
SCENES = {
    "Coast": ("a photograph of a beach and the ocean", "a seascape with waves and a rocky coast"),
    "Grassland": ("a landscape photograph of a grassy meadow", "a photograph of grassland and rolling green hills"),
    "Forest": ("a photograph of a forest with trees and foliage", "a landscape photograph of woodland"),
    "Sky": ("a photograph of the sky and clouds", "a photograph of a rainbow in the sky",
            "a photograph of the moon in the sky", "a photograph of the sunset sky"),
    "Mountains": ("a landscape photograph of mountains", "a photograph of a rocky mountain valley"),
    "Snow": ("a photograph of a snowy landscape", "a photograph of snow and ice"),
    "Portrait": ("a portrait photograph of a person", "a photograph of people posing together",
                 "a photograph of a person sitting at a table", "a photograph of a person standing outdoors"),
    "Street": ("a photograph of a city street and buildings", "a photograph of urban architecture",
               "a photograph of a temple or historic building", "a photograph of a famous architectural landmark"),
    "Interior": ("a photograph of an indoor room", "a photograph inside a restaurant"),
    "Aquarium": ("a photograph of fish in an aquarium", "an underwater photograph of marine life"),
    "Flowers": ("a photograph of colorful flowers", "a close-up photograph of flowers in a garden"),
    "Food": ("a photograph of food on a table", "a photograph of a prepared meal"),
    "Animals": ("a photograph of an animal", "a wildlife photograph"),
    "Objects": ("a photograph of an everyday object", "a close-up photograph of a product",
                "a photograph of a vehicle", "a photograph of an object on display in a museum"),
    "Document": ("a photograph of a printed document", "a photograph of a drawing or screen"),
}
LIGHTING = {
    "Daylight": ("a photograph taken in natural daylight", "a photograph taken on a sunny day"),
    "Overcast": ("a photograph taken on an overcast day", "a photograph in soft cloudy daylight"),
    "Sunset": ("a photograph taken in warm sunset light", "a photograph at sunrise or golden hour"),
    "Night": ("a photograph taken at night", "a dark nighttime photograph"),
    "Indoor light": ("a photograph taken in indoor lighting", "a photograph illuminated by indoor lamps"),
    "Colored light": ("a photograph illuminated by colored neon lights", "a photograph in colorful artificial lighting"),
}


def prompt_digest():
    return hashlib.sha256(json.dumps([SCENES, LIGHTING], sort_keys=True).encode()).hexdigest()


def model_directory():
    override = os.environ.get("OPENRAW_SCENE_MODEL")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "OpenRAW Studio" / "models" / MODEL_ID


@dataclass(frozen=True)
class SceneEvidence:
    status: str
    scene: str = "Unknown"
    lighting: str = "Unknown"
    reliability: float = 0.0
    scenes: dict[str, float] = field(default_factory=dict)
    lights: dict[str, float] = field(default_factory=dict)
    model: str = MODEL_ID


def _distribution(scores):
    scaled = 100 * (scores - scores.max())
    weights = np.exp(scaled)
    return weights / weights.sum()


def evidence_from_scores(scores):
    scores = np.asarray(scores, dtype=np.float32).reshape(-1)
    if scores.shape != (len(SCENES) + len(LIGHTING),) or not np.isfinite(scores).all():
        raise ValueError("Invalid scene model output")
    if np.max(np.abs(scores)) > 1.001:
        raise ValueError("Scene model must return cosine similarities")
    scene_scores, light_scores = scores[:len(SCENES)], scores[len(SCENES):]
    weights = _distribution(scene_scores)
    lights = _distribution(light_scores)
    # Absolute similarity and ambiguity both limit downstream influence.
    reliability = float(np.clip((scene_scores.max() - .20) / .10, 0, 1)
                        * np.clip((weights.max() - .12) / .35, 0, 1))
    return SceneEvidence(
        "ready" if reliability >= .15 else "uncertain",
        tuple(SCENES)[int(np.argmax(weights))],
        tuple(LIGHTING)[int(np.argmax(lights))] if lights.max() >= .45 else "Mixed light",
        reliability, dict(zip(SCENES, map(float, weights))), dict(zip(LIGHTING, map(float, lights))),
    )


def prepare_scene_input(image):
    image = image.convert("RGB")
    width, height = image.size
    size = (224, int(224 * height / width)) if width <= height else (int(224 * width / height), 224)
    image = image.resize(size, Image.Resampling.BICUBIC)
    left, top = round((image.width - 224) / 2), round((image.height - 224) / 2)
    pixels = np.asarray(image.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255
    pixels = (pixels - np.array([.48145466, .4578275, .40821073], dtype=np.float32)) / np.array(
        [.26862954, .26130258, .27577711], dtype=np.float32,
    )
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])


class LocalSceneClassifier:
    """No network, image logging, or model downloads in the app runtime."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.session = None
        self.cache = {}

    def _load(self):
        import onnxruntime as ort

        manifest = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("model_id") != MODEL_ID or manifest.get("prompt_sha256") != prompt_digest():
            raise ValueError("Incompatible scene model manifest")
        path = self.folder / "scene.onnx"
        with path.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        if checksum != manifest.get("onnx_sha256"):
            raise ValueError("Scene model checksum mismatch")
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])

    def classify(self, image):
        pixels = prepare_scene_input(image)
        key = hashlib.sha256(pixels.tobytes()).digest()
        if key in self.cache:
            return self.cache[key]
        if self.session is None:
            self._load()
        scores = self.session.run(["scores"], {"image": pixels})[0]
        evidence = evidence_from_scores(scores)
        if len(self.cache) >= 16:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = evidence
        return evidence


_lock = threading.Lock()
_classifier = None


def analyze_scene(image):
    global _classifier
    if os.environ.get("OPENRAW_SCENE", "auto").lower() == "off":
        return SceneEvidence("disabled")
    if not isinstance(image, Image.Image):
        pixels = np.asarray(image)
        if pixels.ndim != 3 or pixels.shape[-1] != 3 or not np.isfinite(pixels).all():
            return SceneEvidence("insufficient-resolution")
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    if min(image.size) < 96:
        return SceneEvidence("insufficient-resolution")
    folder = model_directory()
    if not (folder / "manifest.json").is_file():
        return SceneEvidence("not-installed")
    with _lock:
        try:
            if _classifier is None or _classifier.folder != folder:
                _classifier = LocalSceneClassifier(folder)
            return _classifier.classify(image)
        except Exception:  # Optional model failures must not disable tonal Auto.
            return SceneEvidence("unavailable")
