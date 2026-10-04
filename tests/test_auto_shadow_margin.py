import unittest
from unittest.mock import Mock

import numpy as np

from openraw_studio.decision.auto_adjust import _limit_contrast, _RenderGuard, suggest_auto_adjustments_from_preview


class AutoShadowMarginTests(unittest.TestCase):
    def test_near_black_pixels_count_before_they_cross_hard_black_threshold(self):
        original = np.full((1000, 3), 120, np.uint8)
        original[:8] = 20
        candidate = original.copy()
        candidate[:4] = 0
        candidate[4:8] = 3
        legacy = _RenderGuard(original, lambda _: candidate, preserve_midtones=False)
        protected = _RenderGuard(original, lambda _: candidate, preserve_midtones=False, shadow_margin=True)
        self.assertTrue(legacy.tones_preserved({}))
        self.assertFalse(protected.tones_preserved({}))
        self.assertAlmostEqual(protected.measure({}).crushed_shadows, .004)
        self.assertAlmostEqual(protected.measure({}).shadow_risk, .006)
        self.assertNotIn('shadow_clipping_risk', legacy.metrics({}))
        self.assertAlmostEqual(protected.metrics({})['shadow_clipping_risk'], .006)

    def test_margin_is_continuous_and_does_not_count_existing_black_pixels(self):
        original = np.full((1000, 3), 120, np.uint8)
        original[:20] = 20
        original[20:400] = 0
        risks = []
        for gray in (0, 1, 2, 3, 4, 5):
            candidate = original.copy()
            candidate[:400] = gray
            callback = Mock(return_value=candidate)
            guard = _RenderGuard(original, callback, preserve_midtones=False, shadow_margin=True)
            saved = candidate.copy()
            metrics = guard.measure({})
            self.assertIs(metrics, guard.measure({}))
            callback.assert_called_once()
            np.testing.assert_array_equal(candidate, saved)
            risks.append(metrics.shadow_risk)
        np.testing.assert_allclose(risks, [.02,.02,.02,.01,0,0], atol=1e-8)

    def test_margin_does_not_penalize_unchanged_shadows_or_intentional_dark_background(self):
        original = np.full((100, 100, 3), 2, np.uint8)
        original[10:20] = 12
        original[20:30] = 50
        original[30:40] = 200
        guard = _RenderGuard(original, lambda _:original, preserve_midtones=False, shadow_margin=True)
        self.assertTrue(guard.tones_preserved({}))
        self.assertEqual(guard.measure({}).shadow_risk, 0)

    def test_contrast_fit_adapts_to_measured_limit_and_bounds_work(self):
        for maximum in (0, .013, .067, .117, .18):
            values = {'contrast':.18, 'exposure':.6, 'warmth':.1}
            original = values.copy()
            validate = Mock(side_effect=lambda v:v['contrast']<=maximum)
            result = _limit_contrast(values, validate)
            self.assertLessEqual(result['contrast'], maximum)
            self.assertGreaterEqual(result['contrast'], maximum - .18 / 32 - .0001)
            self.assertLessEqual(validate.call_count, 7)
            self.assertEqual(result['exposure'], .6)
            self.assertEqual(result['warmth'], .1)
            self.assertEqual(values, original)

    def test_already_safe_or_nonpositive_contrast_does_not_add_probe_work(self):
        for contrast in (-.1, 0, .1):
            values = {'contrast':contrast}
            validate = Mock(return_value=True)
            self.assertIs(_limit_contrast(values, validate), values)
            self.assertEqual(validate.call_count, int(contrast>0))

    def test_no_acceptable_zero_requires_whole_correction_backoff(self):
        validate = Mock(return_value=False)
        values = {'contrast':.1,'exposure':.6}
        self.assertEqual(_limit_contrast(values, validate), {'contrast':0,'exposure':.6})
        self.assertEqual(validate.call_count, 2)

    def test_nonmonotonic_response_only_returns_an_actually_checked_candidate(self):
        checked = []
        def validate(values):
            contrast = values['contrast']
            valid = contrast == 0 or .02 <= contrast <= .07
            checked.append((contrast, valid))
            return valid
        result = _limit_contrast({'contrast':.18}, validate)
        self.assertIn((result['contrast'], True), checked)
        self.assertLessEqual(result['contrast'], .07)

    def test_native_and_detail_margins_catch_loss_missing_from_small_proxy(self):
        primary = np.full((20, 30, 3), 100, np.uint8)
        baseline = np.full((100, 100, 3), 100, np.uint8)
        baseline[:2] = 20
        def render(values):
            result = baseline.copy()
            if values['contrast'] > .12:
                result[:2] = 3
            return result
        for domain in ('native', 'detail'):
            result = suggest_auto_adjustments_from_preview(
                primary, render=lambda _:primary,
                **{f'{domain}_preview':baseline, f'render_{domain}':render},
            )
            self.assertGreater(result.contrast, .11)
            self.assertLessEqual(result.contrast, .12)
            self.assertEqual(result.metrics['contrast_guarded'], 1)
            self.assertEqual(result.metrics[f'{domain}_new_shadow_clipping_fraction'], 0)
            self.assertEqual(result.metrics[f'{domain}_shadow_clipping_risk'], 0)

    def test_sparse_shadow_risk_is_checked_at_intermediate_strengths(self):
        baseline = np.full((100, 100, 3), 150, np.uint8)
        baseline[:2] = 20
        def render(values):
            result = baseline.copy()
            if .02 < values['contrast'] < .15:
                result[:2] = 3
            return result
        endpoint = suggest_auto_adjustments_from_preview(baseline, render=render)
        self.assertEqual(endpoint.contrast, .18)
        self.assertEqual(endpoint.highlights, 0)
        protected = suggest_auto_adjustments_from_preview(
            baseline, render=render, validation_strengths=(.25,.5,.7),
        )
        self.assertEqual(protected.contrast, 0)
        for strength in (.25,.5,.7,1):
            edits = {k:v*strength for k,v in protected.as_overrides().items()}
            self.assertTrue(np.all(render(edits)[:2] == 20))
