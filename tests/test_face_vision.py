import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.vision import face


def outputs():
    return {f"{name}_{stride}": np.zeros((1, (640 // stride) ** 2, channels), np.float32)
            for stride in (8, 16, 32) for name, channels in (("cls", 1), ("obj", 1), ("bbox", 4))}


def add_box(data, box, score=.95, stride=8, offset=0):
    x, y, width, height = box
    cx, cy = x + width / 2, y + height / 2
    col, row = int(cx / stride) + offset, int(cy / stride)
    index = row * (640 // stride) + col
    data[f"cls_{stride}"][0, index] = score
    data[f"obj_{stride}"][0, index] = score
    data[f"bbox_{stride}"][0, index] = [cx / stride - col, cy / stride - row,
                                                     np.log(width / stride), np.log(height / stride)]


class FaceVisionTests(unittest.TestCase):
    def test_bgr_raw_input_aspect_padding_and_original_preservation(self):
        source = Image.new("RGB", (320, 160), (10, 90, 230))
        before = source.tobytes()
        data, size = face.prepare_face_input(source)
        self.assertEqual(size, (640, 320))
        self.assertEqual(data.shape, (1, 3, 640, 640))
        self.assertEqual(data.dtype, np.float32)
        self.assertTrue(data.flags.c_contiguous)
        np.testing.assert_array_equal(data[0, :, 0, 0], [230, 90, 10])
        self.assertFalse(data[:, :, 320:].any())
        self.assertEqual(before, source.tobytes())

    def test_decode_coordinates_are_normalized_to_unpadded_oriented_image(self):
        data = outputs()
        add_box(data, (80, 40, 64, 96))
        found = face.decode_faces(data, (640, 320))
        self.assertEqual(len(found), 1)
        np.testing.assert_allclose(found[0].box, [.125, .125, .225, .425], atol=1e-7)
        self.assertAlmostEqual(found[0].score, .95)

    def test_nms_keeps_best_box_across_strides(self):
        data = outputs()
        add_box(data, (80, 40, 64, 96), .94)
        add_box(data, (82, 42, 64, 96), .97, stride=16)
        add_box(data, (400, 60, 32, 32), .91)
        found = face.decode_faces(data, (640, 320))
        self.assertEqual(len(found), 2)
        self.assertAlmostEqual(found[0].score, .97)

    def test_padding_tiny_weak_and_boundary_boxes_are_rejected(self):
        for box, score in (((80, 350, 40, 50), .99), ((80, 60, 9, 11), .99),
                           ((80, 60, 40, 50), .89), ((-30, 60, 50, 50), .99)):
            data = outputs()
            add_box(data, box, score)
            self.assertEqual(face.decode_faces(data, (640, 320)), ())

    def test_malformed_outputs_and_size_cannot_reach_coordinates(self):
        for shape in ((1, 6400, 3), (6400, 4)):
            data = outputs()
            data['bbox_8'] = np.zeros(shape)
            with self.assertRaisesRegex(ValueError, 'shape'):
                face.decode_faces(data, (640, 320))
        data = outputs()
        data['cls_16'][0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, 'finite'):
            face.decode_faces(data, (640, 320))
        for size in ((0, 320), (641, 320), (640.0, 320), (True, 320)):
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                face.decode_faces(outputs(), size)

    def test_extreme_box_sizes_and_dense_candidates_are_bounded(self):
        data = outputs()
        add_box(data, (40, 40, 16, 16))
        data['bbox_8'][0, 6 * 80 + 6, 2] = 100
        self.assertEqual(face.decode_faces(data, (640, 640)), ())
        for y in range(20, 620, 80):
            for x in range(20, 620, 80):
                add_box(data, (x, y, 20, 20))
        self.assertEqual(len(face.decode_faces(data, (640, 640))), 16)

    def test_inference_cache_is_content_based_and_bounded(self):
        class Session:
            calls = 0
            def run(self, names, inputs):
                self.calls += 1
                self.assert_input = inputs['input'].shape
                data = outputs()
                return [data[name] for name in names]
        detector = face.LocalFaceDetector('unused')
        detector.session = Session()
        image = Image.new('RGB', (200, 100), 'red')
        first = detector.detect(image)
        self.assertIs(first, detector.detect(image.copy()))
        self.assertEqual(first.status, 'no-face')
        self.assertEqual(detector.session.calls, 1)
        for value in range(20):
            detector.detect(Image.new('RGB', (200, 100), (value, 20, 30)))
        self.assertEqual(len(detector.cache), 16)

    def test_disabled_missing_small_and_runtime_failure_are_optional(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            'OPENRAW_FACE_MODEL': directory, 'OPENRAW_SCENE': 'auto', 'OPENRAW_PERSON': 'auto', 'OPENRAW_FACE': 'auto',
        }), patch.object(face, '_detector', None):
            image = Image.new('RGB', (200, 100))
            self.assertEqual(face.analyze_faces(image).status, 'not-installed')
            self.assertEqual(face.analyze_faces(image.resize((90, 90))).status, 'insufficient-resolution')
            for flag in ('OPENRAW_SCENE', 'OPENRAW_PERSON', 'OPENRAW_FACE'):
                with patch.dict(os.environ, {flag: 'off'}):
                    self.assertEqual(face.analyze_faces(image).status, 'disabled')
            Path(directory, 'face.onnx').touch()
            with patch.object(face.LocalFaceDetector, 'detect', side_effect=RuntimeError('failed')):
                self.assertEqual(face.analyze_faces(image).status, 'unavailable')

    def test_checksum_is_verified_before_onnx_session_creation(self):
        runtime = SimpleNamespace(InferenceSession=lambda *a, **kw: self.fail('Unverified graph loaded'))
        with TemporaryDirectory() as directory, patch.dict(sys.modules, {'onnxruntime': runtime}):
            Path(directory, 'face.onnx').write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                face.LocalFaceDetector(directory).detect(Image.new('RGB', (100, 100)))

    def test_setup_verification_and_download_failure_preserve_installed_model(self):
        scripts = str(Path(__file__).resolve().parents[1] / 'scripts')
        with patch.object(sys, 'path', [scripts, *sys.path]):
            install = runpy.run_path(str(Path(scripts) / 'prepare_face_model.py'))['install']
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.onnx'
            source.write_bytes(b'new model')
            target = root / 'installed'
            target.mkdir()
            (target / 'face.onnx').write_bytes(b'previous')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                install(target, source)
            self.assertEqual((target / 'face.onnx').read_bytes(), b'previous')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            def fail_download(*_):
                raise OSError('offline')
            with patch.dict(install.__globals__, MODEL_SHA256=digest, download=fail_download):
                with self.assertRaisesRegex(OSError, 'offline'):
                    install(target, source)
            self.assertEqual((target / 'face.onnx').read_bytes(), b'previous')
            self.assertEqual(list(target.iterdir()), [target / 'face.onnx'])
            with patch.dict(install.__globals__, MODEL_SHA256=digest,
                            download=lambda _, path: path.write_text('MIT license')):
                report = install(target, source)
            self.assertEqual((target / 'face.onnx').read_bytes(), b'new model')
            self.assertEqual(report['onnx_sha256'], digest)
            self.assertEqual(json.loads((target / 'manifest.json').read_text())['license'], 'MIT')
