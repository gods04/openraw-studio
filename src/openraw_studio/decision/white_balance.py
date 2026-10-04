"""Conservative neutral-cast refinement with an optional ambient reference."""

import numpy as np

from openraw_studio.raw.native.tone import PreviewRgbImage


def _rgb(image):
    if isinstance(image, PreviewRgbImage):
        return np.asarray(image.pixels, np.float32).reshape(image.height, image.width, 3)
    return np.asarray(image, np.float32)


def _log_ratios(rgb):
    return np.log(np.maximum(rgb[..., (0, 2)], 1) / np.maximum(rgb[..., 1:2], 1))


def _neutral_evidence(image):
    rgb = _rgb(image)
    if rgb.ndim != 3 or min(rgb.shape[:2]) < 16:
        return None, {"neutral_tiles": 0.0, "neutral_consensus": 0.0}
    high, low = rgb.max(-1), rgb.min(-1)
    luma = rgb @ np.array([.2126, .7152, .0722], np.float32) / 255
    mask = ((high - low) / np.maximum(high, 1) < .22) & (luma > .15) & (luma < .8)
    fraction = float(mask.mean())
    channels = np.bincount(np.argmax(rgb, axis=-1).ravel(), minlength=3) / mask.size
    median = float(np.median(luma))
    # Dim red/blue illumination can make large pale surfaces look like neutrals.
    # Without semantic or reference evidence, retain that ambient color.
    dim_ambient = median < .35 and max(channels[0], channels[2]) > .85
    metrics = {"neutral_tiles": 0.0, "neutral_consensus": 0.0, "neutral_fraction": fraction}
    if fraction < .12 or median < .18 or dim_ambient or (channels.max() > .85 and fraction < .5):
        return None, metrics
    ratios = _log_ratios(rgb)
    bias = np.median(ratios[mask], axis=0)
    norm = float(np.linalg.norm(bias))
    if norm < .025:
        return None, metrics
    height, width = mask.shape
    votes = []
    quadrants = set()
    for row in range(4):
        for col in range(4):
            region = np.s_[row * height // 4:(row + 1) * height // 4, col * width // 4:(col + 1) * width // 4]
            valid = mask[region]
            if valid.mean() >= .12 and valid.sum() >= 16:
                vote = np.median(ratios[region][valid], axis=0)
                length = float(np.linalg.norm(vote))
                agrees = length > .015 and np.dot(vote, bias) > .6 * norm * length
                votes.append(agrees)
                if agrees:
                    quadrants.add((row // 2, col // 2))
    consensus = sum(votes) / max(1, len(votes))
    metrics.update(neutral_tiles=float(len(votes)), neutral_consensus=float(consensus))
    if len(votes) < 6 or consensus < .8 or len(quadrants) < 3:
        return None, metrics
    return mask, metrics


class NeutralCast:
    """Fixed original pixels and ambient target shared by every render probe."""

    def __init__(self, preview, *, ambient=None):
        self.mask, self.metrics = _neutral_evidence(preview)
        self.retained_bias = None
        self.regions = ()
        if self.mask is not None:
            indices = np.flatnonzero(self.mask)
            self.indices = indices[::max(1, (len(indices) + 4095) // 4096)]
            height, width = self.mask.shape
            ys, xs = np.divmod(self.indices, width)
            regions = []
            for row in range(4):
                for col in range(4):
                    y0, y1 = row * height // 4, (row + 1) * height // 4
                    x0, x1 = col * width // 4, (col + 1) * width // 4
                    valid = self.mask[y0:y1, x0:x1]
                    group = np.flatnonzero((ys >= y0) & (ys < y1) & (xs >= x0) & (xs < x1))
                    if valid.mean() >= .12 and valid.sum() >= 16 and len(group) >= 8:
                        regions.append(group)
            self.regions = tuple(regions)
            if ambient is not None:
                self.retained_bias = ambient.retain(self.measure(_rgb(preview).reshape(-1, 3) / 255))
                self.metrics.update(ambient_retention=ambient.weight,
                                    retained_red_ratio=float(self.retained_bias[0]),
                                    retained_blue_ratio=float(self.retained_bias[1]))

    def measure(self, pixels):
        if self.mask is None:
            return None
        bias = np.median(self._sample_ratios(pixels), axis=0)
        return bias if self.retained_bias is None else bias - self.retained_bias

    def _sample_ratios(self, pixels):
        selected = pixels[self.indices]
        return np.log(np.maximum(selected[:, (0, 2)], 1 / 255) / np.maximum(selected[:, 1:2], 1 / 255))

    def measure_with_regions(self, pixels):
        """Use the same bounded original samples for global and spatial votes."""
        if self.mask is None:
            return None, None
        ratios = self._sample_ratios(pixels)
        bias = np.median(ratios, axis=0)
        regions = np.asarray([np.median(ratios[group], axis=0) for group in self.regions], np.float32).reshape(-1, 2)
        if self.retained_bias is not None:
            bias = bias - self.retained_bias
            regions = regions - self.retained_bias
        return bias, regions


def _regional_strength(biases):
    common = np.median(biases, axis=0)
    signal = float(np.linalg.norm(common))
    spread = float(np.quantile(np.linalg.norm(biases - common, axis=1), .75))
    # Dispersion reduces the permitted correction; this is not a calibrated
    # illuminant confidence or proof that a near-neutral material is gray.
    return float(np.clip(.75 / (1 + (spread / max(signal, .025)) ** 2), .25, .75))


def _regions_preserved(before, after):
    if (before is None or after is None or before.ndim != 2 or before.shape[1] != 2
            or after.shape != before.shape or not np.isfinite(before).all() or not np.isfinite(after).all()):
        return False
    return bool(np.all(np.linalg.norm(after, axis=1) <= np.linalg.norm(before, axis=1) + .01))


def _bounded_color_delta(response, target, lower, upper):
    """Solve the two-control box fit, including its four boundary optima."""
    candidates = [np.clip(np.linalg.solve(response, target), lower, upper)]
    for fixed in (0, 1):
        free = 1 - fixed
        column = response[:, free]
        for edge in (lower[fixed], upper[fixed]):
            delta = np.zeros(2)
            delta[fixed] = edge
            delta[free] = np.clip(np.dot(column, target - response[:, fixed] * edge)
                                  / np.dot(column, column), lower[free], upper[free])
            candidates.append(delta)
    return min(candidates, key=lambda delta: float(np.sum((response @ delta - target) ** 2)))


def refine_white_balance(evidence, measure, values, validate, *, detail_evidence=None, measure_detail=None,
                         validation_strengths=(), measure_regions=None, measure_detail_regions=None):
    """Try a measured, bounded correction only with spatially consistent votes.

    The caller owns clipping/shadow validation across its preview/native domains
    and strength samples. The two probe renders measure this renderer's response,
    including its actual camera matrix; no generic RGB-to-slider gain is assumed.
    An optional corroborated ambient component is retained in the original
    reference; the solver fits only the residual, including at weaker strengths.
    Near-neutral materials and colored illumination remain inherently ambiguous.
    Optional regional measurements adapt the target fraction to spatial
    dispersion and prevent aggregate improvement from hiding a worsened region.
    """
    metrics = {"white_balance_refined": 0.0, "white_balance_response_probes": 0.0, **{f"white_balance_{key}": value for key, value in evidence.metrics.items()}}
    if evidence.mask is None:
        return values, metrics
    base = measure(values)
    error = float(np.linalg.norm(base))
    metrics["white_balance_error_before"] = error
    metrics["white_balance_error_after"] = error
    if error < .025:
        return values, metrics
    if measure_detail is not None:
        if detail_evidence.mask is None:
            return values, metrics
        detail_base = measure_detail(values)
        detail_error = float(np.linalg.norm(detail_base))
        if detail_error < .025 or np.dot(base, detail_base) < .8 * error * detail_error:
            return values, metrics

    regional = []
    for read in (measure_regions, measure_detail_regions):
        if read is not None:
            biases = read(values)
            if biases is None or biases.ndim != 2 or biases.shape[1] != 2 or len(biases) < 4 or not np.isfinite(biases).all():
                return values, metrics
            regional.append((read, biases))
    strength = min((_regional_strength(biases) for _, biases in regional), default=.5)
    if regional:
        metrics.update(white_balance_target_fraction=strength,
                       white_balance_applied_fraction=0.0,
                       white_balance_regions=float(len(regional[0][1])),
                       white_balance_region_error_before=float(np.mean(np.linalg.norm(regional[0][1], axis=1))))
        metrics['white_balance_region_error_after'] = metrics['white_balance_region_error_before']

    columns = []
    metrics["white_balance_response_probes"] = 2.0
    for key in ("warmth", "tint"):
        probe = {**values, key: values[key] + .5}
        columns.append((measure(probe) - base) / .5)
    jacobian = np.stack(columns, axis=1)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    if singular[-1] < .01 or singular[0] / singular[-1] > 20:
        return values, metrics
    if regional:
        current = np.array([values['warmth'], values['tint']])
        lower, upper = np.array([-.35, -.25]) - current, np.array([.35, .25]) - current
        fractions = (strength, strength * .5, strength * .25) + ((.5,) if strength > .5 else ())
        deltas = [(_bounded_color_delta(jacobian, -base * fraction, lower, upper), fraction) for fraction in fractions]
        if strength >= .5:
            # Keep the established proposal in the actual-render comparison;
            # a better linear fit need not win after nonlinear tone/quantization.
            deltas.append((np.linalg.solve(jacobian, -base * .5), .5))
    else:
        delta = np.linalg.solve(jacobian, -base * .5)
        deltas = [(delta, .5), (delta * .5, .25)]
    trials, seen = [], set()
    for delta, fraction in deltas:
        candidate = {
            **values,
            "warmth": round(float(np.clip(values["warmth"] + delta[0], -.35, .35)), 2),
            "tint": round(float(np.clip(values["tint"] + delta[1], -.25, .25)), 2),
        }
        if max(abs(candidate[key] - values[key]) for key in ("warmth", "tint")) < .02:
            continue
        key = (candidate['warmth'], candidate['tint'])
        if key in seen:
            continue
        seen.add(key)
        improved = float(np.linalg.norm(measure(candidate)))
        if improved > min(error * .9, error - .004):
            continue
        scores = [improved / error]
        if measure_detail is not None:
            detail_improved = float(np.linalg.norm(measure_detail(candidate)))
            if detail_improved > min(detail_error * .9, detail_error - .004):
                continue
            scores.append(detail_improved / detail_error)
        region_after = [read(candidate) for read, _ in regional]
        if any(not _regions_preserved(before, after)
               or np.mean(np.linalg.norm(after, axis=1)) > np.mean(np.linalg.norm(before, axis=1)) - .004
               for (_, before), after in zip(regional, region_after)):
            continue
        scores.extend(float(np.mean(np.linalg.norm(after, axis=1)) / np.mean(np.linalg.norm(before, axis=1)))
                      for (_, before), after in zip(regional, region_after))
        trials.append((max(scores), candidate, improved, region_after, fraction))
    if regional:
        metrics['white_balance_candidates'] = float(len(seen))
        trials.sort(key=lambda trial: (trial[0], trial[2],
                    abs(trial[1]['warmth'] - values['warmth']) + abs(trial[1]['tint'] - values['tint'])))
    for _, candidate, improved, region_after, fraction in trials:
        measures = (measure, measure_detail) if measure_detail is not None else (measure,)
        if any(
            np.linalg.norm(check({key: value * strength for key, value in candidate.items()}))
            > np.linalg.norm(check({key: value * strength for key, value in values.items()})) + .001
            for strength in validation_strengths for check in measures
        ):
            continue
        if any(not _regions_preserved(read({key: value * strength for key, value in values.items()}),
                                      read({key: value * strength for key, value in candidate.items()}))
               for strength in validation_strengths for read, _ in regional):
            continue
        if validate(candidate):
            metrics.update(white_balance_refined=1.0, white_balance_error_after=improved)
            if regional:
                metrics['white_balance_applied_fraction'] = fraction
                metrics['white_balance_region_error_after'] = float(np.mean(np.linalg.norm(region_after[0], axis=1)))
                metrics['white_balance_region_worst_change'] = float(np.max(
                    np.linalg.norm(region_after[0], axis=1) - np.linalg.norm(regional[0][1], axis=1)))
            return candidate, metrics
    return values, metrics
