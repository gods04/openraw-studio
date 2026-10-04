import os
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from PIL import Image

from openraw_studio.vision import material, scene


class MaterialVisionTests(unittest.TestCase):
    def test_neutral_material_requires_absolute_score_and_color_margin(self):
        scores = np.full(len(material.MATERIALS), .2, np.float32)
        scores[0] = .30
        result = material.evidence_from_scores(scores)
        self.assertEqual((result.status, result.label), ('ready', 'White'))
        scores[3] = .29
        self.assertEqual(material.evidence_from_scores(scores).status, 'uncertain')
        scores[3] = .35
        result = material.evidence_from_scores(scores)
        self.assertEqual((result.status, result.label), ('uncertain', 'Pink'))
        self.assertEqual(material.evidence_from_scores(np.full(len(scores), .2)).status, 'uncertain')
        for invalid in (np.zeros(3), scores * np.nan, scores * 4):
            with self.assertRaises(ValueError):
                material.evidence_from_scores(invalid)

    def test_crop_is_oriented_bounded_and_requires_sufficient_resolution(self):
        image = Image.new('RGB', (200, 150), (50, 100, 200))
        weights = np.zeros((150, 200), np.float32)
        weights[20:120, 50:180] = 1
        crop = material.subject_crop(image, weights)
        self.assertEqual(crop.size, (130, 100))
        self.assertEqual(crop.getpixel((0, 0)), (50, 100, 200))
        self.assertIsNone(material.subject_crop(image, weights * .8))
        weights[:] = 0
        weights[20:40, 50:100] = 1
        self.assertIsNone(material.subject_crop(image, weights))
        with self.assertRaises(ValueError):
            material.subject_crop(image, np.ones((100, 150)))

    def test_optional_head_has_separate_cache_keys_and_never_changes_scene_evidence(self):
        class Session:
            calls = 0
            def run(self, outputs, _):
                self.calls += 1
                if outputs == ['material_scores']:
                    result = np.full((1, len(material.MATERIALS)), .15)
                    result[0, 0] = .30
                    return [result]
                return [np.full((1, 21), .3)]
        classifier = scene.LocalSceneClassifier('unused')
        classifier.session = Session()
        image = Image.new('RGB', (120, 100), 'white')
        self.assertEqual(classifier.classify_material(image).status, 'not-installed')
        self.assertEqual(classifier.session.calls, 0)
        classifier.material_available = True
        first = classifier.classify(image)
        evidence = classifier.classify_material(image)
        self.assertEqual(evidence.status, 'ready')
        self.assertIs(classifier.classify(image), first)
        self.assertIs(classifier.classify_material(image.copy()), evidence)
        self.assertEqual(classifier.session.calls, 2)
        for value in range(20):
            classifier.classify_material(Image.new('RGB', (120, 100), (value, 30, 40)))
        self.assertEqual(len(classifier.cache), 16)

    def test_disabled_missing_and_broken_models_do_not_affect_editing(self):
        image = Image.new('RGB', (150, 100))
        weights = np.ones((100, 150))
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            'OPENRAW_SCENE_MODEL': directory, 'OPENRAW_SCENE': 'auto', 'OPENRAW_PERSON': 'auto', 'OPENRAW_MATERIAL': 'auto',
        }), patch.object(scene, '_classifier', None):
            self.assertEqual(material.analyze_material(image, weights).status, 'not-installed')
            for flag in ('OPENRAW_SCENE', 'OPENRAW_PERSON', 'OPENRAW_MATERIAL'):
                with patch.dict(os.environ, {flag: 'off'}):
                    self.assertEqual(material.analyze_material(image, weights).status, 'disabled')
            Path(directory, 'manifest.json').write_text('{}')
            self.assertEqual(material.analyze_material(image, weights).status, 'unavailable')

    def test_manifest_digest_and_output_name_are_both_required_for_optional_head(self):
        with TemporaryDirectory() as directory:
            Path(directory, 'scene.onnx').write_bytes(b'verified')
            manifest = {'model_id': scene.MODEL_ID, 'prompt_sha256': scene.prompt_digest(),
                        'onnx_sha256': hashlib.sha256(b'verified').hexdigest()}
            for digest, output_names, enabled in (
                (None, ['scores'], False), ('wrong', ['scores', 'material_scores'], False),
                (material.prompt_digest(), ['scores'], False),
                (material.prompt_digest(), ['scores', 'material_scores'], True),
            ):
                Path(directory, 'manifest.json').write_text(json.dumps({**manifest, 'material_prompt_sha256': digest}))
                session = SimpleNamespace(get_outputs=lambda: [SimpleNamespace(name=name) for name in output_names])
                runtime = SimpleNamespace(disable_telemetry_events=lambda: None, SessionOptions=SimpleNamespace,
                                          InferenceSession=lambda *args, **kwargs: session)
                with patch.dict('sys.modules', {'onnxruntime': runtime}):
                    classifier = scene.LocalSceneClassifier(directory)
                    classifier._load()
                    self.assertIs(classifier.session, session)
                    self.assertEqual(classifier.material_available, enabled)
