import unittest

import numpy as np

from openraw_studio.decision.scene_color import ColorObjective, refine_scene_color, _extrema
from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_from_preview
from openraw_studio.raw.native.interactive import InteractivePhoto
from openraw_studio.vision.scene import SceneEvidence


ZERO = dict(exposure=0., contrast=0., highlights=0., shadows=0., warmth=0., tint=0., saturation=0.)


def photo(rgb):
    pixels = np.broadcast_to(np.asarray(rgb, np.float32), (96, 128, 3)).copy()
    return InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1))


def evidence(label, light="Daylight"):
    return SceneEvidence("ready", label, light, 1., {label: 1.}, {light: 1.})


class SceneColorTests(unittest.TestCase):
    def test_fast_rgb_extrema_are_exact(self):
        rgb = np.random.default_rng(31).random((1200, 3), dtype=np.float32)
        high, low = _extrema(rgb)
        np.testing.assert_array_equal(high, rgb.max(-1))
        np.testing.assert_array_equal(low, rgb.min(-1))

    def test_scene_changes_objective_not_fixed_adjustment_values(self):
        outputs = []
        for rgb in ((.22, .30, .35), (.28, .31, .35)):
            item = photo(rgb)
            render = lambda values: item.render(values)[0]
            coast, metrics = refine_scene_color(render({}), render, ZERO, evidence("Coast"), lambda _: True)
            portrait, _ = refine_scene_color(render({}), render, ZERO, evidence("Portrait"), lambda _: True)
            self.assertNotEqual(portrait, coast)
            self.assertEqual(metrics["scene_color_refined"], 1)
            self.assertGreater(coast["saturation"], 0)
            self.assertLess(metrics["scene_color_error_after"], metrics["scene_color_error_before"])
            outputs.append(coast)
        self.assertNotEqual(outputs[0], outputs[1])

    def test_already_vivid_material_is_not_boosted(self):
        item = photo((.01, .12, .65))
        render = lambda values: item.render(values)[0]
        result, metrics = refine_scene_color(render({}), render, ZERO, evidence("Coast"), lambda _: True)
        self.assertEqual(result, ZERO)
        self.assertEqual(metrics["scene_color_refined"], 0)

    def test_sunset_preserves_measured_ambient_color(self):
        item = photo((.45, .28, .16))
        render = lambda values: item.render(values)[0]
        starting = {**ZERO, "warmth": -.30, "tint": .08}
        result, metrics = refine_scene_color(render({}), render, starting, evidence("Coast", "Sunset"), lambda _: True)
        self.assertEqual(metrics["scene_color_refined"], 1)
        self.assertGreater(result["warmth"], starting["warmth"])
        self.assertEqual(result["exposure"], starting["exposure"])

    def test_tonal_validator_can_reject_all_color_candidates(self):
        item = photo((.22, .30, .35))
        render = lambda values: item.render(values)[0]
        result, metrics = refine_scene_color(render({}), render, ZERO, evidence("Coast"), lambda _: False)
        self.assertEqual(result, ZERO)
        self.assertEqual(metrics["scene_color_refined"], 0)

    def test_uncertain_or_absent_evidence_does_not_render_or_change_parameters(self):
        def unexpected(_):
            raise AssertionError("Unnecessary render")
        for item in (None, SceneEvidence("uncertain"), SceneEvidence("not-installed")):
            result, _ = refine_scene_color(None, unexpected, ZERO, item, lambda _: True)
            self.assertEqual(result, ZERO)

    def test_uncertain_scene_retains_complete_tonal_auto_result(self):
        item = photo((.22, .30, .35))
        render = lambda values: item.render(values)[0]
        old = suggest_auto_adjustments_from_preview(render({}), render=render)
        result = suggest_auto_adjustments_from_preview(render({}), render=render,
                                                       scene_evidence=SceneEvidence("uncertain"))
        self.assertEqual(result.as_overrides(), old.as_overrides())
        self.assertEqual(result.metrics, old.metrics)
        self.assertEqual(result.scene, old.scene)

    def test_possible_skin_hue_and_saturation_are_protected(self):
        image = np.broadcast_to(np.array([170, 135, 110], np.uint8), (100, 100, 3)).copy()
        objective = ColorObjective(image, image, evidence("Portrait"))
        self.assertTrue(objective.protect_warm)
        self.assertTrue(objective.preserved(image.copy()))
        altered = image.copy()
        altered[:, :, 2] = 60
        self.assertFalse(objective.preserved(altered))

    def test_finer_detail_can_veto_small_proxy_improvement(self):
        item = photo((.22, .30, .35))
        render = lambda values: item.render(values)[0]
        detail = lambda values: render({key: -value for key, value in values.items()})
        result, _ = refine_scene_color(render({}), render, ZERO, evidence("Coast"), lambda _: True,
                                      detail_preview=render({}), render_detail=detail)
        self.assertEqual(result, ZERO)

    def test_malformed_color_inputs_fail_explicitly(self):
        with self.assertRaises(ValueError):
            ColorObjective(np.zeros((10, 3)), np.zeros((10, 3)), evidence("Coast"))
        with self.assertRaises(ValueError):
            ColorObjective(np.zeros((10, 10, 3)), np.zeros((11, 10, 3)), evidence("Coast"))
