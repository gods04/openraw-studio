"""Explicit setup for the pinned optional offline YuNet face-metering model."""

import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openraw_studio.core.files import sha256_file
from openraw_studio.models.artifacts import publish_model_artifact
from openraw_studio.vision.face import MODEL_ID, MODEL_SHA256, model_directory
from prepare_person_model import download


REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
DIRECTORY = "models/face_detection_yunet"
MODEL_URL = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/{DIRECTORY}/face_detection_yunet_2023mar.onnx"
LICENSE_URL = f"https://raw.githubusercontent.com/opencv/opencv_zoo/{REVISION}/{DIRECTORY}/LICENSE"


def install(output, source=None):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="face-setup-", dir=output) as staging:
        folder = Path(staging)
        model = folder / "face.onnx"
        if source is None:
            download(MODEL_URL, model)
        else:
            shutil.copyfile(source, model)
        if sha256_file(model) != MODEL_SHA256:
            raise ValueError("Face model checksum mismatch; no installed model was replaced")
        download(LICENSE_URL, folder / "LICENSE")
        manifest = {
            "model_id": MODEL_ID, "onnx_sha256": MODEL_SHA256, "bytes": model.stat().st_size,
            "source": MODEL_URL, "revision": REVISION, "license": "MIT",
            "scope": "Offline face locations for photography metering; no recognition or demographics",
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for name in ("LICENSE", "face.onnx", "manifest.json"):
            publish_model_artifact(folder / name, output / name)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=model_directory())
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.output, args.source), indent=2))
