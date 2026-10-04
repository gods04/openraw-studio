"""Content-conditioned color objectives solved against the actual RAW renderer."""

from __future__ import annotations

import numpy as np

from openraw_studio.decision.white_balance import NeutralCast, _rgb


def _pixels(image):
    rgb = _rgb(image) / 255
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or not np.isfinite(rgb).all():
        raise ValueError("Scene color needs a finite spatial RGB preview")
    return rgb.reshape(-1, 3)


def _saturation(rgb):
    high, low = _extrema(rgb)
    return (high - low) / np.maximum(high, 1 / 255)


def _extrema(rgb):
    return (np.maximum(np.maximum(rgb[:, 0], rgb[:, 1]), rgb[:, 2]),
            np.minimum(np.minimum(rgb[:, 0], rgb[:, 1]), rgb[:, 2]))


def _ratios(rgb):
    return np.log(np.maximum(rgb[:, (0, 2)], 1 / 255) / np.maximum(rgb[:, 1:2], 1 / 255))


def _subject_regions(mask, shape):
    """Keep small subject colors from being averaged away by large clothing."""
    spatial = mask.reshape(shape)
    ys, xs = np.nonzero(spatial)
    y_edges = np.linspace(ys.min(), ys.max() + 1, 5, dtype=int)
    x_edges = np.linspace(xs.min(), xs.max() + 1, 5, dtype=int)
    minimum = max(8, np.count_nonzero(mask) * .015)
    groups = []
    for y0, y1 in zip(y_edges, y_edges[1:]):
        for x0, x1 in zip(x_edges, x_edges[1:]):
            region = np.zeros_like(spatial)
            region[y0:y1, x0:x1] = spatial[y0:y1, x0:x1]
            if np.count_nonzero(region) >= minimum:
                groups.append(np.flatnonzero(region))
    return groups or [np.flatnonzero(mask)]


class ColorObjective:
    """Material color hints, optionally separated from a corroborated person mask."""

    def __init__(self, original, baseline, evidence, person=None):
        self.original = _pixels(original)
        self.baseline = _pixels(baseline)
        self.shape = self.original.shape
        if self.baseline.shape != self.shape:
            raise ValueError("Scene color baseline shape mismatch")
        self.parts = []
        self.weights = []
        self.targets = []
        rgb = self.original
        red, green, blue = rgb.T
        luma = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
        usable = (luma > .08) & (luma < .85) & (rgb.max(-1) < .97)
        reliability = evidence.reliability
        scenes, lights = evidence.scenes, evidence.lights
        portrait = scenes.get("Portrait", 0) * reliability
        ambient_vote = sum(lights.get(key, 0) for key in ("Sunset", "Night", "Colored light"))
        ambient = max(float(np.clip((ambient_vote - .5) / .4, 0, 1)), scenes.get("Aquarium", 0)) * reliability
        daylight = sum(lights.get(key, 0) for key in ("Daylight", "Overcast")) * reliability
        minimum = max(32, len(rgb) * .025)
        self.person = None
        self.person_regions = []
        subject_mask = None
        if person is not None:
            subject_mask = person.core_mask(_rgb(original).shape[:2])
            if subject_mask is not None:
                self.person = subject_mask.reshape(-1) & usable & (_saturation(rgb) > .10)
                if np.count_nonzero(self.person) < max(16, len(rgb) * .002):
                    self.person = None
                else:
                    self.person_regions = _subject_regions(self.person, _rgb(original).shape[:2])

        def add(kind, mask, target, weight):
            self.parts.append((kind, np.flatnonzero(mask)))
            self.targets.extend(np.atleast_1d(target))
            self.weights.extend([weight] * len(np.atleast_1d(target)))

        greens = usable & (green > red * 1.06) & (green > blue * 1.04)
        blues = usable & (blue > red * 1.08) & (blue > green * .98)
        if subject_mask is not None:
            greens &= ~subject_mask.reshape(-1)
            blues &= ~subject_mask.reshape(-1)
        for mask, relevance in (
            (greens, sum(scenes.get(key, 0) for key in ("Grassland", "Forest"))),
            (blues, sum(scenes.get(key, 0) for key in ("Coast", "Sky", "Mountains"))),
        ):
            influence = relevance * reliability * (1 - portrait) * (1 - ambient)
            if influence >= .12 and np.count_nonzero(mask) >= minimum:
                saturation = float(np.median(_saturation(self.baseline[mask])))
                # Muted materials get more opportunity than already-vivid ones.
                lift = min(.04, max(0, .55 - saturation) * .12) * influence
                if lift > .002:
                    add("saturation", mask, saturation + lift, 2.0)

        colorful = usable & (_saturation(rgb) > .10)
        if ambient >= .25 and np.count_nonzero(colorful) >= minimum:
            current = np.median(_ratios(self.baseline[colorful]), axis=0)
            reference = np.median(_ratios(rgb[colorful]), axis=0)
            add("ratios", colorful, current + ambient * (reference - current), .6)
        neutral = NeutralCast(original)
        if daylight > .35 and ambient < .25 and neutral.mask is not None:
            mask = neutral.mask.reshape(-1)
            current = np.median(_ratios(self.baseline[mask]), axis=0)
            add("ratios", mask, current * (1 - .25 * daylight), .8)
        if self.person is not None:
            # Keep measured subject color while solving scenery objectives;
            # no preferred skin hue or ethnicity is assumed.
            for indices in self.person_regions:
                self.parts.append(("ratios", indices))
                self.targets.extend(np.median(_ratios(self.baseline[indices]), axis=0))
                self.weights.extend([1 / np.sqrt(len(self.person_regions))] * 2)

        # Warm material candidates protect possible skin without asserting a
        # face, ethnicity, or a target skin tone. Never brighten/whiten by label.
        self.warm = usable & (red > green * 1.04) & (green > blue * 1.03) & (_saturation(rgb) < .65)
        self.protect_warm = portrait >= .15 and np.count_nonzero(self.warm) >= minimum
        self.target = np.asarray(self.targets, dtype=np.float32)
        self.weight = np.asarray(self.weights, dtype=np.float32)

    def measure(self, image):
        rgb = _pixels(image)
        if rgb.shape != self.shape:
            raise ValueError("Scene color candidate shape mismatch")
        result = []
        for kind, indices in self.parts:
            samples = rgb[indices]
            value = np.median(_saturation(samples)) if kind == "saturation" else np.median(_ratios(samples), axis=0)
            result.extend(np.atleast_1d(value))
        return np.asarray(result, dtype=np.float32)

    def preserved(self, image, baseline=None):
        rgb = _pixels(image)
        if rgb.shape != self.shape:
            return False
        baseline = self.baseline if baseline is None else _pixels(baseline)
        if baseline.shape != self.shape:
            return False
        protected = ([self.warm] if self.protect_warm else []) + self.person_regions
        for mask in protected:
            before, after = baseline[mask], rgb[mask]
            if np.median(_saturation(after)) > np.median(_saturation(before)) + .012:
                return False
            # Opponent-chroma direction excludes brightness and limits hue drift.
            a = before[:, (0, 2)] - before[:, 1:2]
            b = after[:, (0, 2)] - after[:, 1:2]
            chromatic = np.linalg.norm(a, axis=1) > 1e-3
            if not chromatic.any():
                continue
            a, b = a[chromatic], b[chromatic]
            cosine = np.sum(a * b, axis=1) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-6)
            if np.quantile(cosine, .1) < np.cos(np.deg2rad(4)):
                return False
        # Do not trade broad color clipping for a small objective improvement.
        high, low = _extrema(baseline)
        before = high - low
        high, low = _extrema(rgb)
        after = high - low
        return float(np.mean((before < .85) & (after >= .98))) <= .001


def refine_scene_color(original, render, values, evidence, validate, *, detail_preview=None,
                       render_detail=None, validation_strengths=(), person=None):
    metrics = {"scene_color_refined": 0.0, "scene_color_renders": 0.0}
    if evidence is None or evidence.status != "ready":
        return values, metrics
    original_render, original_detail = render, render_detail

    def render(values):
        metrics["scene_color_renders"] += 1
        return original_render(values)

    if original_detail is not None:
        def render_detail(values):
            metrics["scene_color_renders"] += 1
            return original_detail(values)
    baseline = render(values)
    objective = ColorObjective(original, baseline, evidence, person)
    if objective.person is not None:
        metrics["person_color_samples"] = float(np.count_nonzero(objective.person))
        metrics["person_color_regions"] = float(len(objective.person_regions))
    if not len(objective.target):
        return values, metrics
    base = objective.measure(baseline)
    residual = (objective.target - base) * objective.weight
    error = float(np.linalg.norm(residual))
    metrics.update(scene_color_error_before=error, scene_color_error_after=error)
    if error < .003:
        return values, metrics
    keys = ("warmth", "tint", "saturation")
    columns = []
    for key in keys:
        probe = {**values, key: values[key] + .1}
        columns.append((objective.measure(render(probe)) - base) * objective.weight / .1)
    response = np.stack(columns, axis=1)
    # Ridge regularization avoids large moves in weakly measured directions.
    system = np.vstack([response, np.eye(3) * .025])
    delta = np.linalg.lstsq(system, np.concatenate([residual, np.zeros(3)]), rcond=None)[0]
    delta = np.clip(delta, [-.12, -.10, -.10], [.12, .10, .10]) * evidence.reliability
    detail = None
    if render_detail is not None:
        detail = ColorObjective(detail_preview, render_detail(values), evidence, person)
    for amount in (1.0, .5):
        candidate = {**values, **{
            key: round(float(np.clip(values[key] + amount * change, -limit, limit)), 4)
            for key, change, limit in zip(keys, delta, (.35, .25, .18))
        }}
        image = render(candidate)
        improved = float(np.linalg.norm((objective.target - objective.measure(image)) * objective.weight))
        if improved >= error * .9 or not objective.preserved(image):
            continue
        if detail is not None:
            detail_image = render_detail(candidate)
            if not detail.preserved(detail_image):
                continue
            if len(detail.target):
                before = np.linalg.norm((detail.target - detail.measure(render_detail(values))) * detail.weight)
                after = np.linalg.norm((detail.target - detail.measure(detail_image)) * detail.weight)
                if after > before + .001:
                    continue
        # Strength interpolation also retains warm subject hues, not only tones.
        domains = [(objective, render)]
        if render_detail is not None:
            domains.append((detail, render_detail))
        if any(not check.preserved(draw({k: v * s for k, v in candidate.items()}),
                                   baseline=draw({k: v * s for k, v in values.items()}))
               for check, draw in domains for s in validation_strengths):
            continue
        if validate(candidate):
            metrics.update(scene_color_refined=1.0, scene_color_error_after=improved)
            return candidate, metrics
    return values, metrics
