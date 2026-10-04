"""Optional local person masks, corroborated by independent scene evidence."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from collections import deque
import hashlib
import os
from pathlib import Path
import threading

import numpy as np
from PIL import Image

from openraw_studio.vision.scene import analyze_scene
from openraw_studio.vision.mask import guided_selection, project_selection


MODEL_ID = "pphumanseg-2023mar"
MODEL_SHA256 = "552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24"


def model_directory():
    override = os.environ.get("OPENRAW_PERSON_MODEL")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "OpenRAW Studio" / "models" / MODEL_ID


@dataclass(frozen=True)
class PersonEvidence:
    status: str
    coverage: float = 0.0
    mean_score: float = 0.0
    scene_agreement: float = 0.0
    regions: int = 0
    model: str = MODEL_ID


@dataclass(frozen=True)
class PersonAnalysis:
    evidence: PersonEvidence
    probabilities: np.ndarray | None = None
    selection: np.ndarray | None = field(default=None, repr=False, compare=False)

    def core_mask(self, shape, *, box=(0, 0, 1, 1)):
        if self.evidence.status != "ready" or self.probabilities is None:
            return None
        weights = self.selection if self.selection is not None else self.probabilities
        return project_selection(weights, shape, box=box) >= .9


def prepare_person_input(image):
    # The official model consumes full-frame RGB, stretched to 192 square.
    pixels = np.asarray(image.convert("RGB").resize((192, 192), Image.Resampling.BILINEAR), dtype=np.float32)
    return np.ascontiguousarray((pixels / 127.5 - 1).transpose(2, 0, 1)[None])


def person_probabilities(scores):
    scores = np.asarray(scores, dtype=np.float32)
    if (scores.shape != (1, 2, 192, 192) or not np.isfinite(scores).all()
            or scores.min() < 0 or scores.max() > 1
            or not np.allclose(scores.sum(axis=1), 1, atol=1e-4)):
        raise ValueError("Invalid person model probabilities")
    probabilities = scores[0, 1].copy()
    probabilities.setflags(write=False)
    return probabilities


class LocalPersonSegmenter:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.session = None
        self.cache = {}

    def _load(self):
        import onnxruntime as ort

        path = self.folder / "person.onnx"
        with path.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        if checksum != MODEL_SHA256:
            raise ValueError("Person model checksum mismatch")
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])

    def segment(self, image):
        pixels = prepare_person_input(image)
        key = hashlib.sha256(pixels.tobytes()).digest()
        if key not in self.cache:
            if self.session is None:
                self._load()
            probabilities = person_probabilities(self.session.run(None, {"x": pixels})[0])
            if len(self.cache) >= 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = probabilities
        return self.cache[key]


def _region_crop(image, core):
    ys, xs = np.nonzero(core)
    height, width = core.shape
    x0, y0, x1, y1 = xs.min() / width, ys.min() / height, (xs.max() + 1) / width, (ys.max() + 1) / height
    pad = .1 * max(x1 - x0, y1 - y0)
    box = tuple(round(value * size) for value, size in zip(
        (max(0, x0 - pad), max(0, y0 - pad), min(1, x1 + pad), min(1, y1 + pad)),
        (image.width, image.height, image.width, image.height),
    ))
    return image.crop(box)


def _components(core):
    """Find bounded-grid 4-connected candidates; discard tiny fragments."""
    remaining = core.copy()
    height, width = remaining.shape
    components = []
    for y, x in np.argwhere(core):
        if not remaining[y, x]:
            continue
        queue = deque([(int(y), int(x))])
        remaining[y, x] = False
        indices = []
        while queue:
            row, column = queue.popleft()
            indices.append(row * width + column)
            for a, b in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
                if 0 <= a < height and 0 <= b < width and remaining[a, b]:
                    remaining[a, b] = False
                    queue.append((a, b))
        if len(indices) >= core.size * .01:
            components.append(indices)
    for indices in sorted(components, key=len, reverse=True)[:3]:
        mask = np.zeros_like(core)
        mask.flat[indices] = True
        yield mask


def confirm_person(image, probabilities, *, classify=analyze_scene):
    core = probabilities >= .9
    coverage = float(core.mean())
    score = float(probabilities[core].mean()) if core.any() else 0.0
    if coverage < .01:
        return PersonAnalysis(PersonEvidence("insufficient-area", coverage, score))
    if coverage > .85 or score < .95:
        return PersonAnalysis(PersonEvidence("uncertain", coverage, score))

    def corroborate(mask):
        scene = classify(_region_crop(image, mask))
        return scene.reliability * scene.scenes.get("Portrait", 0) if scene.status == "ready" else 0.0

    agreement = corroborate(core)
    if agreement >= .15:
        return PersonAnalysis(PersonEvidence("ready", coverage, score, agreement, 1), probabilities)
    components = list(_components(core))
    accepted = np.zeros_like(core)
    regions = 0
    # Only retry distinct substantial candidates, not repeated crops of one blob.
    if len(components) > 1:
        for mask in components:
            if probabilities[mask].mean() < .95:
                continue
            vote = corroborate(mask)
            if vote >= .15:
                accepted |= mask
                agreement = max(agreement, vote)
                regions += 1
    if not regions:
        return PersonAnalysis(PersonEvidence("unconfirmed", coverage, score, agreement))
    selected = np.where(accepted, probabilities, 0)
    selected.setflags(write=False)
    return PersonAnalysis(PersonEvidence("ready", float(accepted.mean()), float(probabilities[accepted].mean()),
                                         agreement, regions), selected)


_lock = threading.Lock()
_segmenter = None


def analyze_person(image):
    global _segmenter
    if any(os.environ.get(key, "auto").lower() == "off" for key in ("OPENRAW_SCENE", "OPENRAW_PERSON")):
        return PersonAnalysis(PersonEvidence("disabled"))
    if not isinstance(image, Image.Image):
        pixels = np.asarray(image)
        if pixels.ndim != 3 or pixels.shape[-1] != 3 or not np.isfinite(pixels).all():
            return PersonAnalysis(PersonEvidence("insufficient-resolution"))
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    if min(image.size) < 96:
        return PersonAnalysis(PersonEvidence("insufficient-resolution"))
    folder = model_directory()
    if not (folder / "person.onnx").is_file():
        return PersonAnalysis(PersonEvidence("not-installed"))
    try:
        with _lock:
            if _segmenter is None or _segmenter.folder != folder:
                _segmenter = LocalPersonSegmenter(folder)
            probabilities = _segmenter.segment(image)
        # Do not hold two model locks while corroborating a candidate region.
        analysis = confirm_person(image, probabilities)
        if analysis.evidence.status == "ready":
            analysis = replace(analysis, selection=guided_selection(image, analysis.probabilities))
        return analysis
    except Exception:  # An optional model must never disable ordinary Auto.
        return PersonAnalysis(PersonEvidence("unavailable"))
