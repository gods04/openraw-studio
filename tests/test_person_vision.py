import json
import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.vision import person
from openraw_studio.vision.scene import SceneEvidence


def probabilities(value=.99):
    result = np.zeros((192, 192), np.float32)
    result[24:170, 70:130] = value
    return result


class PersonVisionTests(unittest.TestCase):
    def test_preprocess_is_rgb_full_frame_with_fixed_normalization(self):
        image = Image.new("RGB", (200, 300), (0, 128, 255))
        before = image.tobytes()
        data = person.prepare_person_input(image)
        self.assertEqual(data.shape, (1, 3, 192, 192))
        self.assertEqual(data.dtype, np.float32)
        np.testing.assert_allclose(data[0, :, 0, 0], [-1, 128 / 127.5 - 1, 1], atol=1e-7)
        self.assertEqual(before, image.tobytes())

    def test_model_output_is_validated_and_detached_read_only(self):
        foreground = probabilities()
        scores = np.stack([1 - foreground, foreground])[None]
        result = person.person_probabilities(scores)
        np.testing.assert_array_equal(result, foreground)
        self.assertFalse(result.flags.writeable)
        self.assertFalse(np.shares_memory(scores, result))
        for bad in (np.zeros((1, 2, 10, 10)), scores * 2, scores * np.nan, np.zeros_like(scores)):
            with self.assertRaises(ValueError):
                person.person_probabilities(bad)

    def test_clear_mask_requires_corroborating_scene_without_forcing_top_label(self):
        calls = []
        def classify(crop):
            calls.append(crop.size)
            return SceneEvidence("ready", "Aquarium", "Mixed light", .8, {"Portrait": .25}, {})
        result = person.confirm_person(Image.new("RGB", (600, 400)), probabilities(), classify=classify)
        self.assertEqual(result.evidence.status, "ready")
        self.assertAlmostEqual(result.evidence.scene_agreement, .2)
        self.assertLess(calls[0][0], 600)
        self.assertEqual(result.core_mask((60, 90)).shape, (60, 90))
        self.assertTrue(result.core_mask((192, 192))[90, 100])
        self.assertFalse(result.core_mask((192, 192))[90, 10])
        json.dumps(result.evidence.__dict__)

    def test_small_diffuse_or_weak_masks_do_not_invoke_scene_model(self):
        def unexpected(_):
            raise AssertionError("Unnecessary scene inference")
        for scores in (np.zeros((192, 192), np.float32), np.ones((192, 192), np.float32), probabilities(.93)):
            result = person.confirm_person(Image.new("RGB", (300, 200)), scores, classify=unexpected)
            self.assertIsNone(result.probabilities)
            self.assertIsNone(result.core_mask((50, 50)))

    def test_scene_disagreement_abstains(self):
        for evidence in (SceneEvidence("not-installed"), SceneEvidence("uncertain", reliability=1, scenes={"Portrait": 1}),
                         SceneEvidence("ready", "Sky", reliability=.8, scenes={"Portrait": .1})):
            result = person.confirm_person(Image.new("RGB", (300, 200)), probabilities(), classify=lambda _: evidence)
            self.assertEqual(result.evidence.status, "unconfirmed")
            self.assertIsNone(result.probabilities)

    def test_group_fallback_keeps_only_corroborated_components(self):
        scores = np.zeros((192, 192), np.float32)
        scores[20:160, 10:60] = .99
        scores[30:150, 130:180] = .99
        responses = iter([SceneEvidence("ready", "Interior", reliability=.8, scenes={"Portrait": .1}),
                          SceneEvidence("ready", "Portrait", reliability=.8, scenes={"Portrait": .8}),
                          SceneEvidence("ready", "Objects", reliability=.8, scenes={"Portrait": .01})])
        result = person.confirm_person(Image.new("RGB", (600, 400)), scores, classify=lambda _: next(responses))
        self.assertEqual(result.evidence.status, "ready")
        self.assertEqual(result.evidence.regions, 1)
        self.assertTrue(result.core_mask((192, 192))[60, 30])
        self.assertFalse(result.core_mask((192, 192))[60, 150])
        self.assertFalse(result.probabilities.flags.writeable)

    def test_components_are_size_bounded_and_do_not_join_diagonals(self):
        core = np.zeros((100, 100), bool)
        for y, x in ((0, 0), (10, 10), (25, 25), (40, 40)):
            core[y:y + 10, x:x + 10] = True
        core[90, 90] = True
        masks = list(person._components(core))
        self.assertEqual(len(masks), 3)
        self.assertTrue(all(mask.sum() == 100 for mask in masks))

    def test_cache_is_content_based_bounded_and_reuses_inference(self):
        class Session:
            calls = 0
            def run(self, *_):
                self.calls += 1
                foreground = probabilities()
                return [np.stack([1 - foreground, foreground])[None]]
        model = person.LocalPersonSegmenter("unused")
        model.session = Session()
        image = Image.new("RGB", (200, 100), "blue")
        first = model.segment(image)
        self.assertIs(first, model.segment(image.copy()))
        self.assertEqual(model.session.calls, 1)
        for value in range(20):
            model.segment(Image.new("RGB", (200, 100), (value, 10, 20)))
        self.assertLessEqual(len(model.cache), 16)

    def test_missing_disabled_and_small_images_never_load_a_model(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENRAW_PERSON_MODEL": directory, "OPENRAW_SCENE": "auto", "OPENRAW_PERSON": "auto"}):
            self.assertEqual(person.analyze_person(Image.new("RGB", (200, 200))).evidence.status, "not-installed")
            self.assertEqual(person.analyze_person(np.zeros((12, 12, 3))).evidence.status, "insufficient-resolution")
            self.assertEqual(person.analyze_person(np.zeros((12, 3))).evidence.status, "insufficient-resolution")
            for key in ("OPENRAW_PERSON", "OPENRAW_SCENE"):
                with patch.dict(os.environ, {key: "off"}):
                    self.assertEqual(person.analyze_person(Image.new("RGB", (200, 200))).evidence.status, "disabled")

    def test_invalid_model_is_rejected_before_creating_runtime(self):
        class Runtime:
            def InferenceSession(*_, **__):
                raise AssertionError("Unverified model loaded")
        with TemporaryDirectory() as directory, patch.dict("sys.modules", {"onnxruntime": Runtime}):
            Path(directory, "person.onnx").write_bytes(b"wrong")
            model = person.LocalPersonSegmenter(directory)
            with self.assertRaisesRegex(ValueError, "checksum"):
                model._load()

    def test_optional_runtime_failure_does_not_break_auto(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENRAW_PERSON_MODEL": directory, "OPENRAW_SCENE": "auto", "OPENRAW_PERSON": "auto"}):
            Path(directory, "person.onnx").touch()
            with patch.object(person, "_segmenter", None), patch.object(person.LocalPersonSegmenter, "segment", side_effect=RuntimeError("failed")):
                self.assertEqual(person.analyze_person(Image.new("RGB", (200, 200))).evidence.status, "unavailable")

    def test_setup_rejects_wrong_checkpoint_without_replacing_installed_files(self):
        install = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/prepare_person_model.py"))["install"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wrong.onnx"
            source.write_bytes(b"wrong")
            output = root / "installed"
            output.mkdir()
            (output / "person.onnx").write_bytes(b"existing")
            with self.assertRaisesRegex(ValueError, "checksum"):
                install(output, source)
            self.assertEqual((output / "person.onnx").read_bytes(), b"existing")
            self.assertEqual(list(output.iterdir()), [output / "person.onnx"])
