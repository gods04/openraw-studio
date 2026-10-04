import unittest
from unittest.mock import Mock

import numpy as np

from openraw_studio.decision.auto_adjust import _fit_exposure, suggest_auto_adjustments_from_preview


class ExposureFitTests(unittest.TestCase):
    def test_checked_interval_fits_different_boundaries_and_preserves_other_controls(self):
        original = {'exposure': .2, 'contrast': .03, 'warmth': .08}
        for boundary in (.37, .91, 1.43):
            with self.subTest(boundary=boundary):
                checked = Mock(side_effect=lambda v: v['exposure'] <= boundary)
                result = _fit_exposure(original, 2., checked)
                self.assertLessEqual(result['exposure'], boundary)
                self.assertGreaterEqual(result['exposure'], boundary - 1.8 / 32 - .0001)
                self.assertEqual(result['contrast'], original['contrast'])
                self.assertEqual(result['warmth'], original['warmth'])
                self.assertLessEqual(checked.call_count, 7)
                self.assertIn(result, [call.args[0] for call in checked.call_args_list])
        self.assertEqual(original, {'exposure': .2, 'contrast': .03, 'warmth': .08})

    def test_accepted_upper_bound_needs_one_probe(self):
        checked = Mock(return_value=True)
        self.assertEqual(_fit_exposure({'exposure': .4}, 1.234567, checked), {'exposure': 1.2346})
        checked.assert_called_once()

    def test_negative_interval_still_returns_a_checked_exposure(self):
        checked = Mock(side_effect=lambda values: values['exposure'] <= -.37)
        result = _fit_exposure({'exposure': -.8, 'warmth': .1}, -.1, checked)
        self.assertLessEqual(result['exposure'], -.37)
        self.assertGreater(result['exposure'], -.4)
        self.assertEqual(result['warmth'], .1)
        self.assertIn(result, [call.args[0] for call in checked.call_args_list])

    def test_reversed_or_empty_interval_never_renders(self):
        checked = Mock(side_effect=AssertionError('Unexpected render'))
        for upper in (.1, .4):
            self.assertEqual(_fit_exposure({'exposure': .4}, upper, checked), {'exposure': .4})
        checked.assert_not_called()

    def test_invalid_endpoints_return_the_original_without_inventing_a_safe_midpoint(self):
        values = {'exposure': .4, 'highlights': -.5}
        checked = Mock(return_value=False)
        self.assertIs(_fit_exposure(values, 2, checked), values)
        self.assertEqual(checked.call_count, 2)

    def test_no_renderer_retains_conservative_estimate(self):
        original = np.full((50, 50, 3), 60, np.uint8)
        self.assertEqual(suggest_auto_adjustments_from_preview(original).exposure, 1.2)

    def test_rendered_response_sets_exposure_beyond_old_cap_within_desktop_range(self):
        original = np.full((50, 50, 3), 60, np.uint8)
        saved = original.copy()
        results = []
        for response in (30, 40):
            with self.subTest(response=response):
                def render(values):
                    return np.full_like(original, min(255, 60 + round(response * values['exposure'])))

                result = suggest_auto_adjustments_from_preview(original, render=render)
                self.assertGreater(result.exposure, 1.2)
                self.assertLessEqual(result.exposure, 2)
                self.assertEqual(result.metrics['exposure_refined'], 1)
                self.assertEqual(result.metrics['validated_strength_samples'], 100)
                self.assertLessEqual(result.metrics['median_luma_after'], .44 + 1 / 255)
                self.assertGreater(result.metrics['median_luma_after'], .42)
                for percent in range(1, 101):
                    image = render({key: value * percent / 100 for key, value in result.as_overrides().items()})
                    self.assertLessEqual(float(np.median(image)), .44 * 255 + 1)
                results.append(result.exposure)
        self.assertGreater(results[0], results[1])
        np.testing.assert_array_equal(original, saved)

    def test_finer_resolution_can_limit_the_extension(self):
        original = np.full((50, 50, 3), 60, np.uint8)

        def render(values, response):
            return np.full_like(original, min(255, 60 + round(response * values['exposure'])))

        result = suggest_auto_adjustments_from_preview(
            original, render=lambda values: render(values, 30),
            detail_preview=original, render_detail=lambda values: render(values, 40),
        )
        self.assertGreater(result.exposure, 1.2)
        self.assertLessEqual(result.exposure, 1.34)
        self.assertLessEqual(result.metrics['detail_median_luma_after'], .44 + 1 / 255)

    def test_flat_response_does_not_spend_detail_renders_on_useless_extension(self):
        original = np.full((50, 50, 3), 60, np.uint8)
        detail = Mock(return_value=original)
        result = suggest_auto_adjustments_from_preview(
            original, render=lambda _: original, detail_preview=original, render_detail=detail,
        )
        self.assertEqual(result.exposure, 1.2)
        self.assertEqual(result.metrics['exposure_refined'], 0)
        self.assertLessEqual(detail.call_count, 2)


if __name__ == '__main__':
    unittest.main()
