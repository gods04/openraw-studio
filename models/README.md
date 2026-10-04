# Models

Do not commit model weights to this repository until licensing and distribution
are reviewed.

This folder is reserved for lightweight model documentation, download manifests,
or checksums. Actual weights should be fetched or configured separately.

Before adding any model, update `docs/MODEL_LICENSES.md` with:

- model code license
- model weights license
- dataset license where known
- commercial-use permission
- redistribution permission
- attribution requirements
- file size and checksum

## Experimental Local Scene Auto

The optional CLIP ViT-B/32 encoder recognizes coarse scene/lighting descriptions.
OpenRAW's own constrained solver uses that evidence and measured pixel colors;
CLIP does not generate edits or decode RAW. Scores are uncalibrated similarities.
This is a local photography beta, not a generally validated aesthetic model.
Read the upstream use limitations in `docs/MODEL_LICENSES.md` before deployment.

The Windows build contains the CPU ONNX runtime, not the model or PyTorch.
The app looks in `%LOCALAPPDATA%\OpenRAW Studio\models\clip-vit-b32-scene-v1`
(on non-Windows systems, `~/.local/share/OpenRAW Studio/models/...`).
`OPENRAW_SCENE_MODEL` can name a different model folder. No network calls or
model downloads occur while using Auto. Missing or invalid models leave tonal
Auto working; `OPENRAW_SCENE=off` explicitly disables scene analysis.

Developer setup from the repository root, with network access only for the
dependencies, source checkout, and checksum-verified upstream checkpoint:

```powershell
python -m venv output\scene-tools
.\output\scene-tools\Scripts\python.exe -m pip install --upgrade pip
.\output\scene-tools\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
git clone https://github.com/openai/CLIP.git output\clip-source
git -C output\clip-source checkout d05afc436d78f1c48dc0dbf8e5980a9d471f35f6
.\output\scene-tools\Scripts\python.exe -m pip install -e output\clip-source onnx onnxruntime==1.30.0
.\output\scene-tools\Scripts\python.exe scripts\prepare_scene_model.py --checkpoint-cache output\scene-checkpoints
.\.venv\Scripts\python.exe -m pip install -e ".[scene]"
```

Restart the app after installing or replacing the model. Setup exports the
image encoder with the scene text embeddings, checks ONNX/PyTorch agreement,
retains the upstream license, and writes a checksum/prompt manifest last.
The runtime rejects mismatched model or prompt hashes. Weights, text embeddings,
private validation photos, and reports remain outside Git. Export currently
uses about 352 MB on disk; source export tools need additional temporary space.

This setup was exercised with CPU PyTorch 2.14.1, torchvision 0.29.1,
ONNX 1.23.1, and ONNX Runtime 1.30.0. The model is pretrained by OpenAI, not
trained on the user's photographs. Future model/prompt revisions need new
photography validation and a new exported manifest.

## Experimental Person-Aware Color

The optional person model supplies a coarse mask for Auto's color objectives
and protection checks. It does not identify people, infer demographics, detect
faces, smooth skin, or create separate local edits. Its mask must have enough
high-scoring area and be corroborated by the scene model on the candidate crop.
Distinct substantial regions can be checked separately when a group crop is
ambiguous. Small, uncertain, and unconfirmed subjects retain scene-only Auto.
No model score is a calibrated guarantee of correctness.

After the scene model/runtime setup above, explicitly run:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_person_model.py
```

Setup downloads the pinned 6.16 MB OpenCV Zoo PPHumanSeg ONNX artifact and its
license, verifies SHA-256 before replacing an installed model, and retains
provenance in a manifest. Both the download and license retrieval need network
access during setup only. Runtime analysis stays offline and CPU-only.

The default location is `%LOCALAPPDATA%\OpenRAW Studio\models\pphumanseg-2023mar`
(under the same local share directory on non-Windows). `OPENRAW_PERSON_MODEL`
overrides the folder. `OPENRAW_PERSON=off` keeps scene Auto but disables person
analysis; `OPENRAW_SCENE=off` disables both. Missing/broken person models retain
scene-only Auto. Missing scene evidence prevents a person mask from influencing
edits. Restart after installing or replacing a model. Neither model is bundled
in the app or downloaded automatically. See `docs/MODEL_LICENSES.md` for the
source, hash, license, and remaining deployment review.
