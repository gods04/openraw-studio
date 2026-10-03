"""Conservative rendered neutral-cast refinement, not illuminant recognition."""

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
    """Fixed original-pixel selection shared with the cached render guard."""

    def __init__(self, preview):
        self.mask, self.metrics = _neutral_evidence(preview)
        if self.mask is not None:
            indices = np.flatnonzero(self.mask)
            self.indices = indices[::max(1, (len(indices) + 4095) // 4096)]

    def measure(self, pixels):
        if self.mask is None:
            return None
        selected = pixels[self.indices]
        return np.median(np.log(np.maximum(selected[:, (0, 2)], 1 / 255) / np.maximum(selected[:, 1:2], 1 / 255)), axis=0)


def refine_white_balance(evidence, measure, values, validate, *, detail_evidence=None, measure_detail=None, validation_strengths=()):
    """Try a measured, bounded correction only with spatially consistent votes.

    The caller owns clipping/shadow validation across its preview/native domains
    and strength samples. The two probe renders measure this renderer's response,
    including its actual camera matrix; no generic RGB-to-slider gain is assumed.
    Near-neutral materials and colored illumination remain inherently ambiguous.
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

    columns = []
    metrics["white_balance_response_probes"] = 2.0
    for key in ("warmth", "tint"):
        probe = {**values, key: values[key] + .5}
        columns.append((measure(probe) - base) / .5)
    jacobian = np.stack(columns, axis=1)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    if singular[-1] < .01 or singular[0] / singular[-1] > 20:
        return values, metrics
    # Correct at most half the estimated cast, with hard bounds and a backoff.
    delta = np.linalg.solve(jacobian, -base * .5)
    for fraction in (1.0, .5):
        candidate = {
            **values,
            "warmth": round(float(np.clip(values["warmth"] + fraction * delta[0], -.35, .35)), 2),
            "tint": round(float(np.clip(values["tint"] + fraction * delta[1], -.25, .25)), 2),
        }
        if max(abs(candidate[key] - values[key]) for key in ("warmth", "tint")) < .02:
            continue
        improved = float(np.linalg.norm(measure(candidate)))
        if improved > min(error * .9, error - .004):
            continue
        if measure_detail is not None:
            detail_improved = float(np.linalg.norm(measure_detail(candidate)))
            if detail_improved > min(detail_error * .9, detail_error - .004):
                continue
        measures = (measure, measure_detail) if measure_detail is not None else (measure,)
        if any(
            np.linalg.norm(check({key: value * strength for key, value in candidate.items()}))
            > np.linalg.norm(check({key: value * strength for key, value in values.items()})) + .001
            for strength in validation_strengths for check in measures
        ):
            continue
        if validate(candidate):
            metrics.update(white_balance_refined=1.0, white_balance_error_after=improved)
            return candidate, metrics
    return values, metrics
