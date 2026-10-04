"""Optional offline face locations for exposure metering, never identification."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import threading

import numpy as np
from PIL import Image


MODEL_ID = "yunet-2023mar"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
INPUT_SIZE = 640


def model_directory():
    override = os.environ.get("OPENRAW_FACE_MODEL")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "OpenRAW Studio" / "models" / MODEL_ID


@dataclass(frozen=True)
class Face:
    box: tuple[float, float, float, float]
    score: float


@dataclass(frozen=True)
class FaceAnalysis:
    status: str
    faces: tuple[Face, ...] = ()
    model: str = MODEL_ID


def prepare_face_input(image):
    image = image.convert("RGB")
    scale = min(INPUT_SIZE / image.width, INPUT_SIZE / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    pixels = np.asarray(image.resize(size, Image.Resampling.BILINEAR), dtype=np.float32)
    padded = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), np.float32)
    padded[:size[1], :size[0]] = pixels[..., ::-1]
    return np.ascontiguousarray(padded.transpose(2, 0, 1)[None]), size


def decode_faces(outputs, size):
    """YuNet's documented stride-center boxes; floating-point IoU suppression."""
    if len(size) != 2 or any(type(value) is not int or not 1 <= value <= INPUT_SIZE for value in size):
        raise ValueError("Invalid face model image dimensions")
    candidates = []
    for stride in (8, 16, 32):
        side = INPUT_SIZE // stride
        count = side * side
        arrays = [np.asarray(outputs[f"{name}_{stride}"], np.float32) for name in ("cls", "obj", "bbox")]
        if any(not np.isfinite(array).all() for array in arrays):
            raise ValueError("Non-finite face model output")
        if [array.shape for array in arrays] != [(1, count, 1), (1, count, 1), (1, count, 4)]:
            raise ValueError("Invalid face model output shape")
        cls, obj, bounds = arrays
        scores = np.sqrt(np.clip(cls.reshape(-1), 0, 1) * np.clip(obj.reshape(-1), 0, 1))
        for index in np.flatnonzero(scores >= .9):
            dx, dy, lw, lh = map(float, bounds[0, index])
            if abs(lw) > 8 or abs(lh) > 8:
                continue
            width, height = np.exp([lw, lh]) * stride
            cx, cy = ((index % side + dx) * stride, (index // side + dy) * stride)
            if min(width, height) < 10 or not (0 <= cx < size[0] and 0 <= cy < size[1]):
                continue
            x0, y0 = max(0, cx - width / 2), max(0, cy - height / 2)
            x1, y1 = min(size[0], cx + width / 2), min(size[1], cy + height / 2)
            if (x1 - x0) * (y1 - y0) < width * height * .8:
                continue
            candidates.append((float(scores[index]), (x0, y0, x1, y1)))
    accepted = []
    for score, box in sorted(candidates, reverse=True)[:100]:
        x0, y0, x1, y1 = box
        area = (x1 - x0) * (y1 - y0)
        duplicate = False
        for _, (a, b, c, d) in accepted:
            overlap = max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
            if overlap / (area + (c - a) * (d - b) - overlap) > .3:
                duplicate = True
                break
        if not duplicate:
            accepted.append((score, box))
        if len(accepted) == 16:
            break
    return tuple(Face(tuple(float(v / size[i % 2]) for i, v in enumerate(box)), score)
                 for score, box in accepted)


class LocalFaceDetector:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.session = None
        self.cache = {}

    def detect(self, image):
        pixels, size = prepare_face_input(image)
        key = (hashlib.sha256(pixels.tobytes()).digest(), size)
        if key not in self.cache:
            if self.session is None:
                import onnxruntime as ort

                path = self.folder / "face.onnx"
                with path.open("rb") as handle:
                    checksum = hashlib.file_digest(handle, "sha256").hexdigest()
                if checksum != MODEL_SHA256:
                    raise ValueError("Face model checksum mismatch")
                ort.disable_telemetry_events()
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
            names = [f"{name}_{stride}" for name in ("cls", "obj", "bbox") for stride in (8, 16, 32)]
            faces = decode_faces(dict(zip(names, self.session.run(names, {"input": pixels}))), size)
            if len(self.cache) >= 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = FaceAnalysis("ready" if faces else "no-face", faces)
        return self.cache[key]


_lock = threading.Lock()
_detector = None


def analyze_faces(image):
    global _detector
    if any(os.environ.get(key, "auto").lower() == "off" for key in ("OPENRAW_SCENE", "OPENRAW_PERSON", "OPENRAW_FACE")):
        return FaceAnalysis("disabled")
    if not isinstance(image, Image.Image) or min(image.size) < 96:
        return FaceAnalysis("insufficient-resolution")
    folder = model_directory()
    if not (folder / "face.onnx").is_file():
        return FaceAnalysis("not-installed")
    try:
        with _lock:
            if _detector is None or _detector.folder != folder:
                _detector = LocalFaceDetector(folder)
            return _detector.detect(image)
    except Exception:  # Optional analysis must never disable photo editing.
        return FaceAnalysis("unavailable")
