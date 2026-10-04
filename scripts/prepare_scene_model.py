"""Explicit developer setup: export a verified CLIP checkpoint for offline Auto."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openraw_studio.vision.scene import LIGHTING, MODEL_ID, SCENES, model_directory, prompt_digest
from openraw_studio.vision.material import MATERIALS, prompt_digest as material_digest
from openraw_studio.models.artifacts import publish_model_artifact as _publish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=model_directory())
    parser.add_argument("--checkpoint-cache", type=Path, required=True)
    parser.add_argument("--with-materials", action="store_true", help="Add optional garment-color corroboration without replacing scene scores")
    args = parser.parse_args()
    import clip
    import numpy as np
    import onnxruntime as ort
    import torch

    torch.set_num_threads(2)
    torch.backends.mha.set_fastpath_enabled(False)
    source_root = Path(clip.__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True).strip()
    if revision != "d05afc436d78f1c48dc0dbf8e5980a9d471f35f6":
        raise RuntimeError("Use the reviewed CLIP source revision documented in models/README.md")
    model, _preprocess = clip.load("ViT-B/32", device="cpu", download_root=str(args.checkpoint_cache))
    model = model.float().eval()
    vectors = []
    ranges = []
    with torch.no_grad():
        for prompts in (*SCENES.values(), *LIGHTING.values()):
            vector = model.encode_text(clip.tokenize(list(prompts)))
            vector = vector / vector.norm(dim=-1, keepdim=True)
            start = len(vectors)
            vectors.extend(vector.unbind())
            ranges.append((start, len(vectors)))
        material_vectors, material_ranges = [], []
        if args.with_materials:
            for prompts in MATERIALS.values():
                vector = model.encode_text(clip.tokenize(list(prompts)))
                vector = vector / vector.norm(dim=-1, keepdim=True)
                start = len(material_vectors)
                material_vectors.extend(vector.unbind())
                material_ranges.append((start, len(material_vectors)))

    class SceneModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.visual = model.visual
            self.register_buffer("texts", torch.stack(vectors).T)
            if args.with_materials:
                self.register_buffer("material_texts", torch.stack(material_vectors).T)

        def forward(self, image):
            features = self.visual(image)
            normalized = features / features.norm(dim=-1, keepdim=True)
            scores = normalized @ self.texts
            scenes = torch.stack([scores[:, start:end].max(dim=1).values for start, end in ranges], dim=1)
            if not args.with_materials:
                return scenes
            material = normalized @ self.material_texts
            return scenes, torch.stack([material[:, start:end].max(dim=1).values for start, end in material_ranges], dim=1)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    scene = SceneModel().eval()
    torch.manual_seed(21)
    sample = torch.randn(1, 3, 224, 224)
    path = output / "scene.onnx.pending"
    with torch.no_grad():
        expected = scene(sample)
        expected = [value.numpy() for value in expected] if args.with_materials else [expected.numpy()]
        output_names = ["scores", "material_scores"] if args.with_materials else ["scores"]
        torch.onnx.export(scene, sample, str(path), input_names=["image"], output_names=output_names,
                          opset_version=17, dynamo=False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    runtime = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    actual = runtime.run(output_names, {"image": sample.numpy()})
    for actual_output, reference in zip(actual, expected):
        np.testing.assert_allclose(actual_output, reference, atol=2e-5, rtol=2e-5)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    license_path = Path(clip.__file__).resolve().parents[1] / "LICENSE"
    if not license_path.is_file():
        raise RuntimeError("Install CLIP from its official source checkout to retain its license")
    shutil.copyfile(license_path, output / "LICENSE-CLIP.txt")
    manifest = {
        "model_id": MODEL_ID, "prompt_sha256": prompt_digest(), "onnx_sha256": digest,
        "source": "https://github.com/openai/CLIP", "checkpoint": "ViT-B/32",
        "source_revision": revision, "torch_version": torch.__version__, "onnxruntime_version": ort.__version__,
        "checkpoint_sha256": "40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af",
        "scene_labels": list(SCENES), "lighting_labels": list(LIGHTING),
        "license": "MIT", "scope": "Local evaluated photography beta; no identity or demographic inference",
        "bytes": path.stat().st_size, "export_max_error": max(float(np.abs(a - b).max()) for a, b in zip(actual, expected)),
    }
    if args.with_materials:
        manifest.update(material_prompt_sha256=material_digest(), material_labels=list(MATERIALS))
    pending = output / "manifest.json.pending"
    pending.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _publish(path, output / "scene.onnx")
    _publish(pending, output / "manifest.json")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
