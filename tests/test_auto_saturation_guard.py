import unittest

import numpy as np

from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_from_preview


class AutoSaturationGuardTests(unittest.TestCase):
    @staticmethod
    def scene(*, limit=.01, max_exposure=2, intermediate=False):
        pixels = np.full((100, 100, 3), (60, 62, 64), np.uint8)
        pixels[45:50, 48:52] = (210, 195, 190)

        def render(values):
            candidate = pixels.copy()
            if (
                values["saturation"] > limit or values["exposure"] > max_exposure
                or (intermediate and (
                    (values["exposure"] > 1 and values["highlights"] > -.2)
                    or .02 < values["saturation"] < .04
                ))
            ):
                candidate[45:50, 48:52, 0] = 255
            return candidate

        return pixels, render

    def test_half_boost_is_retained_when_safe_and_tones_remain_useful(self):
        pixels, render = self.scene(limit=.07)
        initial = suggest_auto_adjustments_from_preview(pixels)
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.saturation, initial.saturation / 2)
        self.assertEqual(result.exposure, initial.exposure)
        self.assertEqual(result.contrast, initial.contrast)
        self.assertEqual(result.highlights, initial.highlights)
        self.assertEqual(result.metrics["guard_strength"], 1)
        self.assertEqual(result.metrics["saturation_guarded"], 1)
        self.assertIn("Reduced added saturation", " ".join(result.rationale))

    def test_removing_boost_does_not_bypass_exposure_limits(self):
        pixels, render = self.scene(max_exposure=.6)
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertGreater(result.exposure, 0)
        self.assertLessEqual(result.exposure, .6)
        self.assertEqual(result.saturation, 0)
        self.assertEqual(result.metrics["exposure_guarded"], 1)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)

    def test_color_recovery_rechecks_detail_and_native_domains(self):
        pixels, unsafe = self.scene()
        for affected in ("primary", "detail", "native"):
            safe = lambda _: pixels
            with self.subTest(affected=affected):
                result = suggest_auto_adjustments_from_preview(
                    pixels, render=unsafe if affected == "primary" else safe,
                    detail_preview=pixels, render_detail=unsafe if affected == "detail" else safe,
                    native_preview=pixels, render_native=unsafe if affected == "native" else safe,
                )
                self.assertEqual(result.saturation, 0)
                self.assertGreater(result.exposure, 0)
                self.assertEqual(result.metrics["guard_strength"], 1)
                for prefix in ("", "detail_", "native_"):
                    self.assertEqual(result.metrics[prefix + "highlight_detail_loss_fraction"], 0)

    def test_half_boost_cannot_escape_intermediate_highlight_checks(self):
        pixels, render = self.scene(limit=.07, intermediate=True)
        result = suggest_auto_adjustments_from_preview(
            pixels, render=render, validation_strengths=(.25, .5, .7),
        )
        self.assertEqual(result.saturation, 0)
        self.assertLess(result.highlights, 0)
        self.assertEqual(result.metrics["saturation_guarded"], 1)
        for strength in (.25, .5, .7, 1):
            values = {key: value * strength for key, value in result.as_overrides().items()}
            self.assertLess(render(values)[45, 48, 0], 254)

    def test_safe_color_and_negative_saturation_are_not_rewritten(self):
        for color in ((60, 62, 64), (80, 150, 240)):
            pixels = np.full((20, 20, 3), color, np.uint8)
            expected = suggest_auto_adjustments_from_preview(pixels)
            result = suggest_auto_adjustments_from_preview(pixels, render=lambda _: pixels)
            with self.subTest(color=color):
                self.assertEqual(result.as_overrides(), expected.as_overrides())
                self.assertEqual(result.metrics["saturation_guarded"], 0)
                self.assertEqual(result.metrics["exposure_refined"], 0)
                # A dim result may probe two larger exposures before finding no gain.
                self.assertLessEqual(result.metrics["validation_renders"], 3 + result.metrics.get("white_balance_response_probes", 0))


if __name__ == "__main__":
    unittest.main()
