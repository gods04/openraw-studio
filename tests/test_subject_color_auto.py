from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.core.subject import make_subject, subject_with_color
from openraw_studio.decision.subject_color import suggest_subject_color, suggest_subject_color_for_photo
from openraw_studio.vision.face import Face, FaceAnalysis
from openraw_studio.vision.material import MaterialEvidence
from openraw_studio.vision.scene import SceneEvidence


class SubjectColorAutoTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'sample.NEF'
        path.write_bytes(b'original')
        self.pixels = np.full((180, 240, 3), 145, np.uint8)
        weights = np.zeros((180, 240), np.float32)
        weights[10:170, 70:170] = 1
        self.subject = make_subject(weights, path)
        for y in range(10, 170):
            self.pixels[y, 70:170] = np.rint((90 + y * .4) * np.array([1.02, 1, 1.12]))
        self.faces = FaceAnalysis('ready', (Face((.36, .07, .64, .3), .95),))
        self.scene = SceneEvidence('ready', 'Portrait', 'Daylight', 1, {'Portrait': 1}, {'Daylight': 1})
        self.material = MaterialEvidence('ready', 'White', .32, .06)

    def suggest(self, pixels=None, **changes):
        return suggest_subject_color(Image.fromarray(self.pixels if pixels is None else pixels),
                                     changes.get('subject', self.subject), changes.get('faces', self.faces),
                                     changes.get('scene', self.scene), changes.get('material', self.material))

    def test_independent_measured_local_color_reduces_a_blue_cast(self):
        before, subject = self.pixels.copy(), deepcopy(self.subject)
        advice = self.suggest()
        self.assertEqual(advice.status, 'suggested')
        self.assertGreater(advice.warmth, 0)
        self.assertLess(advice.tint, 0)
        self.assertLess(advice.metrics['cast_after'], advice.metrics['cast_before'] * .9)
        self.assertTrue(abs(advice.warmth) <= .3 and abs(advice.tint) <= .3)
        self.assertEqual(advice, self.suggest())
        np.testing.assert_array_equal(before, self.pixels)
        self.assertEqual(subject, self.subject)

    def test_opposite_cast_changes_warmth_direction(self):
        advice = self.suggest(self.pixels[..., ::-1].copy())
        self.assertEqual(advice.status, 'suggested')
        self.assertLess(advice.warmth, 0)

    def test_semantic_ambiguity_never_uses_colored_clothing_as_a_neutral(self):
        for evidence in (None, MaterialEvidence('uncertain', 'Pink', .35, -.05),
                         MaterialEvidence('disabled'), MaterialEvidence('not-installed')):
            self.assertIsNone(self.suggest(material=evidence).warmth)

    def test_ambient_lighting_is_retained(self):
        for lights, scenes in (({'Night': 1}, {}), ({'Sunset': .7}, {}), ({'Colored light': .6}, {}),
                               ({'Daylight': 1}, {'Aquarium': .4})):
            self.assertEqual(self.suggest(scene=replace(self.scene, lights=lights, scenes=scenes)).status, 'ambient-light')

    def test_face_and_neutral_area_requirements_abstain(self):
        self.assertEqual(self.suggest(subject=None).status, 'no-selection')
        self.assertEqual(self.suggest(faces=FaceAnalysis('no-face')).status, 'face-no-face')
        self.assertEqual(self.suggest(scene=SceneEvidence('uncertain')).status, 'scene-uncertain')
        pixels = self.pixels.copy()
        pixels[10:170, 70:170] = [240, 30, 40]
        self.assertEqual(self.suggest(pixels).status, 'insufficient-neutrals')
        pixels[10:170, 70:170] = [110, 110, 120]
        self.assertEqual(self.suggest(pixels).status, 'insufficient-neutrals')

    def test_equal_scene_cast_is_not_misdiagnosed_as_local_mixed_light(self):
        pixels = self.pixels.copy()
        outside = np.ones((180, 240), bool)
        outside[10:170, 70:170] = False
        pixels[outside] = [148, 145, 162]
        self.assertEqual(self.suggest(pixels).status, 'balanced')

    def test_oppositely_cast_regions_are_not_hidden_by_the_larger_region(self):
        pixels = self.pixels.copy()
        for y in range(10, 170):
            color = np.array([1.1, 1, 1.01]) if y < 95 else np.array([1.01, 1, 1.1])
            pixels[y, 70:170] = np.rint((90 + y * .4) * color)
        advice = self.suggest(pixels)
        self.assertEqual(advice.status, 'suggested')
        self.assertLess(abs(advice.warmth), .08)
        self.assertLess(advice.tint, 0)

    def test_wrapper_retains_local_exposure_but_removes_old_color_before_metering(self):
        calls = []
        def render(edits):
            calls.append(deepcopy(edits))
            return Image.fromarray(self.pixels), None
        subject = subject_with_color({**self.subject, 'exposure': .5}, -.7, .6)
        overrides = {'exposure': .2, 'subject': subject}
        with patch('openraw_studio.vision.face.analyze_faces', return_value=self.faces), \
                patch('openraw_studio.vision.material.analyze_material', return_value=self.material):
            result = suggest_subject_color_for_photo(SimpleNamespace(render=render), overrides, scene=self.scene)
        self.assertEqual(result.status, 'suggested')
        self.assertEqual(calls[-1]['subject']['exposure'], .5)
        self.assertEqual(calls[-1]['subject']['warmth'], 0)
        self.assertEqual(calls[-1]['subject']['tint'], 0)
        self.assertEqual(overrides['subject']['warmth'], -.7)
