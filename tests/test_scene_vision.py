import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.vision import scene


class SceneVisionTests(unittest.TestCase):
    def test_scores_are_content_weights_with_separate_lighting(self):
        scores = np.full(len(scene.SCENES) + len(scene.LIGHTING), .1, np.float32)
        scores[list(scene.SCENES).index("Coast")] = .33
        scores[len(scene.SCENES) + list(scene.LIGHTING).index("Sunset")] = .32
        result = scene.evidence_from_scores(scores)
        self.assertEqual((result.status, result.scene, result.lighting), ("ready", "Coast", "Sunset"))
        self.assertGreater(result.reliability, .9)
        self.assertAlmostEqual(sum(result.scenes.values()), 1, places=5)
        self.assertAlmostEqual(sum(result.lights.values()), 1, places=5)

    def test_ambiguous_and_low_similarity_abstain(self):
        for value in (.05, .32):
            evidence = scene.evidence_from_scores(np.full(21, value))
            self.assertEqual(evidence.status, "uncertain")
            self.assertEqual(evidence.lighting, "Mixed light")
        scores = np.full(21, -.1)
        scores[0] = .18
        self.assertEqual(scene.evidence_from_scores(scores).status, "uncertain")

    def test_malformed_scores_rejected(self):
        for scores in (np.zeros(5), np.full(21, np.nan), np.full(21, 1.5)):
            with self.assertRaises(ValueError):
                scene.evidence_from_scores(scores)

    def test_preprocess_has_fixed_rgb_normalization_without_mutating_image(self):
        original = Image.new("RGB", (321, 181), (64, 128, 192))
        before = original.tobytes()
        result = scene.prepare_scene_input(original)
        self.assertEqual(result.shape, (1, 3, 224, 224))
        self.assertEqual(result.dtype, np.float32)
        expected = (np.array([64, 128, 192]) / 255 - [.48145466, .4578275, .40821073]) / [.26862954, .26130258, .27577711]
        np.testing.assert_allclose(result[0, :, 100, 100], expected, atol=1e-6)
        self.assertEqual(original.tobytes(), before)

    def test_odd_aspect_ratios_do_not_introduce_crop_padding(self):
        reference = scene.prepare_scene_input(Image.new("RGB", (224, 224), "white"))
        for size in ((960, 641), (97, 199), (133, 401), (641, 960)):
            result = scene.prepare_scene_input(Image.new("RGB", size, "white"))
            np.testing.assert_array_equal(result, reference)

    def test_disabled_missing_and_nonspatial_inputs_do_not_load_model(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENRAW_SCENE_MODEL": directory}):
            with patch.dict(os.environ, {"OPENRAW_SCENE": "off"}):
                self.assertEqual(scene.analyze_scene(Image.new("RGB", (200, 200))).status, "disabled")
            with patch.dict(os.environ, {"OPENRAW_SCENE": "auto"}):
                self.assertEqual(scene.analyze_scene(Image.new("RGB", (200, 200))).status, "not-installed")
                self.assertEqual(scene.analyze_scene(np.zeros((200, 3))).status, "insufficient-resolution")
                self.assertEqual(scene.analyze_scene(Image.new("RGB", (12, 16))).status, "insufficient-resolution")

    def test_model_errors_fall_back_without_escaping_auto_worker(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENRAW_SCENE_MODEL": directory, "OPENRAW_SCENE": "auto"}):
            Path(directory, "manifest.json").write_text("{}")
            with patch.object(scene, "_classifier", None), patch.object(scene.LocalSceneClassifier, "classify", side_effect=Exception("runtime failure")):
                self.assertEqual(scene.analyze_scene(Image.new("RGB", (200, 200))).status, "unavailable")

    def test_cache_is_content_based_bounded_and_read_only(self):
        class Session:
            calls = 0

            def run(self, *_):
                self.calls += 1
                return [np.full((1, 21), .3)]

        classifier = scene.LocalSceneClassifier("unused")
        classifier.session = Session()
        first = Image.new("RGB", (100, 120), "blue")
        result = classifier.classify(first)
        self.assertIs(classifier.classify(first.copy()), result)
        self.assertEqual(classifier.session.calls, 1)
        for value in range(20):
            classifier.classify(Image.new("RGB", (100, 120), (value, 10, 30)))
        self.assertLessEqual(len(classifier.cache), 16)

    def test_manifest_and_checksum_are_checked_before_session_creation(self):
        class Runtime:
            @staticmethod
            def InferenceSession(*_, **__):
                raise AssertionError("Unverified model loaded")

        with TemporaryDirectory() as directory, patch.dict("sys.modules", {"onnxruntime": Runtime}):
            manifest = Path(directory, "manifest.json")
            manifest.write_text(json.dumps({"model_id": "other"}))
            classifier = scene.LocalSceneClassifier(directory)
            with self.assertRaisesRegex(ValueError, "manifest"):
                classifier._load()
            Path(directory, "scene.onnx").write_bytes(b"broken")
            manifest.write_text(json.dumps({"model_id": scene.MODEL_ID, "prompt_sha256": scene.prompt_digest(),
                                            "onnx_sha256": hashlib.sha256(b"correct").hexdigest()}))
            with self.assertRaisesRegex(ValueError, "checksum"):
                classifier._load()
