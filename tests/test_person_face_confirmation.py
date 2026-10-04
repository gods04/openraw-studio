import unittest
from unittest.mock import Mock, patch
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from openraw_studio.vision.face import Face, FaceAnalysis, face_interiors
from openraw_studio.vision.person import confirm_person
from openraw_studio.vision import person
from openraw_studio.vision.scene import SceneEvidence


class PersonFaceConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new('RGB', (600, 400))
        self.scores = np.zeros((192, 192), np.float32)
        self.scores[24:170, 70:130] = .99
        self.face = Face((.4, .16, .6, .36), .95)
        self.scene = SceneEvidence('ready', 'Aquarium', 'Colored light', .8,
                                   {'Aquarium': .9, 'Portrait': .05}, {'Colored light': 1})

    def confirm(self, *, scores=None, faces=None, classify=None, locate=None):
        return confirm_person(self.image, self.scores if scores is None else scores,
                              classify=classify or (lambda _: self.scene),
                              locate_faces=locate or Mock(return_value=faces or FaceAnalysis('ready', (self.face,))))

    def test_face_can_corroborate_a_strong_region_without_relabeling_scene_evidence(self):
        saved = self.scores.copy()
        result = self.confirm()
        self.assertEqual(result.evidence.status, 'ready')
        self.assertEqual(result.evidence.regions, 1)
        self.assertEqual(result.evidence.face_regions, 1)
        self.assertAlmostEqual(result.evidence.scene_agreement, .04)
        self.assertTrue(result.core_mask((192, 192))[80, 100])
        self.assertFalse(result.core_mask((192, 192))[80, 20])
        self.assertFalse(result.probabilities.flags.writeable)
        np.testing.assert_array_equal(self.scores, saved)

    def test_successful_scene_corroboration_skips_face_model(self):
        locate = Mock(side_effect=AssertionError('Unnecessary face inference'))
        result = self.confirm(classify=lambda _: SceneEvidence('ready', 'Portrait', reliability=1,
                                                               scenes={'Portrait': .8}), locate=locate)
        self.assertEqual(result.evidence.status, 'ready')
        self.assertEqual(result.evidence.face_regions, 0)
        self.assertIs(result.probabilities, self.scores)
        locate.assert_not_called()

    def test_face_confirmation_does_not_fabricate_missing_scene_confidence(self):
        result = self.confirm(classify=lambda _: SceneEvidence('not-installed'))
        self.assertEqual(result.evidence.status,'ready')
        self.assertEqual(result.evidence.face_regions,1)
        self.assertEqual(result.evidence.scene_agreement,0)

    def test_face_does_not_rescue_tiny_diffuse_or_weak_segmentation(self):
        locate = Mock(side_effect=AssertionError('Unnecessary face inference'))
        for scores in (np.zeros_like(self.scores), np.ones_like(self.scores), self.scores * .94):
            result = self.confirm(scores=scores, locate=locate)
            self.assertNotEqual(result.evidence.status, 'ready')
        locate.assert_not_called()

    def test_missing_uncertain_and_failed_face_evidence_leave_region_unconfirmed(self):
        for status in ('disabled', 'not-installed', 'unavailable', 'no-face', 'uncertain'):
            self.assertEqual(self.confirm(faces=FaceAnalysis(status)).evidence.status, 'unconfirmed')
        result = self.confirm(locate=Mock(side_effect=RuntimeError('Optional model failed')))
        self.assertEqual(result.evidence.status, 'unconfirmed')

    def test_face_outside_or_only_partly_inside_mask_does_not_confirm(self):
        for box in ((.05,.16,.25,.36), (.25,.16,.5,.36), (.4,.01,.6,.15)):
            self.assertEqual(self.confirm(faces=FaceAnalysis('ready', (Face(box,.99),))).evidence.status,
                             'unconfirmed')

    def test_low_score_tiny_invalid_or_nonfinite_faces_are_not_evidence(self):
        for face in (Face(self.face.box,.899), Face(self.face.box,float('nan')),
                     Face((.5,.2,.51,.21),.99), Face((.6,.2,.4,.4),.99),
                     Face((float('nan'),.2,.6,.4),.99)):
            self.assertEqual(self.confirm(faces=FaceAnalysis('ready', (face,))).evidence.status, 'unconfirmed')

    def test_only_matching_component_is_kept(self):
        scores = self.scores.copy()
        scores[25:170, 5:50] = .99
        result = self.confirm(scores=scores)
        self.assertEqual(result.evidence.regions, 1)
        self.assertEqual(result.evidence.face_regions, 1)
        self.assertTrue(result.core_mask((192,192))[80,100])
        self.assertFalse(result.core_mask((192,192))[80,20])

    def test_a_face_cannot_join_disconnected_regions_or_count_twice(self):
        scores = self.scores.copy()
        scores[45:90, 99:102] = 0
        result = self.confirm(scores=scores, faces=FaceAnalysis('ready', (self.face, self.face)))
        self.assertEqual(result.evidence.regions, 1)
        self.assertEqual(result.evidence.face_regions, 1)
        scores[24:170, 99:102] = 0
        self.assertEqual(self.confirm(scores=scores).evidence.status, 'unconfirmed')

    def test_scene_and_face_regions_can_coexist_with_one_face_inference(self):
        scores = self.scores.copy()
        scores[25:170, 5:50] = .99
        votes = iter((self.scene, self.scene,
                      SceneEvidence('ready','Portrait',reliability=1,scenes={'Portrait':1})))
        locate = Mock(return_value=FaceAnalysis('ready',(self.face,)))
        result = self.confirm(scores=scores, classify=lambda _: next(votes), locate=locate)
        self.assertEqual((result.evidence.regions, result.evidence.face_regions),(2,1))
        locate.assert_called_once_with(self.image)

    def test_face_runtime_failure_preserves_scene_confirmed_region(self):
        scores = self.scores.copy()
        scores[25:170, 5:50] = .99
        votes = iter((self.scene, self.scene,
                      SceneEvidence('ready','Portrait',reliability=1,scenes={'Portrait':1})))
        result = self.confirm(scores=scores, classify=lambda _: next(votes),
                              locate=Mock(side_effect=RuntimeError('Unavailable')))
        self.assertEqual((result.evidence.regions,result.evidence.face_regions),(1,0))
        self.assertFalse(result.core_mask((192,192))[80,100])
        self.assertTrue(result.core_mask((192,192))[80,20])

    def test_shared_face_geometry_is_orientation_and_resolution_relative(self):
        small = next(face_interiors((self.face,),(192,192)))
        large = next(face_interiors((self.face,),(400,600)))
        for mask in (small,large):
            ys,xs = np.nonzero(mask)
            self.assertAlmostEqual(xs.mean()/mask.shape[1],.5,delta=.005)
            self.assertAlmostEqual(ys.mean()/mask.shape[0],.26,delta=.005)

    def test_analysis_wires_optional_face_evidence_and_refines_only_after_confirmation(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            'OPENRAW_PERSON_MODEL': directory, 'OPENRAW_SCENE': 'auto', 'OPENRAW_PERSON': 'auto',
        }):
            Path(directory, 'person.onnx').touch()
            with patch.object(person, '_segmenter', None), \
                    patch.object(person.LocalPersonSegmenter, 'segment', return_value=self.scores), \
                    patch.object(person, 'analyze_scene', return_value=self.scene), \
                    patch.object(person, 'analyze_faces', return_value=FaceAnalysis('ready',(self.face,))) as locate, \
                    patch.object(person, 'guided_selection', wraps=person.guided_selection) as refine:
                result = person.analyze_person(self.image)
                self.assertEqual(result.evidence.face_regions,1)
                locate.assert_called_once_with(self.image)
                refine.assert_called_once()
                self.assertFalse(result.selection.flags.writeable)


if __name__ == '__main__':
    unittest.main()
