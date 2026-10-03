import unittest

import numpy as np

from openraw_studio.decision.auto_adjust import (
    suggest_auto_adjustments_for_photo,
    suggest_auto_adjustments_from_preview,
)
from openraw_studio.raw.native.interactive import InteractivePhoto


class AutoShadowMidtoneTests(unittest.TestCase):
    @staticmethod
    def scene(*, fraction=.3, darkening=100, background=120):
        pixels = np.full((100, 100, 3), background, np.uint8)
        rows = round(fraction * 100)
        pixels[:rows] = 50

        def render(values):
            candidate = pixels.copy()
            candidate[rows:] = min(255, background + 5)
            candidate[:rows] = 50 - round(darkening * values["contrast"])
            return candidate

        return pixels, render

    def test_brightening_majority_cannot_hide_darkened_shadow_subject(self):
        pixels, render = self.scene()
        original = pixels.copy()
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.contrast, 0)
        self.assertEqual(result.metrics["contrast_guarded"], 1)
        self.assertEqual(result.metrics["new_shadow_clipping_fraction"], 0)
        self.assertGreater(result.metrics["median_luma_after"], result.metrics["median_luma"])
        self.assertGreaterEqual(result.metrics["shadow_midtone_mean_after"], result.metrics["shadow_midtone_mean_before"])
        np.testing.assert_array_equal(original, pixels)

    def test_mild_safe_contrast_is_retained_instead_of_disabling_it(self):
        pixels, render = self.scene(darkening=24)
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.contrast, .09)
        self.assertEqual(result.metrics["contrast_guarded"], 1)
        self.assertGreaterEqual(result.metrics["shadow_midtone_mean_after"], 48 / 255 - 1e-6)

    def test_detail_validation_also_protects_shadow_midtones(self):
        detail, render_detail = self.scene()
        primary = np.full((4, 4, 3), 110, np.uint8)
        result = suggest_auto_adjustments_from_preview(
            primary, render=lambda _: primary, detail_preview=detail, render_detail=render_detail,
        )
        self.assertEqual(result.contrast, 0)
        self.assertEqual(result.metrics["contrast_guarded"], 1)
        self.assertAlmostEqual(result.metrics["detail_shadow_midtone_fraction"], .3)

    def test_brightness_biased_native_samples_do_not_set_shadow_target(self):
        primary = np.full((4, 4, 3), 110, np.uint8)
        native, render_native = self.scene()
        base = suggest_auto_adjustments_from_preview(primary, render=lambda _: primary)
        result = suggest_auto_adjustments_from_preview(
            primary, render=lambda _: primary, native_preview=native, render_native=render_native,
        )
        self.assertEqual(result.as_overrides(), base.as_overrides())
        self.assertLess(result.metrics["native_shadow_midtone_mean_after"], result.metrics["native_shadow_midtone_mean_before"])

    def test_sparse_shadows_do_not_veto_scene_contrast(self):
        pixels, render = self.scene(fraction=.09)
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.contrast, .18)
        self.assertEqual(result.metrics["contrast_guarded"], 0)

    def test_intermediate_contrast_checks_do_not_require_highlight_compression(self):
        pixels, _ = self.scene()

        def render(values):
            candidate = pixels.copy()
            if .02 < values["contrast"] < .15:
                candidate[:30] = 30
            return candidate

        endpoint = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(endpoint.contrast, .18)
        self.assertEqual(endpoint.highlights, 0)
        result = suggest_auto_adjustments_from_preview(pixels, render=render, validation_strengths=(.25, .5, .7))
        self.assertEqual(result.contrast, 0)
        for strength in (.25, .5, .7, 1):
            edits = {key: value * strength for key, value in result.as_overrides().items()}
            self.assertTrue(np.all(render(edits)[:30] == 50))

    def test_intentional_negative_exposure_can_darken_shadow_midtones(self):
        pixels, _ = self.scene(background=180)

        def darker(_):
            return pixels - np.uint8(8)

        result = suggest_auto_adjustments_from_preview(pixels, render=darker)
        self.assertLess(result.exposure, 0)
        self.assertEqual(result.metrics["guard_strength"], 1)
        self.assertLess(result.metrics["shadow_midtone_mean_after"], result.metrics["shadow_midtone_mean_before"])

    def test_real_linear_scene_retains_dark_midtones_at_multiple_strengths(self):
        pixels = np.full((120, 160, 3), .17, np.float32)
        pixels[:40] = np.linspace(.012, .045, 40)[:, None, None]
        photo = InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1))
        original = np.asarray(photo.render({})[0], dtype=np.float32)
        baseline = original[:40].mean()
        result = suggest_auto_adjustments_for_photo(photo)
        self.assertEqual(result.metrics["contrast_guarded"], 1)
        for strength in (.25, .5, .7, 1):
            edits = {key: value * strength for key, value in result.as_overrides().items()}
            after = np.asarray(photo.render(edits)[0], dtype=np.float32)
            with self.subTest(strength=strength):
                self.assertGreaterEqual(after[:40].mean(), baseline - 2.55)


if __name__ == "__main__":
    unittest.main()
