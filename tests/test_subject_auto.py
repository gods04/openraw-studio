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
from openraw_studio.decision.subject_exposure import suggest_subject_exposure, suggest_subject_exposure_for_photo
from openraw_studio.raw.native.subject import apply_subject
from openraw_studio.vision.face import Face, FaceAnalysis
from openraw_studio.vision.scene import SceneEvidence


class SubjectAutoTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / 'source.NEF'
        source.write_bytes(b'original')
        self.pixels = np.full((160, 240, 3), 140, np.uint8)
        self.pixels[20:150, 80:160] = 90
        weights = np.zeros((160, 240), np.float32)
        weights[20:150, 80:160] = 1
        self.subject = make_subject(weights, source)
        self.faces = FaceAnalysis('ready', (Face((.375, .15, .625, .45), .95),))
        self.scene = SceneEvidence('ready', 'Portrait', 'Daylight', 1, {'Portrait': 1}, {'Daylight': 1})

    def suggest(self, pixels=None, **changes):
        return suggest_subject_exposure(Image.fromarray(self.pixels if pixels is None else pixels),
                                        changes.get('subject', self.subject), changes.get('faces', self.faces),
                                        changes.get('scene', self.scene))

    def test_relative_fill_is_bounded_and_improves_measured_face(self):
        before = self.pixels.copy()
        subject = deepcopy(self.subject)
        result = self.suggest()
        self.assertEqual(result.status, 'suggested')
        self.assertTrue(0 < result.exposure <= .8)
        self.assertGreater(result.metrics['face_luma_after'], result.metrics['face_luma_before'] + .02)
        self.assertLess(result.metrics['objective_after'], result.metrics['objective_before'])
        self.assertEqual(result.metrics['validation_renders'], 12)
        np.testing.assert_array_equal(self.pixels, before)
        self.assertEqual(self.subject, subject)
        self.assertEqual(result, self.suggest())

    def test_clothing_color_does_not_set_face_exposure(self):
        dark, bright = self.pixels.copy(), self.pixels.copy()
        dark[80:150, 80:160] = (5, 8, 10)
        bright[80:150, 80:160] = (240, 240, 230)
        self.assertEqual(self.suggest(dark).exposure, self.suggest(bright).exposure)

    def test_dark_background_and_balanced_faces_do_not_force_brightness(self):
        dark = self.pixels.copy()
        dark[:, :80] = dark[:, 160:] = 40
        dark[:20] = dark[150:] = 40
        self.assertEqual(self.suggest(dark).status, 'balanced')
        bright = self.pixels.copy()
        bright[20:150, 80:160] = 135
        self.assertEqual(self.suggest(bright).status, 'balanced')

    def test_environment_continuously_limits_fill(self):
        day = self.suggest()
        evening = self.suggest(scene=replace(self.scene, lights={'Sunset': .5, 'Daylight': .5}))
        night = self.suggest(scene=replace(self.scene, lights={'Night': 1}))
        aquarium = self.suggest(scene=replace(self.scene, scenes={'Aquarium': 1}))
        self.assertGreater(day.exposure, evening.exposure or 0)
        self.assertGreater(evening.exposure, night.exposure or 0)
        self.assertEqual(night.exposure, aquarium.exposure)

    def test_abstains_without_matching_face_or_reliable_context(self):
        for status in ('disabled', 'no-face', 'not-installed', 'unavailable'):
            self.assertEqual(self.suggest(faces=FaceAnalysis(status)).status, 'face-' + status)
        self.assertEqual(self.suggest(subject=None).status, 'no-selection')
        self.assertEqual(self.suggest(scene=SceneEvidence('uncertain')).status, 'scene-uncertain')
        outside = FaceAnalysis('ready', (Face((.01, .15, .15, .45), .95),))
        self.assertEqual(self.suggest(faces=outside).status, 'face-outside-selection')
        for score in (.85, float('nan')):
            self.assertEqual(self.suggest(faces=FaceAnalysis('ready', (replace(self.faces.faces[0], score=score),))).status,
                             'face-outside-selection')

    def test_partial_selection_does_not_expose_entire_detected_face(self):
        with patch('openraw_studio.decision.subject_exposure.subject_weights', return_value=np.zeros((160, 240))):
            self.assertEqual(self.suggest().status, 'face-outside-selection')
        with patch('openraw_studio.decision.subject_exposure.subject_weights', return_value=np.ones((160, 240))):
            self.assertEqual(self.suggest().status, 'insufficient-context')

    def test_bright_face_can_be_reduced_without_crushing_shadows(self):
        pixels = self.pixels.copy()
        pixels[:, :] = 100
        pixels[20:150, 80:160] = 182
        pixels[25:40, 90:150] = 240
        result = self.suggest(pixels)
        self.assertEqual(result.status, 'suggested')
        self.assertLess(result.exposure, 0)
        self.assertGreaterEqual(result.exposure, -.5)

    def test_competing_faces_are_not_averaged_into_one_harmful_edit(self):
        pixels = self.pixels.copy()
        pixels[:] = 110
        pixels[20:150, 80:160] = 70
        pixels[80:150, 80:160] = 182
        pixels[92:103, 90:150] = 240
        faces = FaceAnalysis('ready', self.faces.faces + (Face((.375, .55, .625, .90), .98),))
        self.assertEqual(self.suggest(pixels, faces=faces).status, 'conflicting-faces')

    def test_rgb8_required_and_background_is_exact_after_advice(self):
        with self.assertRaisesRegex(ValueError, 'RGB8'):
            suggest_subject_exposure(Image.new('L', (240, 160)), self.subject, self.faces, self.scene)
        result = self.suggest()
        edited = apply_subject(self.pixels, {**self.subject, 'exposure': result.exposure})
        np.testing.assert_array_equal(edited[:, :80], self.pixels[:, :80])
        np.testing.assert_array_equal(edited[:, 160:], self.pixels[:, 160:])
        self.assertLessEqual(edited.max(), 255)

    def test_subject_highlights_outside_face_are_also_guarded(self):
        def unsafe_render(pixels, subject):
            edited = apply_subject(pixels, subject)
            if subject['exposure'] != 0:
                edited = edited.copy()
                edited[80:140, 90:150] = 255
            return edited
        with patch('openraw_studio.decision.subject_exposure.apply_subject', side_effect=unsafe_render):
            result = self.suggest()
        self.assertEqual(result.status, 'no-safe-benefit')
        self.assertIsNone(result.exposure)

    def test_saved_exposure_is_replaced_not_stacked_and_globals_are_retained(self):
        calls = []
        def render(edits):
            calls.append(deepcopy(edits))
            return Image.fromarray(self.pixels), None
        photo = SimpleNamespace(render=render)
        edits = {'exposure': .3, 'warmth': -.2, 'color_noise': .3,
                 'subject': {**self.subject, 'exposure': -.7, 'enabled': False}}
        original = deepcopy(edits)
        with patch('openraw_studio.vision.face.analyze_faces', return_value=self.faces), \
                patch('openraw_studio.vision.scene.analyze_scene', return_value=self.scene) as scene:
            result = suggest_subject_exposure_for_photo(photo, edits)
        self.assertGreater(result.exposure, 0)
        self.assertEqual(calls, [{}, {'exposure': .3, 'warmth': -.2, 'color_noise': .3}])
        self.assertEqual(edits, original)
        scene.assert_called_once()

    def test_no_selection_or_missing_model_skips_unnecessary_renders(self):
        photo = SimpleNamespace(render=lambda _: (Image.fromarray(self.pixels), None))
        with patch.object(photo, 'render', wraps=photo.render) as render, \
                patch('openraw_studio.vision.face.analyze_faces', return_value=FaceAnalysis('disabled')):
            self.assertEqual(suggest_subject_exposure_for_photo(photo, {}).status, 'no-selection')
            render.assert_not_called()
            self.assertEqual(suggest_subject_exposure_for_photo(photo, {'subject': self.subject}).status, 'face-disabled')
            self.assertEqual(render.call_count, 1)

    def test_metering_keeps_color_in_baseline_and_every_candidate(self):
        colored = subject_with_color(self.subject, .4, -.3)
        with patch('openraw_studio.decision.subject_exposure.apply_subject', wraps=apply_subject) as render:
            result = self.suggest(subject=colored)
        self.assertEqual(result.status, 'suggested')
        self.assertEqual(render.call_args_list[0].args[1]['exposure'], 0)
        self.assertEqual(len(render.call_args_list), 13)
        for call in render.call_args_list:
            self.assertEqual((call.args[1]['warmth'], call.args[1]['tint']), (.4, -.3))
