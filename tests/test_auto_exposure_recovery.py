import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_for_photo, suggest_auto_adjustments_from_preview
from openraw_studio.raw.native.interactive import InteractivePhoto


def recovery_scene(*, lift=30, clip=False, intermediate_clip=False, level=70):
    original = np.full((100, 100, 3), level, np.uint8)
    original[:20] = 240

    def render(values):
        result = original.copy()
        result[20:] = np.clip(level + round(lift * values["exposure"]), 0, 255)
        if (
            values["exposure"] + values["highlights"] > .12
            or (clip and values["exposure"] > .2)
            or (intermediate_clip and -.28 < values["highlights"] < -.10 and values["exposure"] > .10)
        ):
            result[:20] = 255
        return result

    return original, render


class ExposureRecoveryTests(unittest.TestCase):
    def suggest(self, original, render, **kwargs):
        return suggest_auto_adjustments_from_preview(
            original, render=render, validation_strengths=(.7, .5, .25), **kwargs,
        )

    def test_revisits_upper_tail_exposure_limit_with_measured_highlight_headroom(self):
        original, render = recovery_scene()
        saved = original.copy()
        initial = suggest_auto_adjustments_from_preview(original)
        result = self.suggest(original, render, detail_preview=original, render_detail=render)
        self.assertEqual(result.metrics["exposure_refined"], 1)
        self.assertGreater(result.exposure, initial.exposure + .15)
        self.assertLessEqual(result.exposure, initial.exposure + .4)
        self.assertEqual(result.highlights, -.5)
        self.assertEqual(result.metrics["new_highlight_clipping_fraction"], 0)
        self.assertEqual(result.metrics["detail_new_highlight_clipping_fraction"], 0)
        self.assertEqual(result.metrics["validated_strength_samples"], 8)
        self.assertIn("Recovered midtones", " ".join(result.rationale))
        np.testing.assert_array_equal(original, saved)

    def test_rechecks_primary_detail_and_native_highlights(self):
        original, render = recovery_scene()
        _, unsafe = recovery_scene(clip=True)
        for domain in ("primary", "detail", "native"):
            with self.subTest(domain=domain):
                kwargs = {}
                if domain != "primary":
                    kwargs.update({f"{domain}_preview": original, f"render_{domain}": unsafe})
                result = self.suggest(original, unsafe if domain == "primary" else render, **kwargs)
                self.assertEqual(result.metrics["exposure_refined"], 0)
                self.assertLessEqual(result.exposure, .2)

    def test_intermediate_strength_cannot_escape_recovery_checks(self):
        original, unsafe = recovery_scene(intermediate_clip=True)
        endpoint = suggest_auto_adjustments_from_preview(original, render=unsafe)
        self.assertEqual(endpoint.metrics["exposure_refined"], 1)
        result = self.suggest(original, unsafe)
        self.assertEqual(result.metrics["exposure_refined"], 0)
        for strength in (.25, .5, .7, 1):
            self.assertLess(unsafe({key: value * strength for key, value in result.as_overrides().items()})[:20].max(), 254)

    def test_requires_measurable_gain_on_both_display_resolutions(self):
        original, render = recovery_scene()
        _, unchanged = recovery_scene(lift=0)
        for primary, detail in ((unchanged, render), (render, unchanged)):
            with self.subTest(primary=primary):
                result = self.suggest(original, primary, detail_preview=original, render_detail=detail)
                self.assertEqual(result.metrics["exposure_refined"], 0)

    def test_recovery_also_checks_low_strength_native_shoulder_peak(self):
        original, render = recovery_scene()

        for low, high in ((.08, .12), (.13, .17), (.18, .22), (.33, .37)):
            with self.subTest(peak=(low, high)):
                def native(values):
                    result = render(values)
                    # This fixture retains contrast, so it identifies strength
                    # independently of the exposure/highlight candidate pair.
                    strength = values["contrast"] / .03
                    if low < strength < high and values["exposure"] > .1 * strength:
                        result[:20] = 255
                    return result

                result = self.suggest(original, render, native_preview=original, render_native=native)
                self.assertEqual(result.metrics["exposure_refined"], 0)

    def test_photo_entry_protects_old_candidate_even_without_exposure_recovery(self):
        original, base_render = recovery_scene(lift=0)

        def render(values):
            result = base_render(values)
            if -.06 < values["highlights"] < -.04 and values["exposure"] > -.15 * values["highlights"]:
                result[:20] = 255
            return result

        class Photo:
            pixels = original
            native_samples = None

            def resized(self, _dimension):
                return self

            def render(self, values):
                return (render(values) if values else original), "test"

        previous = self.suggest(original, render)
        self.assertEqual(previous.highlights, -.3)
        result = suggest_auto_adjustments_for_photo(Photo())
        self.assertEqual(result.metrics["exposure_refined"], 0)
        self.assertEqual(result.highlights, -.5)
        for percent in range(1, 101):
            self.assertLess(render({key: value * percent / 100 for key, value in result.as_overrides().items()})[:20].max(), 254)

    def test_white_balance_after_recovery_retains_the_extra_strength_checks(self):
        original, base_render = recovery_scene()

        def render(values):
            result = base_render(values)
            if values["warmth"] > 0 and -.08 < values["highlights"] < -.03 and values["exposure"] > .025:
                result[:20] = 255
            return result

        def balance(_evidence, _measure, values, validate, **kwargs):
            self.assertTrue({.1, .15, .2, .35}.issubset(kwargs["validation_strengths"]))
            candidate = {**values, "warmth": .1}
            accepted = validate(candidate)
            return (candidate if accepted else values), {"white_balance_refined": float(accepted)}

        with patch("openraw_studio.decision.auto_adjust.refine_white_balance", side_effect=balance):
            result = self.suggest(original, render)
        self.assertEqual(result.metrics["exposure_refined"], 1)
        self.assertEqual(result.metrics["white_balance_refined"], 0)
        self.assertEqual(result.warmth, 0)

    def test_backs_off_excessive_lift_and_rejects_when_both_steps_are_too_large(self):
        for lift, expected in ((50, 1), (90, 0)):
            with self.subTest(lift=lift):
                original, render = recovery_scene(lift=lift)
                initial = suggest_auto_adjustments_from_preview(original)
                result = self.suggest(original, render)
                self.assertEqual(result.metrics["exposure_refined"], expected)
                self.assertLessEqual(result.exposure, initial.exposure + .2)

    def test_detail_resolution_cannot_hide_excessive_gain(self):
        original, render = recovery_scene()
        _, exaggerated = recovery_scene(lift=90)
        result = self.suggest(original, render, detail_preview=original, render_detail=exaggerated)
        self.assertEqual(result.metrics["exposure_refined"], 0)

    def test_preserves_low_key_and_already_bright_scenes(self):
        for level in (6, 20, 150, 240):
            with self.subTest(level=level):
                original, render = recovery_scene(level=level)
                result = self.suggest(original, render)
                self.assertEqual(result.metrics["exposure_refined"], 0)
                if level == 6:
                    self.assertEqual(result.scene, "Low-key")
                    self.assertLessEqual(result.exposure, .35)

    def test_native_brightness_does_not_set_the_scene_target(self):
        original, render = recovery_scene()
        native = np.full((20, 20, 3), 240, np.uint8)
        result = self.suggest(original, render, native_preview=native, render_native=lambda _: native)
        self.assertEqual(result.metrics["exposure_refined"], 1)

    def test_real_renderer_retains_black_and_highlight_detail(self):
        pixels = np.full((100, 100, 3), .035, np.float32)
        pixels[:5] = 0
        pixels[10:30] = .8
        photo = InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1))
        original = photo.render({})[0]
        result = self.suggest(original, lambda values: photo.render(values)[0])
        self.assertEqual(result.metrics["exposure_refined"], 1)
        for strength in (.25, .5, .7, 1):
            rendered = np.asarray(photo.render({key: value * strength for key, value in result.as_overrides().items()})[0])
            self.assertTrue(np.all(rendered[:5] == 0))
            self.assertTrue(np.all(rendered[10:30] < 254))


if __name__ == "__main__":
    unittest.main()
