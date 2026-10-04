import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_from_preview
from openraw_studio.decision.tonal_intent import dark_noise_evidence
from openraw_studio.vision.scene import SceneEvidence


def noisy_dark(seed=31):
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal([19, 7, 14], 6, (320, 480, 3)), 0, 255).astype(np.uint8)


class DarkNoiseEvidenceTests(unittest.TestCase):
    def test_noise_is_measured_without_changing_source_or_rendering_candidates(self):
        image = noisy_dark()
        saved = image.copy()
        render = Mock(side_effect=AssertionError('Unnecessary candidate render'))
        scene = SceneEvidence('ready', 'Aquarium', 'Colored light', 1,
                              {'Aquarium': 1}, {'Colored light': 1})
        result = suggest_auto_adjustments_from_preview(image, render=render, scene_evidence=scene)
        self.assertEqual(set(result.as_overrides().values()), {0})
        self.assertEqual(result.metrics['noise_dominated_tones'], 1)
        self.assertLess(result.metrics['dark_noise_structure_ratio'], .12)
        self.assertEqual(result.scene_evidence.status, 'insufficient-information')
        self.assertIn('noise', result.rationale[0])
        render.assert_not_called()
        np.testing.assert_array_equal(image, saved)

    def test_noise_attenuation_in_small_proxy_does_not_hide_fine_domain_evidence(self):
        detail = noisy_dark()
        small = Image.fromarray(detail).resize((120, 80), Image.Resampling.BOX)
        self.assertIsNone(dark_noise_evidence(small))
        self.assertIsNotNone(dark_noise_evidence(small, detail))
        self.assertIsNotNone(dark_noise_evidence(detail, small))

    def test_structure_in_either_domain_vetoes_abstention(self):
        noise = noisy_dark()
        for amplitude in (1, 2, 4, 7):
            gradient = np.broadcast_to(np.linspace(0, amplitude, 480)[None, :, None], noise.shape)
            for pair in ((noise, gradient), (gradient, noise)):
                with self.subTest(amplitude=amplitude):
                    self.assertIsNone(dark_noise_evidence(*pair))

    def test_smooth_gradient_survives_even_when_darker_than_noise(self):
        for amplitude in (1, 2, 4, 7):
            gradient = np.broadcast_to(np.linspace(0, amplitude, 480)[None, :, None], (320, 480, 3))
            self.assertIsNone(dark_noise_evidence(gradient))

    def test_visible_gradient_and_small_spatial_subject_survive_noise(self):
        gradient = noisy_dark().astype(np.float32)
        gradient += np.linspace(0, 7, 480)[None, :, None]
        subject = noisy_dark()
        subject[25:45, 35:55] = 40
        for image in (gradient, subject):
            self.assertIsNone(dark_noise_evidence(image))

    def test_repeated_fine_texture_is_not_mistaken_for_random_noise(self):
        y, x = np.indices((320, 480))
        for size in (1, 2, 4):
            for pattern in ((x // size + y // size) % 2, (x // size) % 2):
                texture = np.broadcast_to(8 + pattern[..., None] * 12, (320, 480, 3)).copy()
                self.assertIsNone(dark_noise_evidence(texture))
                noisy = texture + np.random.default_rng(23).normal(0, 3, texture.shape)
                self.assertIsNone(dark_noise_evidence(np.clip(noisy, 0, 255)))

    def test_paired_small_texture_does_not_get_hidden_by_larger_noise_image(self):
        y, x = np.indices((80, 120))
        small = np.broadcast_to(8 + ((x + y) % 2)[..., None] * 12, (80, 120, 3))
        self.assertIsNone(dark_noise_evidence(small, noisy_dark()))

    def test_connected_tiny_lights_including_edges_and_diagonals_survive(self):
        for points in (((0, 0), (0, 1)), ((0, 0), (1, 0)),
                       ((0, 0), (1, 1)), ((0, 1), (1, 0)),
                       ((319, 478), (319, 479))):
            image = noisy_dark()
            for y, x in points:
                image[y, x] = [0, 0, 90]
            self.assertIsNone(dark_noise_evidence(image))

    def test_disconnected_hot_pixels_do_not_force_lift(self):
        image = noisy_dark()
        image[0, 0] = 255
        image[20, 20] = 200
        self.assertIsNotNone(dark_noise_evidence(image))

    def test_many_disconnected_bright_pixels_are_not_ignored(self):
        image = noisy_dark()
        image[::50, ::50] = 90
        self.assertIsNone(dark_noise_evidence(image))

    def test_strong_colored_light_and_flat_noiseless_fields_remain_eligible(self):
        for color in ([0, 0, 90], [19, 7, 14], [30, 0, 0]):
            image = np.broadcast_to(np.array(color, np.uint8), (320, 480, 3))
            self.assertIsNone(dark_noise_evidence(image))
        bright = noisy_dark().astype(np.float32) + 30
        self.assertIsNone(dark_noise_evidence(bright))

    def test_transposition_and_reflection_preserve_noise_decision(self):
        image = noisy_dark()
        for transformed in (image, image[::-1], image[:, ::-1], image.transpose(1, 0, 2)):
            self.assertIsNotNone(dark_noise_evidence(transformed))

    def test_too_small_and_nonfinite_inputs(self):
        self.assertIsNone(dark_noise_evidence(noisy_dark()[:32]))
        invalid = noisy_dark().astype(np.float32)
        invalid[5, 5] = np.nan
        with self.assertRaises(ValueError):
            dark_noise_evidence(invalid)


if __name__ == '__main__':
    unittest.main()
