"""Conservative local cast advice from spatially corroborated neutral candidates."""

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from openraw_studio.core.subject import clean_subject, subject_with_color
from openraw_studio.decision.subject_exposure import _face_samples
from openraw_studio.raw.native.subject import subject_weights
from openraw_studio.raw.native.subject_color import apply_subject_color


@dataclass(frozen=True)
class SubjectColorSuggestion:
    warmth: float | None
    tint: float | None
    status: str
    metrics: dict[str, float] = field(default_factory=dict)


def _ratios(rgb):
    return np.log(np.maximum(rgb[..., (0, 2)], .004) / np.maximum(rgb[..., 1:2], .004))


def _neutral_regions(rgb, core, faces):
    high, low = rgb.max(-1), rgb.min(-1)
    luma = rgb @ np.array([.2126, .7152, .0722], np.float32)
    usable = ((high - low) / np.maximum(high, .004) < .18) & (luma > .25) & (luma < .8)
    height, width = core.shape
    for face in faces:
        x0, y0, x1, y1 = face.box
        usable[max(0, int(y0 * height)):min(height, int(y1 * height) + 1),
               max(0, int(x0 * width)):min(width, int(x1 * width) + 1)] = False
    selected = usable & core
    if selected.sum() < max(128, core.sum() * .12):
        return None, usable, {}
    ratios = _ratios(rgb)
    ys, xs = np.nonzero(core)
    y_edges = np.linspace(ys.min(), ys.max() + 1, 5, dtype=int)
    x_edges = np.linspace(xs.min(), xs.max() + 1, 5, dtype=int)
    biases, groups = [], []
    for ya, yb in zip(y_edges[:-1], y_edges[1:]):
        for xa, xb in zip(x_edges[:-1], x_edges[1:]):
            region = selected[ya:yb, xa:xb]
            if region.sum() < max(16, region.size * .1):
                continue
            biases.append(np.median(ratios[ya:yb, xa:xb][region], axis=0))
            yy, xx = np.nonzero(region)
            groups.append((yy + ya) * width + xx + xa)
    shared = np.mean(biases, axis=0) if biases else np.zeros(2)
    votes = [np.dot(bias, shared) > .3 * np.linalg.norm(bias) * np.linalg.norm(shared) for bias in biases]
    consensus = sum(votes) / max(1, len(votes))
    metrics = {'neutral_fraction': float(selected.sum() / max(1, core.sum())),
               'neutral_tiles': float(len(votes)), 'neutral_consensus': float(consensus)}
    if len(votes) < 4 or consensus < .8 or np.quantile(luma[selected], .9) - np.quantile(luma[selected], .1) < .05:
        return None, usable, metrics
    return groups, usable, metrics


def suggest_subject_color(image, subject, faces, scene, material=None):
    def abstain(status, metrics=None):
        return SubjectColorSuggestion(None, None, status, metrics or {})
    if subject is None:
        return abstain('no-selection')
    if faces.status != 'ready':
        return abstain('face-' + faces.status)
    if scene is None or scene.status != 'ready':
        return abstain('scene-uncertain')
    if material is None or material.status != 'ready':
        return abstain('material-' + (material.status if material is not None else 'not-installed'))
    ambient = sum(scene.lights.get(key, 0) for key in ('Night', 'Sunset', 'Colored light'))
    if ambient >= .5 or scene.scenes.get('Aquarium', 0) >= .2:
        return abstain('ambient-light')
    if not isinstance(image, Image.Image) or image.mode != 'RGB':
        raise ValueError('Subject color analysis requires rendered RGB8')
    subject = clean_subject(subject)
    pixels = np.asarray(image)
    rgb = pixels.astype(np.float32) / 255
    weights = subject_weights(subject, image.size)
    face_groups = _face_samples(faces.faces, weights, pixels.shape[:2])
    if not face_groups:
        return abstain('face-outside-selection')
    groups, usable, metrics = _neutral_regions(rgb, weights >= .9, faces.faces)
    if groups is None:
        return abstain('insufficient-neutrals', metrics)
    background = usable & (weights < .01)
    if background.sum() < max(256, (weights < .01).sum() * .1):
        return abstain('insufficient-background', metrics)
    ratios = _ratios(rgb).reshape(-1, 2)
    # Equal spatial votes prevent a large shirt/trouser area from concealing
    # an oppositely lit smaller region. Every region is checked after rendering.
    baseline = np.mean([np.median(ratios[group], axis=0) for group in groups], axis=0)
    reference = np.median(ratios[background.ravel()], axis=0)
    error = float(np.linalg.norm(baseline))
    difference = float(np.linalg.norm(baseline - reference))
    metrics.update(cast_before=error, cast_after=error, background_cast=float(np.linalg.norm(reference)),
                   subject_background_difference=difference, validation_renders=0.0)
    # Background is corroboration of differing light, never a target shirt or
    # skin color. Near-neutral material remains intrinsically ambiguous.
    if error < .045 or difference < .06:
        return abstain('balanced', metrics)
    def measure(warmth, tint):
        candidate = subject_with_color({**subject, 'enabled': True}, warmth, tint)
        edited = apply_subject_color(pixels, candidate)
        measured = _ratios(edited.astype(np.float32) / 255).reshape(-1, 2)
        metrics['validation_renders'] += 1
        return edited, measured, np.mean([np.median(measured[group], axis=0) for group in groups], axis=0)
    columns = [(measure(.4, 0)[2] - baseline) / .4, (measure(0, .4)[2] - baseline) / .4]
    jacobian = np.stack(columns, axis=1)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    if singular[-1] < .025 or singular[0] / singular[-1] > 12:
        return abstain('weak-response', metrics)
    strength = .5 * min(1, scene.reliability * 2) * (1 - ambient)
    delta = np.linalg.solve(jacobian, -baseline * strength)
    region_before = [np.linalg.norm(np.median(ratios[group], axis=0)) for group in groups]
    face_before = [np.median(ratios[group], axis=0) for group in face_groups]
    for amount in (1, .5, .25):
        warmth, tint = (round(float(np.clip(value * amount, -.3, .3)), 4) for value in delta)
        if max(abs(warmth), abs(tint)) < .02:
            continue
        edited, measured, cast = measure(warmth, tint)
        improved = float(np.linalg.norm(cast))
        if improved > min(error * .9, error - .004):
            continue
        if any(np.linalg.norm(np.median(measured[group], axis=0)) > before + .004
               for group, before in zip(groups, region_before)):
            continue
        if any(np.linalg.norm(np.median(measured[group], axis=0) - before) > .06
               for group, before in zip(face_groups, face_before)):
            continue
        selected = weights > .01
        if np.mean((edited[selected] >= 254) & (pixels[selected] < 250)) > .001:
            continue
        metrics['cast_after'] = improved
        return SubjectColorSuggestion(warmth, tint, 'suggested', metrics)
    return abstain('no-safe-benefit', metrics)


def suggest_subject_color_for_photo(photo, overrides, *, scene=None):
    subject = overrides.get('subject')
    if subject is None:
        return SubjectColorSuggestion(None, None, 'no-selection')
    from openraw_studio.vision.face import analyze_faces
    from openraw_studio.vision.material import analyze_material
    from openraw_studio.vision.scene import analyze_scene
    original = photo.render({})[0]
    faces = analyze_faces(original)
    if faces.status != 'ready':
        return SubjectColorSuggestion(None, None, 'face-' + faces.status)
    scene = scene if scene is not None else analyze_scene(original)
    material = analyze_material(original, subject_weights(subject, original.size))
    baseline = {**overrides, 'subject': subject_with_color({**subject, 'enabled': True}, 0, 0)}
    return suggest_subject_color(photo.render(baseline)[0], subject, faces, scene, material)
