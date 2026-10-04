"""Explicit developer setup for the pinned, optional offline person model."""

import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openraw_studio.core.files import sha256_file
from openraw_studio.models.artifacts import publish_model_artifact
from openraw_studio.vision.person import MODEL_ID, MODEL_SHA256, model_directory


REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
DIRECTORY = "models/human_segmentation_pphumanseg"
MODEL_URL = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/{DIRECTORY}/human_segmentation_pphumanseg_2023mar.onnx"
LICENSE_URL = f"https://raw.githubusercontent.com/opencv/opencv_zoo/{REVISION}/{DIRECTORY}/LICENSE"


def download(url, destination):
    with urlopen(url, timeout=60) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)


def install(output, source=None):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="person-setup-", dir=output) as staging:
        folder = Path(staging)
        model = folder / "person.onnx"
        if source is None:
            download(MODEL_URL, model)
        else:
            shutil.copyfile(source, model)
        if sha256_file(model) != MODEL_SHA256:
            raise ValueError("Person model checksum mismatch; no installed model was replaced")
        license_path = folder / "LICENSE"
        download(LICENSE_URL, license_path)
        manifest = {
            "model_id": MODEL_ID, "onnx_sha256": MODEL_SHA256, "bytes": model.stat().st_size,
            "source": MODEL_URL, "revision": REVISION, "license": "Apache-2.0",
            "scope": "Local photo person-mask experiment; no identity or demographics",
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for name in ("LICENSE", "person.onnx", "manifest.json"):
            publish_model_artifact(folder / name, output / name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=model_directory())
    parser.add_argument("--source", type=Path, help="Use an already downloaded, checksum-verified model")
    args = parser.parse_args()
    print(json.dumps(install(args.output, args.source), indent=2))


if __name__ == "__main__":
    main()
