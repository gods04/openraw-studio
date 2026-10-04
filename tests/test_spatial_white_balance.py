import unittest
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.decision.ambient_color import AmbientColor
from openraw_studio.decision.auto_adjust import _RenderGuard, suggest_auto_adjustments_for_photo
from openraw_studio.decision.white_balance import NeutralCast, _bounded_color_delta, _regional_strength, _regions_preserved, refine_white_balance
from openraw_studio.raw.native.interactive import InteractivePhoto


class SpatialWhiteBalanceTests(unittest.TestCase):
    @staticmethod
    def evidence():
        return NeutralCast(np.full((80, 120, 3), (130, 120, 124), np.uint8))

    @staticmethod
    def response(values):
        return np.array([.10, .04]) + .5 * np.array([values['warmth'], values['tint']])

    def test_global_measurement_is_unchanged_and_regional_samples_are_bounded(self):
        for height, width in ((80, 120), (127, 89), (960, 1440), (16, 16)):
            original = np.full((height, width, 3), (130, 120, 124), np.uint8)
            original.flags.writeable = False
            evidence = NeutralCast(original)
            pixels = original.reshape(-1, 3).astype(np.float32) / 255
            bias, regions = evidence.measure_with_regions(pixels)
            np.testing.assert_array_equal(bias, evidence.measure(pixels))
            self.assertEqual(regions.shape, (16, 2))
            self.assertLessEqual(len(evidence.indices), 4096)
            positions = np.concatenate(evidence.regions)
            self.assertEqual(len(positions), len(np.unique(positions)))
            self.assertLessEqual(len(positions), 4096)
            for group, actual in zip(evidence.regions, regions):
                samples = pixels[evidence.indices[group]]
                expected = np.median(np.log(samples[:, (0, 2)] / samples[:, 1:2]), axis=0)
                np.testing.assert_array_equal(actual, expected)
            self.assertFalse(original.flags.writeable)

    def test_original_positions_survive_new_neutrals_and_noncontiguous_input(self):
        original = np.full((99, 131, 3), (130, 120, 124), np.uint8)
        original[20:60, 30:60] = (20, 50, 190)
        evidence = NeutralCast(original[::-1, ::-1])
        first = original[::-1, ::-1].reshape(-1, 3).astype(np.float32) / 255
        second = first.copy()
        second[~evidence.mask.ravel()] = (.5, .6, .5)
        saved = second.copy()
        for before, after in zip(evidence.measure_with_regions(first), evidence.measure_with_regions(second)):
            np.testing.assert_array_equal(before, after)
        np.testing.assert_array_equal(second, saved)

    def test_ambient_reference_is_retained_in_every_regional_measurement(self):
        original = np.full((80, 120, 3), (140, 128, 117), np.uint8)
        ambient = AmbientColor((2**-.5, -2**-.5), .8, 16, 1)
        plain, retained = NeutralCast(original), NeutralCast(original, ambient=ambient)
        for gain in ((1, 1, 1), (.97, 1.03, 1.02)):
            pixels = original.reshape(-1, 3).astype(np.float32) / 255 * np.array(gain, np.float32)
            before = plain.measure_with_regions(pixels)
            after = retained.measure_with_regions(pixels)
            for reference, actual in zip(before, after):
                np.testing.assert_array_equal(actual, reference - retained.retained_bias)

    def test_uncertain_evidence_has_no_regional_measurements(self):
        evidence = NeutralCast(np.full((80, 120, 3), 120, np.uint8))
        self.assertEqual(evidence.regions, ())
        self.assertEqual(evidence.measure_with_regions(np.zeros((9600, 3))), (None, None))

    def test_strength_changes_with_measured_spatial_dispersion(self):
        common = np.tile([.12, .05], (16, 1))
        deltas = np.tile([[.06, 0], [-.06, 0], [0, .06], [0, -.06]], (4, 1))
        fractions = [_regional_strength(common + amount * deltas) for amount in (0, .5, 1, 2, 4)]
        self.assertEqual(fractions[0], .75)
        self.assertTrue(all(.25 <= value <= .75 for value in fractions))
        self.assertTrue(all(a > b for a, b in zip(fractions[:3], fractions[1:4])))
        self.assertEqual(fractions[-1], .25)

    def test_coherent_cast_gets_more_correction_than_legacy_half_step(self):
        edits = {'warmth': 0., 'tint': 0., 'exposure': .3}
        before = edits.copy()
        legacy, _ = refine_white_balance(self.evidence(), self.response, edits, lambda _: True)
        regional = lambda values: np.tile(self.response(values), (16, 1))
        validate = Mock(return_value=True)
        result, metrics = refine_white_balance(self.evidence(), self.response, edits, validate,
                                              measure_regions=regional, validation_strengths=(.25, .5, .7))
        self.assertEqual(metrics['white_balance_target_fraction'], .75)
        self.assertEqual(metrics['white_balance_applied_fraction'], .75)
        self.assertLess(np.linalg.norm(self.response(result)), np.linalg.norm(self.response(legacy)))
        self.assertLess(metrics['white_balance_region_error_after'], metrics['white_balance_region_error_before'])
        self.assertLess(metrics['white_balance_region_worst_change'], 0)
        self.assertEqual(result['exposure'], edits['exposure'])
        self.assertEqual(edits, before)
        validate.assert_called_once_with(result)

    def test_uneven_cast_adapts_strength_without_changing_other_controls(self):
        edits = {'warmth': 0., 'tint': 0., 'exposure': 1.2, 'highlights': -.5}
        coherent = lambda values: np.tile(self.response(values), (16, 1))
        deltas = np.tile([[.06, 0], [-.06, 0], [0, .06], [0, -.06]], (4, 1))
        uneven = lambda values: coherent(values) + deltas
        full, _ = refine_white_balance(self.evidence(), self.response, edits, lambda _: True, measure_regions=coherent)
        partial, metrics = refine_white_balance(self.evidence(), self.response, edits, lambda _: True, measure_regions=uneven)
        self.assertEqual(metrics['white_balance_refined'], 1)
        self.assertLess(metrics['white_balance_target_fraction'], .75)
        self.assertLess(abs(partial['warmth']), abs(full['warmth']))
        self.assertEqual(partial['exposure'], edits['exposure'])
        self.assertEqual(partial['highlights'], edits['highlights'])

    def test_rendered_tonal_guard_can_reject_best_color_and_keep_next_candidate(self):
        edits = {'warmth': 0., 'tint': 0.}
        validate = Mock(side_effect=lambda candidate: candidate['warmth'] >= -.12)
        result, metrics = refine_white_balance(
            self.evidence(), self.response, edits, validate,
            measure_regions=lambda values: np.tile(self.response(values), (16, 1)))
        self.assertEqual(result, {'warmth': -.1, 'tint': -.04})
        self.assertEqual(metrics['white_balance_refined'], 1)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(validate.call_args_list[0].args[0]['warmth'], -.15)
        self.assertEqual(validate.call_args_list[1].args[0], result)

    def test_no_color_proposal_can_bypass_the_final_rendered_guard(self):
        edits = {'warmth': 0., 'tint': 0.}
        validate = Mock(return_value=False)
        result, metrics = refine_white_balance(
            self.evidence(), self.response, edits, validate,
            measure_regions=lambda values: np.tile(self.response(values), (16, 1)))
        self.assertIs(result, edits)
        self.assertEqual(metrics['white_balance_refined'], 0)
        self.assertEqual(metrics['white_balance_applied_fraction'], 0)
        self.assertEqual(metrics['white_balance_error_before'], metrics['white_balance_error_after'])
        self.assertGreater(validate.call_count, 1)
        self.assertLessEqual(validate.call_count, 5)

    def test_global_median_improvement_cannot_hide_an_oppositely_lit_region(self):
        edits = {'warmth': 0., 'tint': 0.}
        def regions(values):
            result = np.tile(self.response(values), (16, 1))
            result[-1] -= [.14, .07]
            return result
        validate = Mock(return_value=True)
        result, metrics = refine_white_balance(self.evidence(), self.response, edits, validate, measure_regions=regions)
        self.assertIs(result, edits)
        self.assertEqual(metrics['white_balance_refined'], 0)
        validate.assert_not_called()

    def test_regional_conflict_can_keep_a_smaller_useful_step(self):
        edits = {'warmth': 0., 'tint': 0.}
        def measure(values):
            return np.array([.05, 0.]) + .3 * np.array([values['warmth'], values['tint']])
        def regions(values):
            result = np.tile(measure(values), (16, 1))
            result[-1] -= [.09, 0]
            return result
        result, metrics = refine_white_balance(self.evidence(), measure, edits, lambda _: True, measure_regions=regions)
        self.assertEqual(metrics['white_balance_refined'], 1)
        self.assertGreater(metrics['white_balance_applied_fraction'], 0)
        self.assertLess(metrics['white_balance_applied_fraction'], metrics['white_balance_target_fraction'])
        self.assertTrue(_regions_preserved(regions(edits), regions(result)))

    def test_display_regions_can_veto_primary_proxy_color_benefit(self):
        edits = {'warmth': 0., 'tint': 0.}
        def fine(values):
            result = np.tile(self.response(values), (8, 1))
            result[-1] -= [.14, .07]
            return result
        result, metrics = refine_white_balance(
            self.evidence(), self.response, edits, lambda _: True,
            detail_evidence=self.evidence(), measure_detail=self.response,
            measure_regions=lambda values: np.tile(self.response(values), (16, 1)), measure_detail_regions=fine)
        self.assertIs(result, edits)
        self.assertEqual(metrics['white_balance_refined'], 0)

    def test_regional_benefit_is_checked_at_intermediate_strengths(self):
        edits = {'warmth': 0., 'tint': 0., 'exposure': 1.}
        def regions(values):
            result = np.tile(self.response(values), (16, 1))
            if values['exposure'] == .5 and values['warmth']:
                result[-1] += [.12, 0]
            return result
        _, endpoint = refine_white_balance(self.evidence(), self.response, edits, lambda _: True, measure_regions=regions)
        self.assertEqual(endpoint['white_balance_refined'], 1)
        result, checked = refine_white_balance(self.evidence(), self.response, edits, lambda _: True,
                                              measure_regions=regions, validation_strengths=(.25, .5, .7))
        self.assertIs(result, edits)
        self.assertEqual(checked['white_balance_refined'], 0)

    def test_invalid_or_sparse_regional_evidence_abstains_before_probes(self):
        edits = {'warmth': 0., 'tint': 0.}
        for value in (None, np.zeros((3, 2)), np.zeros(8), np.zeros((8, 3)), np.full((8, 2), np.nan)):
            result, metrics = refine_white_balance(self.evidence(), self.response, edits, lambda _: True,
                                                  measure_regions=lambda _: value)
            self.assertIs(result, edits)
            self.assertEqual(metrics['white_balance_response_probes'], 0)

    def test_invalid_candidate_regions_do_not_pass_preservation(self):
        baseline = np.full((8, 2), .1)
        for before, after in ((None, baseline), (baseline, None), (baseline, baseline[:3]),
                              (baseline, np.full_like(baseline, np.nan)), (baseline[:, 0], baseline),
                              (np.full_like(baseline, np.inf), baseline)):
            self.assertFalse(_regions_preserved(before, after))

    def test_guard_caches_global_and_spatial_measurements_in_one_render(self):
        original = np.full((81, 129, 3), (130, 120, 124), np.uint8)
        candidate = original.copy()
        candidate[:40] = (125, 121, 120)
        saved = candidate.copy()
        render = Mock(return_value=candidate)
        guard = _RenderGuard(original, render, preserve_midtones=True, balance=True)
        result = guard.measure({'warmth': -.1})
        expected = guard.neutral.measure_with_regions(candidate.reshape(-1, 3).astype(np.float32) / 255)
        np.testing.assert_array_equal(result.neutral_bias, expected[0])
        np.testing.assert_array_equal(result.neutral_regions, expected[1])
        self.assertIs(result, guard.measure({'warmth': -.1}))
        render.assert_called_once()
        np.testing.assert_array_equal(candidate, saved)

    def test_joint_bound_fit_beats_independent_clamping(self):
        response = np.array([[.2, -.15], [.12, .3]])
        target = np.array([-.09, -.10])
        lower, upper = np.array([-.35, -.25]), np.array([.35, .25])
        naive = np.clip(np.linalg.solve(response, target), lower, upper)
        actual = _bounded_color_delta(response, target, lower, upper)
        self.assertTrue(np.all((actual >= lower) & (actual <= upper)))
        self.assertLess(np.linalg.norm(response @ actual - target), np.linalg.norm(response @ naive - target))
        grid = np.stack(np.meshgrid(np.linspace(lower[0], upper[0], 181), np.linspace(lower[1], upper[1], 181)), axis=-1).reshape(-1, 2)
        self.assertLessEqual(np.linalg.norm(response @ actual - target), np.min(np.linalg.norm(grid @ response.T - target, axis=1)) + 1e-10)

    def test_unconstrained_solution_and_asymmetric_room_remain_valid(self):
        response = np.array([[.3, -.1], [.1, .4]])
        lower, upper = np.array([-.6, -.1]), np.array([.1, .4])
        for desired in (np.array([.03, .1]), np.array([.4, -.3]), np.array([-.9, .8])):
            target = response @ desired
            actual = _bounded_color_delta(response, target, lower, upper)
            self.assertTrue(np.all((actual >= lower) & (actual <= upper)))
            if np.all((desired >= lower) & (desired <= upper)):
                np.testing.assert_allclose(actual, desired, atol=1e-12)

    def test_real_renderer_handles_common_cast_and_saturated_control_without_regression(self):
        ramp = np.linspace(.05, .22, 96, dtype=np.float32)
        refine = refine_white_balance
        def legacy(*args, **kwargs):
            kwargs.pop('measure_regions', None)
            kwargs.pop('measure_detail_regions', None)
            return refine(*args, **kwargs)
        changes = []
        for gains in ((1.08, .95, 1.02), (1.04, .97, 1.0)):
            pixels = np.broadcast_to(ramp[:, None, None], (96, 128, 3)).copy()
            pixels *= np.array(gains, np.float32)
            original = pixels.copy()
            photo = InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1))
            with (patch('openraw_studio.decision.auto_adjust.analyze_scene', return_value=None),
                  patch('openraw_studio.decision.auto_adjust.analyze_person', return_value=None)):
                with patch('openraw_studio.decision.auto_adjust.refine_white_balance', side_effect=legacy):
                    before = suggest_auto_adjustments_for_photo(photo)
                after = suggest_auto_adjustments_for_photo(photo)
            self.assertEqual(after.metrics['white_balance_refined'], 1)
            old_error, new_error = before.metrics['white_balance_error_after'], after.metrics['white_balance_error_after']
            self.assertLessEqual(new_error, old_error)
            self.assertLessEqual(after.metrics['white_balance_candidates'], 5)
            changes.append(old_error - new_error)
            self.assertEqual(after.metrics['new_highlight_clipping_fraction'], 0)
            self.assertEqual(after.metrics['new_shadow_clipping_fraction'], 0)
            np.testing.assert_array_equal(pixels, original)
        self.assertGreater(max(changes), .004)


if __name__ == '__main__':
    unittest.main()
