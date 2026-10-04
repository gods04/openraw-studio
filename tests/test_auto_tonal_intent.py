import unittest
from unittest.mock import Mock

import numpy as np

from openraw_studio.decision.auto_adjust import _RenderGuard, suggest_auto_adjustments_from_preview
from openraw_studio.decision.tonal_intent import dark_scene_intent, limit_dark_lift, little_visible_detail
from openraw_studio.raw.native.interactive import InteractivePhoto
from openraw_studio.ui.desktop import _auto_adjust_status
from openraw_studio.vision.scene import SceneEvidence


def evidence(light='Night', reliability=1):
    return SceneEvidence('ready', 'Sky', light, reliability, {'Sky':1}, {light:1})


class AutoTonalIntentTests(unittest.TestCase):
    def test_dark_reference_depends_on_observed_tones_not_just_scene_label(self):
        dim = dark_scene_intent(np.linspace(.07,.13,100), evidence())
        lighter = dark_scene_intent(np.linspace(.17,.23,100), evidence())
        wide = dark_scene_intent(np.r_[np.full(50,.02),np.full(50,.38)], evidence())
        self.assertLess(dim.median_ceiling, lighter.median_ceiling)
        self.assertGreater(wide.median_ceiling, lighter.median_ceiling)
        self.assertGreater(dim.lift, 0)
        self.assertLess(dim.lift, .04)

    def test_uncertainty_relaxes_limit_and_bright_or_daylight_scenes_do_not_activate_it(self):
        luma = np.linspace(.1,.2,100)
        strong = dark_scene_intent(luma, evidence())
        weak = dark_scene_intent(luma, evidence(reliability=.2))
        self.assertGreater(weak.median_ceiling, strong.median_ceiling)
        for item in (None, SceneEvidence('uncertain'), evidence('Daylight'), evidence('Sunset')):
            self.assertIsNone(dark_scene_intent(luma, item))
        self.assertIsNone(dark_scene_intent(np.full(100,.6), evidence()))

    def test_near_black_noise_abstains_without_claiming_a_scene(self):
        image = np.random.default_rng(41).integers(0,5,(96,128,3),dtype=np.uint8)
        image[5,5] = 200
        saved = image.copy()
        self.assertTrue(little_visible_detail(image))
        render = Mock(side_effect=AssertionError('Unnecessary render'))
        result = suggest_auto_adjustments_from_preview(image, render=render, scene_evidence=evidence())
        self.assertEqual(set(result.as_overrides().values()), {0})
        self.assertEqual(result.metrics['insufficient_tonal_information'], 1)
        self.assertEqual(result.scene_evidence.status, 'insufficient-information')
        self.assertEqual(_auto_adjust_status(result), 'Auto unchanged: little visible detail')
        render.assert_not_called()
        np.testing.assert_array_equal(image, saved)

    def test_weak_nonlimiting_evidence_retains_balanced_exposure_deadband(self):
        pixels = np.full((100,100,3),107,np.uint8)
        weak = SceneEvidence('ready','Interior','Mixed light',.2,{'Interior':1},{'Night':.401,'Indoor light':.599})
        baseline = suggest_auto_adjustments_from_preview(pixels)
        result = suggest_auto_adjustments_from_preview(pixels, scene_evidence=weak)
        self.assertEqual(baseline.exposure,0)
        self.assertEqual(result.as_overrides(),baseline.as_overrides())

    def test_faint_spatial_structure_and_sparse_bright_subject_are_not_empty(self):
        gradient = np.broadcast_to(np.linspace(0,7,128,dtype=np.float32)[None,:,None],(96,128,3))
        self.assertFalse(little_visible_detail(gradient))
        sparse = np.full((96,128,3),2,np.uint8)
        sparse[20:24,50:54] = 200
        self.assertFalse(little_visible_detail(sparse))
        self.assertNotIn('insufficient_tonal_information', suggest_auto_adjustments_from_preview(sparse).metrics)

    def test_saturated_blue_signal_is_not_rejected_by_low_luma(self):
        blue = np.broadcast_to(np.array([0,0,90],np.uint8),(96,128,3))
        self.assertFalse(little_visible_detail(blue))
        result = suggest_auto_adjustments_from_preview(blue, scene_evidence=evidence('Colored light'))
        self.assertNotIn('insufficient_tonal_information', result.metrics)

    def test_detail_can_reveal_information_missing_from_small_preview(self):
        small = np.full((20,20,3),2,np.uint8)
        detail = np.broadcast_to(np.linspace(0,7,128,dtype=np.float32)[None,:,None],(96,128,3)).copy()
        result = suggest_auto_adjustments_from_preview(small, render=lambda _:small,
            detail_preview=detail, render_detail=lambda _:detail, scene_evidence=evidence())
        self.assertNotIn('insufficient_tonal_information', result.metrics)

    def test_relative_target_below_old_metering_floor_cannot_reverse_lift_direction(self):
        pixels = np.full((100,100,3),2,np.uint8)
        pixels[10:20,10:20] = 160
        result = suggest_auto_adjustments_from_preview(pixels, scene_evidence=evidence())
        self.assertGreater(result.exposure,0)
        self.assertLessEqual(result.exposure,.35)

    def test_fit_preserves_unrelated_parameters_and_bounds_work(self):
        values = dict(exposure=.8, shadows=.4, contrast=-.08, warmth=.12)
        original = values.copy()
        checked = Mock(side_effect=lambda v:v['exposure']+v['shadows']<=.43)
        result = limit_dark_lift(values, checked)
        self.assertLessEqual(result['exposure']+result['shadows'], .43)
        self.assertGreater(result['exposure']+result['shadows'], .39)
        self.assertEqual(result['contrast'], original['contrast'])
        self.assertEqual(result['warmth'], original['warmth'])
        self.assertEqual(values, original)
        self.assertLessEqual(checked.call_count, 7)
        self.assertIs(limit_dark_lift(values, lambda _:False), values)
        self.assertIs(limit_dark_lift(values, lambda _:True), values)

    def test_real_renderer_limits_night_lift_but_keeps_daylight_path(self):
        outputs = []
        for level in (.008,.018):
            linear = np.broadcast_to(np.linspace(level,level*2,128,dtype=np.float32)[None,:,None],(96,128,3)).copy()
            photo = InteractivePhoto(linear, np.eye(3,dtype=np.float32),(1,1,1))
            render = lambda values:photo.render(values)[0]
            original = render({})
            baseline = suggest_auto_adjustments_from_preview(original, render=render)
            day = suggest_auto_adjustments_from_preview(original, render=render, scene_evidence=evidence('Daylight'))
            night = suggest_auto_adjustments_from_preview(original, render=render, scene_evidence=evidence(),
                validation_strengths=(.25,.5,.7))
            self.assertEqual(day.as_overrides(), baseline.as_overrides())
            self.assertLess(night.exposure, day.exposure)
            self.assertGreater(night.metrics['median_luma_after'], night.metrics['median_luma'])
            self.assertLessEqual(night.metrics['median_luma_after'], night.metrics['dark_scene_median_ceiling'])
            outputs.append(night.as_overrides())
        self.assertNotEqual(outputs[0],outputs[1])

    def test_display_domain_enforces_its_own_limit_while_biased_native_samples_do_not(self):
        baseline = np.full((20,20,3),40,np.uint8)
        lifted = np.full_like(baseline,65)
        plain = _RenderGuard(baseline, lambda _:lifted, preserve_midtones=True)
        guarded = _RenderGuard(baseline, lambda _:lifted, preserve_midtones=True, lighting=evidence())
        self.assertTrue(plain.tones_preserved({}))
        self.assertFalse(guarded.tones_preserved({}))
        result = suggest_auto_adjustments_from_preview(baseline, render=lambda _:baseline,
            native_preview=baseline, render_native=lambda _:lifted, scene_evidence=evidence())
        self.assertNotIn('native_dark_scene_median_ceiling', result.metrics)

    def test_intermediate_strength_must_respect_dark_reference(self):
        baseline = np.full((20,20,3),40,np.uint8)
        def render(values):
            return np.full_like(baseline,60 if .02<values['exposure']<.15 else 40)
        result = suggest_auto_adjustments_from_preview(baseline, render=render, scene_evidence=evidence(),
            validation_strengths=(.25,.5,.7))
        guard = _RenderGuard(baseline, render, preserve_midtones=True, lighting=evidence())
        for strength in (.25,.5,.7,1):
            self.assertTrue(guard.intent_preserved({k:v*strength for k,v in result.as_overrides().items()}))

    def test_finer_display_can_veto_a_lift_that_passes_small_proxy(self):
        baseline = np.full((20,20,3),40,np.uint8)
        def detail(values):
            return np.full_like(baseline,40+round(80*max(values['exposure'],values['shadows'])))
        result = suggest_auto_adjustments_from_preview(baseline, render=lambda _:baseline,
            detail_preview=baseline, render_detail=detail, scene_evidence=evidence(),
            validation_strengths=(.25,.5,.7))
        self.assertGreater(result.exposure,0)
        self.assertLess(result.exposure,.10)
        self.assertLessEqual(result.metrics['detail_median_luma_after'],result.metrics['detail_dark_scene_median_ceiling'])

    def test_nonfinite_information_input_is_rejected(self):
        with self.assertRaises(ValueError):
            little_visible_detail(np.full((20,20,3),np.nan))
