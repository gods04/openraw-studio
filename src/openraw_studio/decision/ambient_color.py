"""Corroborated environmental color to retain during neutral-cast estimation."""

from dataclasses import dataclass

import numpy as np

from openraw_studio.decision.white_balance import _rgb


@dataclass(frozen=True)
class AmbientColor:
    direction: tuple[float, float]
    weight: float
    tiles: int
    fraction: float

    def retain(self, bias):
        direction = np.asarray(self.direction, np.float32)
        amount = max(0.0, float(np.dot(bias, direction))) * self.weight
        return direction * np.float32(amount)


def analyze_ambient_color(image, evidence, person=None):
    """A scene vote alone cannot turn a material color into an illuminant."""
    if evidence is None or evidence.status != 'ready':
        return None
    sunset = evidence.lights.get('Sunset', 0)
    other = max(sum(evidence.lights.get(key, 0) for key in ('Night', 'Colored light')),
                evidence.scenes.get('Aquarium', 0))
    semantic = float(np.clip((min(1, sunset + other) - .35) / .55, 0, 1)
                     * np.clip(evidence.reliability * 2, 0, 1))
    if semantic == 0:
        return None
    rgb = _rgb(image) / 255
    if rgb.ndim != 3 or min(rgb.shape[:2]) < 16 or rgb.shape[-1] != 3 or not np.isfinite(rgb).all():
        return None
    luma = rgb @ np.array([.2126, .7152, .0722], np.float32)
    high, low = rgb.max(-1), rgb.min(-1)
    saturation = (high - low) / np.maximum(high, 1 / 255)
    usable = (luma > .08) & (luma < .85) & (high < .97)
    if person is not None:
        core = person.core_mask(rgb.shape[:2])
        if core is not None:
            usable &= ~core
    colored = usable & (saturation > .08) & (saturation < .85)
    warm = sunset >= other
    if warm:
        colored &= (rgb[..., 0] > rgb[..., 1] * 1.03) & (rgb[..., 1] > rgb[..., 2] * 1.03)
    count = int(colored.sum())
    fraction = count / colored.size
    if count < 128 or fraction < .12:
        return None
    ratios = np.log(np.maximum(rgb[..., (0, 2)], 1 / 255) / np.maximum(rgb[..., 1:2], 1 / 255))
    reference = np.median(ratios[colored], axis=0)
    if np.linalg.norm(reference) < .08:
        return None
    # Sunset's warm/cool component is distinct from green/magenta. For other
    # colored environments the direction comes from measured background color.
    direction = np.array([1, -1], np.float32) if warm else reference
    direction = direction / np.linalg.norm(direction)
    height, width = colored.shape
    votes, quadrants = [], set()
    for row in range(4):
        for col in range(4):
            region = np.s_[row * height // 4:(row + 1) * height // 4,
                           col * width // 4:(col + 1) * width // 4]
            mask = colored[region]
            if mask.sum() < max(16, mask.size * .12):
                continue
            bias = np.median(ratios[region][mask], axis=0)
            agrees = np.dot(bias, direction) > .5 * np.linalg.norm(bias)
            votes.append(agrees)
            if agrees:
                quadrants.add((row // 2, col // 2))
    consensus = sum(votes) / max(1, len(votes))
    if len(votes) < 4 or len(quadrants) < 2 or consensus < .75:
        return None
    weight = semantic * float(np.clip((consensus - .6) / .35, 0, 1))
    return AmbientColor(tuple(map(float, direction)), weight, len(votes), fraction)
