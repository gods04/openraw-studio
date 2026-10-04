import unittest
from unittest.mock import Mock

import numpy as np

from openraw_studio.decision.ambient_color import AmbientColor, analyze_ambient_color
from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_from_preview
from openraw_studio.decision.white_balance import NeutralCast
from openraw_studio.raw.native.interactive import InteractivePhoto
from openraw_studio.raw.native.tone import PreviewRgbImage
from openraw_studio.vision.scene import SceneEvidence


class AmbientColorTests(unittest.TestCase):
    @staticmethod
    def evidence(light='Sunset', *, reliability=.8, status='ready'):
        return SceneEvidence(status, 'Mountains', light, reliability,
                             {'Mountains': 1.0}, {light: 1.0})

    @staticmethod
    def warm():
        return np.full((80, 120, 3), (140, 128, 117), np.uint8)

    def test_scene_vote_requires_measured_color_and_spatial_coverage(self):
        gray = np.full_like(self.warm(), 128)
        small = gray.copy()
        small[20:35, 20:35] = (140, 128, 117)
        corner = gray.copy()
        corner[:40, :60] = (140, 128, 117)
        for image in (gray, small, corner, gray[:8], gray.reshape(-1, 3)):
            with self.subTest(shape=image.shape):
                self.assertIsNone(analyze_ambient_color(image, self.evidence()))
        result = analyze_ambient_color(self.warm(), self.evidence())
        self.assertEqual(result.tiles, 16)
        self.assertEqual(result.fraction, 1)
        self.assertEqual(result.weight, 1)

    def test_missing_uncertain_daylight_or_weak_semantics_abstain(self):
        for evidence in (None, self.evidence(status='missing'),
                         self.evidence(status='uncertain'), self.evidence('Daylight'),
                         self.evidence(reliability=0)):
            self.assertIsNone(analyze_ambient_color(self.warm(), evidence))

    def test_semantic_reliability_scales_retention_continuously(self):
        strong = analyze_ambient_color(self.warm(), self.evidence(reliability=.5))
        weak = analyze_ambient_color(self.warm(), self.evidence(reliability=.2))
        self.assertAlmostEqual(weak.weight, strong.weight * .4)

    def test_person_color_is_not_background_illumination(self):
        image = np.full_like(self.warm(), 128)
        image[:, 30:90] = (140, 128, 117)
        self.assertIsNotNone(analyze_ambient_color(image, self.evidence()))
        mask = np.zeros(image.shape[:2], bool)
        mask[:, 30:90] = True
        person = Mock()
        person.core_mask.return_value = mask
        self.assertIsNone(analyze_ambient_color(image, self.evidence(), person))
        person.core_mask.assert_called_once_with(image.shape[:2])

    def test_night_and_aquarium_use_measured_direction_not_a_fixed_warm_preset(self):
        lights = (self.evidence('Night'), self.evidence('Colored light'),
                  SceneEvidence('ready', 'Aquarium', 'Daylight', .8,
                                {'Aquarium': 1}, {'Daylight': 1}))
        for evidence in lights:
            a = analyze_ambient_color(self.warm(), evidence)
            b = analyze_ambient_color(self.warm()[..., ::-1], evidence)
            self.assertGreater(a.direction[0], .5)
            self.assertLess(b.direction[0], -.5)
            self.assertLess(np.dot(a.direction, b.direction), -.9)

    def test_spatially_opposed_lighting_abstains(self):
        image = self.warm()
        image[:, :60] = image[:, :60, ::-1]
        self.assertIsNone(analyze_ambient_color(image, self.evidence('Night')))

    def test_sunset_does_not_reinterpret_cool_pixels_as_warm_light(self):
        self.assertIsNone(analyze_ambient_color(self.warm()[..., ::-1], self.evidence()))

    def test_clipped_dark_and_nonfinite_pixels_are_not_color_evidence(self):
        for color in ((255, 230, 215), (18, 15, 13), (np.nan, 128, 117)):
            image = np.full((80, 120, 3), color, np.float32)
            self.assertIsNone(analyze_ambient_color(image, self.evidence()))

    def test_flat_preview_format_matches_spatial_array_without_mutation(self):
        image = self.warm()
        original = image.copy()
        flat = PreviewRgbImage(120, 80, tuple(map(tuple, image.reshape(-1, 3))), 'gamma-2.2')
        self.assertEqual(analyze_ambient_color(flat, self.evidence()),
                         analyze_ambient_color(image, self.evidence()))
        np.testing.assert_array_equal(image, original)

    def test_only_positive_projection_is_retained_leaving_orthogonal_cast(self):
        direction = np.array([1, -1], np.float32) / np.sqrt(2)
        ambient = AmbientColor(tuple(direction), .5, 16, 1)
        np.testing.assert_allclose(ambient.retain([.2, 0]), [.05, -.05], atol=1e-7)
        np.testing.assert_array_equal(ambient.retain([-.2, .2]), [0, 0])
        np.testing.assert_array_equal(ambient.retain([.2, .2]), [0, 0])

    def test_neutral_target_is_fixed_to_original_and_sample_count_is_bounded(self):
        image = np.tile(self.warm(), (6, 6, 1))
        ambient = analyze_ambient_color(image, self.evidence())
        legacy, retained = NeutralCast(image), NeutralCast(image, ambient=ambient)
        np.testing.assert_array_equal(legacy.indices, retained.indices)
        self.assertLessEqual(len(retained.indices), 4096)
        original = image.reshape(-1, 3) / 255
        target = ambient.retain(legacy.measure(original))
        for gains in ((1, 1, 1), (.95, 1.05, 1.03), (1.04, .98, .96)):
            candidate = original * gains
            np.testing.assert_allclose(retained.measure(candidate),
                                       legacy.measure(candidate) - target, atol=1e-7)
        np.testing.assert_allclose(retained.retained_bias, target, atol=1e-7)

    def test_no_context_preserves_legacy_suggestions_and_metrics(self):
        image = self.warm()
        before = suggest_auto_adjustments_from_preview(image)
        for evidence in (self.evidence('Daylight'), self.evidence(status='uncertain'),
                         self.evidence(status='missing')):
            after = suggest_auto_adjustments_from_preview(image, scene_evidence=evidence)
            self.assertEqual(after.as_overrides(), before.as_overrides())
            self.assertEqual(after.metrics, before.metrics)

    def test_renderer_keeps_warm_axis_while_still_correcting_magenta(self):
        ramp = np.linspace(.06, .24, 80, dtype=np.float32)
        pixels = np.broadcast_to(ramp[:, None, None], (80, 120, 3)).copy()
        pixels *= np.array([1.3, 1, .9], np.float32)
        photo = InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1))
        original = photo.render({})[0]
        render = lambda values: photo.render(values)[0]
        day = suggest_auto_adjustments_from_preview(original, render=render)
        sunset = suggest_auto_adjustments_from_preview(
            original, render=render, scene_evidence=self.evidence(),
            native_preview=original, render_native=render,
            detail_preview=original, render_detail=render, validation_strengths=(.25, .5, .7),
        )
        measure = NeutralCast(original)
        bias = lambda values: measure.measure(np.asarray(render(values)).reshape(-1, 3) / 255)
        warm = np.array([1, -1]) / np.sqrt(2)
        magenta = np.array([1, 1]) / np.sqrt(2)
        before = bias({})
        for strength in (.25, .5, .7, 1):
            after = bias({k: v * strength for k, v in sunset.as_overrides().items()})
            daylight = bias({k: v * strength for k, v in day.as_overrides().items()})
            self.assertGreater(np.dot(after, warm), np.dot(daylight, warm))
            self.assertLess(abs(np.dot(after, magenta)), abs(np.dot(before, magenta)))
        self.assertEqual(sunset.metrics['white_balance_refined'], 1)
        self.assertEqual(sunset.metrics['new_highlight_clipping_fraction'], 0)
        self.assertEqual(sunset.metrics['native_new_highlight_clipping_fraction'], 0)
        self.assertEqual(sunset.metrics['detail_new_highlight_clipping_fraction'], 0)
        self.assertIn('ambient color', ' '.join(sunset.rationale))
        np.testing.assert_array_equal(photo.pixels, pixels)
