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

### Optional Local Color Corroboration

To enable experimental subject-color Auto, re-export the same checkpoint with
the optional garment-color head, after the scene setup above:

```powershell
.\output\scene-tools\Scripts\python.exe scripts\prepare_scene_model.py --checkpoint-cache output\scene-checkpoints --with-materials
```

Restart the app afterward. This adds 14 coarse garment-description comparisons
to the same encoder, not another model or a new training run. Scene scores use
the original separate projection; all 33 local regression inputs retain exact
scene scores and evidence. `OPENRAW_MATERIAL=off` disables only this head.
`OPENRAW_SCENE=off` or `OPENRAW_PERSON=off` also disables its use. The original
scene-only export remains supported; its absence affects only local color Auto.

White/gray similarity and margin must corroborate spatial neutral pixels before
OpenRAW tries local color correction. These are not calibrated probabilities,
skin labels, or ground-truth material colors. Colored clothes, surrounding objects,
mixed wardrobes, and lighting can confuse this coarse crop-level comparison.
Uncertain evidence leaves edits unchanged. The measured solver additionally
requires visible face overlap, background reference, non-ambient light, and
per-region improvement. Manual color sliders and saved recipes need no head.

The checked optional export is 351,820,318 bytes, SHA-256
`3813e10c680732d1b709736bdf3886113e24373d883a2abe71a2009cf08286d2`.
Its material prompt digest is
`b51076b50fb086b233e38d88ce254d69ae39eba915e36be5ce0248a19e28bcb2`;
the original scene prompt digest is unchanged. The existing CLIP provenance,
license retention, local-beta scope, and public-distribution review still apply.

## Experimental Person-Aware Color

The optional person model supplies a coarse mask for Auto's color objectives
and protection checks. It does not identify people, infer demographics, detect
faces, or smooth skin. Accepted masks can also create a separate subject
exposure layer. Its mask must have enough
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
new Auto decisions; saved local layers remain usable. Restart after installing
or replacing a model. Neither model is bundled
in the app or downloaded automatically. See `docs/MODEL_LICENSES.md` for the
source, hash, license, and remaining deployment review.

## Experimental Face-Metered Exposure

After scene/person setup, explicitly install the optional face locator:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_face_model.py
```

This downloads the pinned 232,589-byte YuNet 2023mar ONNX model and its MIT
license, verifies SHA-256, and records provenance beside the model. An existing
download can be supplied with `--source`; setup still retrieves its license.
Default folder: `%LOCALAPPDATA%\OpenRAW Studio\models\yunet-2023mar`.
`OPENRAW_FACE_MODEL` overrides that directory; `OPENRAW_FACE=off` disables face
metering. Scene/person disable flags also disable it. Restart after replacement.

The existing CPU ONNX runtime locates faces offline. No OpenCV runtime, identity
recognition, demographic inference, or training on private photos is involved.
Face interiors must overlap the corroborated person selection. Background and
ambient-light evidence set a bounded relative exposure target; trial renders
check each face and newly clipped subject channels. Small, hidden, profile,
poorly lit, or excluded faces can be missed. Model scores are not calibrated
accuracy estimates, and a detected box is not a precise face/skin matte.

Missing/broken models leave global Auto and manual local editing available.
Saved subject layers need no model to reopen, preview, or export. No weights
are bundled or downloaded automatically. Public model distribution and broader
photographic evaluation remain under review; see `docs/MODEL_LICENSES.md`.
