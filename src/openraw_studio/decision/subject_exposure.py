"""Face-metered, context-relative local exposure with rendered-image guards."""

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from openraw_studio.core.subject import clean_subject, global_adjustments
from openraw_studio.raw.native.subject import apply_subject, subject_weights


@dataclass(frozen=True)
class SubjectExposureSuggestion:
    exposure: float | None
    status: str
    metrics: dict[str, float] = field(default_factory=dict)


def _face_samples(faces, weights, shape):
    height, width = shape
    groups = []
    x = (np.arange(width)[None, :] + .5) / width
    y = (np.arange(height)[:, None] + .5) / height
    for face in faces:
        x0, y0, x1, y1 = face.box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and .9 <= face.score <= 1):
            continue
        # An inset ellipse meters visible face interiors, not hair, clothing,
        # or a skin-color range. Intersection also corroborates segmentation.
        ellipse = (((x - (x0 + x1) / 2) / ((x1 - x0) * .32)) ** 2
                   + ((y - (y0 + y1) / 2) / ((y1 - y0) * .35)) ** 2) <= 1
        if ellipse.sum() < 24:
            continue
        selected = ellipse & (weights >= .85)
        if selected.sum() >= ellipse.sum() * .85:
            groups.append(np.flatnonzero(selected))
    return groups


def suggest_subject_exposure(image, subject, faces, scene):
    if subject is None:
        return SubjectExposureSuggestion(None, "no-selection")
    if faces.status != "ready":
        return SubjectExposureSuggestion(None, "face-" + faces.status)
    if scene is None or scene.status != "ready":
        return SubjectExposureSuggestion(None, "scene-uncertain")
    if not isinstance(image, Image.Image) or image.mode != "RGB" or min(image.size) < 1:
        raise ValueError("Subject metering requires a rendered RGB8 image")
    subject = clean_subject(subject)
    pixels = np.asarray(image)
    weights = subject_weights(subject, image.size)
    groups = _face_samples(faces.faces, weights, pixels.shape[:2])
    if not groups:
        return SubjectExposureSuggestion(None, "face-outside-selection")
    rgb = pixels.reshape(-1, 3).astype(np.float32) / 255
    luma = rgb @ np.array([.2126, .7152, .0722], np.float32)
    background = (weights.ravel() < .01) & (luma > .04) & (luma < .95)
    if background.sum() < max(100, luma.size * .10):
        return SubjectExposureSuggestion(None, "insufficient-context")
    ambient = max(sum(scene.lights.get(key, 0) for key in ("Night", "Sunset", "Colored light")),
                  scene.scenes.get("Aquarium", 0)) * scene.reliability
    ambient = float(np.clip(ambient, 0, 1))
    context = float(np.quantile(luma[background], .75))
    selected = weights.ravel() > .01
    before = np.array([np.median(luma[indices]) for indices in groups])
    upper = np.array([np.quantile(luma[indices], .9) for indices in groups])
    # A relative fill objective leaves dark environments dark. There is no
    # universal target skin value; scene evidence continuously limits the lift.
    target = np.maximum(before, np.minimum(context * .85, before + .12 * (1 - .75 * ambient)))
    bright = (before > .65) & (upper > .86) & (context < before * .8)
    target[bright] = np.maximum(context * 1.3, before[bright] - .10)
    delta = target - before
    metrics = {"metered_faces": float(len(groups)), "context_luma": context, "ambient_weight": ambient,
               "face_luma_before": float(before.mean()), "face_luma_target": float(target.mean())}
    if np.max(np.abs(delta)) < .035:
        return SubjectExposureSuggestion(None, "balanced", metrics)
    if np.any(delta > .035) and np.any(delta < -.035):
        return SubjectExposureSuggestion(None, "conflicting-faces", metrics)
    direction = 1 if np.max(delta) > .035 else -1
    maximum = .8 - .5 * ambient if direction > 0 else .5
    targets = np.log2(np.maximum(target, .02))
    baseline_loss = float(np.mean((np.log2(np.maximum(before, .02)) - targets) ** 2))
    best = (baseline_loss, 0.0, before)
    samples = 0
    for amount in np.linspace(.05, maximum, 12):
        exposure = round(float(amount * direction), 4)
        edited = apply_subject(pixels, {**subject, "exposure": exposure, "enabled": True})
        values = edited.reshape(-1, 3).astype(np.float32) / 255
        edited_luma = values @ np.array([.2126, .7152, .0722], np.float32)
        medians = np.array([np.median(edited_luma[indices]) for indices in groups])
        samples += 1
        # Every metered face must benefit or stay effectively unchanged; a
        # brighter person cannot be sacrificed to an average group objective.
        if np.any(np.abs(medians - target) > np.abs(before - target) + .015):
            continue
        if any(np.mean((values[indices] >= 254 / 255) & (rgb[indices] < 250 / 255)) > .001
               for indices in groups):
            continue
        if np.mean((values[selected] >= 254 / 255) & (rgb[selected] < 250 / 255)) > .002:
            continue
        if np.any(medians > .75) or np.any(medians < np.minimum(before, .10)):
            continue
        loss = float(np.mean((np.log2(np.maximum(medians, .02)) - targets) ** 2) + .015 * exposure ** 2)
        if loss < best[0]:
            best = (loss, exposure, medians)
    metrics.update(validation_renders=float(samples), objective_before=baseline_loss, objective_after=best[0],
                   face_luma_after=float(best[2].mean()))
    if best[1] == 0 or baseline_loss - best[0] < .003 or np.max(np.abs(best[2] - before)) < .02:
        return SubjectExposureSuggestion(None, "no-safe-benefit", metrics)
    return SubjectExposureSuggestion(best[1], "suggested", metrics)


def suggest_subject_exposure_for_photo(photo, overrides, *, scene=None):
    """Meter against current global edits, never stack exposure on a prior layer."""
    subject = overrides.get("subject")
    if subject is None:
        return SubjectExposureSuggestion(None, "no-selection")
    from openraw_studio.vision.face import analyze_faces
    from openraw_studio.vision.scene import analyze_scene

    original = photo.render({})[0]
    faces = analyze_faces(original)
    if faces.status != "ready":
        return SubjectExposureSuggestion(None, "face-" + faces.status)
    if scene is None:
        scene = analyze_scene(original)
    if scene.status != "ready":
        return SubjectExposureSuggestion(None, "scene-uncertain")
    rendered = photo.render(global_adjustments(overrides))[0]
    return suggest_subject_exposure(rendered, subject, faces, scene)
