from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event, Lock, current_thread, get_ident
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.raw.native import acceleration, compiled_tone, cpu_chunks, cpu_tone_batch
from openraw_studio.raw.native.interactive import InteractivePhoto


class CpuToneBatchTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'OPENRAW_CPU_WORKERS': '4', 'OPENRAW_GPU': 'off'}))
        self.enterContext(patch.object(cpu_chunks.os, 'cpu_count', return_value=8))
        self.enterContext(patch.object(cpu_tone_batch, 'MIN_BATCH_PIXELS', 1))
        self.pixels = np.arange(37 * 49 * 3, dtype=np.float32).reshape(37, 49, 3) / 2000
        self.params = acceleration.color_parameters(np.eye(3), (1.2, 1, 1.5), highlights=-.3)

    @staticmethod
    def fake(pixels, params):
        return (pixels * 53 + params[9]).astype(np.uint8)

    def tearDown(self):
        self.assertFalse(cpu_chunks._parallel_frame.locked())
        self.assertIsNone(getattr(cpu_tone_batch._local, 'batch', None))

    def test_normal_calls_small_frames_and_explicit_serial_mode_do_not_make_pools(self):
        with patch.object(cpu_tone_batch, 'ThreadPoolExecutor', side_effect=AssertionError('pool')), patch.object(
            compiled_tone, 'render', side_effect=self.fake
        ) as kernel:
            cpu_tone_batch.render(self.pixels, self.params)
            self.assertEqual(kernel.call_count, 1)
            for setting, threshold in (('1', 1), ('4', 2_000_000)):
                with patch.dict(os.environ, {'OPENRAW_CPU_WORKERS': setting}), patch.object(
                    cpu_tone_batch, 'MIN_BATCH_PIXELS', threshold
                ), cpu_tone_batch.cpu_tone_batch():
                    np.testing.assert_array_equal(cpu_tone_batch.render(self.pixels, self.params), self.fake(self.pixels, self.params))

    def test_first_row_warms_on_caller_and_every_row_is_written_once(self):
        parent = get_ident()
        barrier = Barrier(4, timeout=5)
        lock = Lock()
        seen = []

        def render(pixels, params):
            with lock:
                initial = not seen
                seen.append(pixels.copy())
            if initial:
                self.assertEqual(get_ident(), parent)
                self.assertEqual(pixels.shape[0], 1)
            else:
                self.assertTrue(current_thread().name.startswith('openraw-auto'))
                barrier.wait()
            return self.fake(pixels, params)

        with patch.object(compiled_tone, 'render', side_effect=render), cpu_tone_batch.cpu_tone_batch():
            actual = cpu_tone_batch.render(self.pixels, self.params)
        np.testing.assert_array_equal(actual, self.fake(self.pixels, self.params))
        rows = sorted(row[0, 0] for block in seen for row in block)
        np.testing.assert_array_equal(rows, self.pixels[:, 0, 0])

    def test_pool_reused_within_call_and_nested_context_but_never_between_calls(self):
        with patch.object(cpu_tone_batch, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor) as factory, patch.object(
            compiled_tone, 'render', side_effect=self.fake
        ):
            with cpu_tone_batch.cpu_tone_batch():
                first = cpu_tone_batch.render(self.pixels, self.params)
                with cpu_tone_batch.cpu_tone_batch():
                    second = cpu_tone_batch.render(self.pixels + 1, self.params)
                self.assertEqual(factory.call_count, 1)
                self.assertTrue(cpu_chunks._parallel_frame.locked())
            self.assertFalse(cpu_chunks._parallel_frame.locked())
            with cpu_tone_batch.cpu_tone_batch():
                cpu_tone_batch.render(self.pixels, self.params)
            self.assertEqual(factory.call_count, 2)
            self.assertFalse(np.shares_memory(first, second))

    def test_export_or_another_batch_keeps_ownership_and_other_call_is_serial(self):
        cpu_chunks._parallel_frame.acquire()
        try:
            with patch.object(cpu_tone_batch, 'ThreadPoolExecutor', side_effect=AssertionError('second pool')), patch.object(
                compiled_tone, 'render', side_effect=self.fake
            ) as kernel, cpu_tone_batch.cpu_tone_batch():
                actual = cpu_tone_batch.render(self.pixels, self.params)
                self.assertEqual(kernel.call_count, 1)
                self.assertTrue(cpu_chunks._parallel_frame.locked())
            self.assertTrue(cpu_chunks._parallel_frame.locked())
            np.testing.assert_array_equal(actual, self.fake(self.pixels, self.params))
        finally:
            cpu_chunks._parallel_frame.release()

    def test_simultaneous_auto_calls_use_one_pool_and_keep_independent_frames(self):
        active, release = Event(), Event()
        def render(pixels, params):
            if len(pixels) == 1:
                active.set()
                if not release.wait(5):
                    raise TimeoutError('second caller did not render')
            return self.fake(pixels, params)

        def first():
            with cpu_tone_batch.cpu_tone_batch():
                return cpu_tone_batch.render(self.pixels, self.params)

        with patch.object(compiled_tone, 'render', side_effect=render), patch.object(
            cpu_tone_batch, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor
        ) as factory, ThreadPoolExecutor(max_workers=1) as callers:
            task = callers.submit(first)
            try:
                self.assertTrue(active.wait(5))
                with cpu_tone_batch.cpu_tone_batch():
                    second = cpu_tone_batch.render(self.pixels + 1, self.params)
            finally:
                release.set()
            actual = task.result(timeout=5)
            self.assertEqual(factory.call_count, 1)
        np.testing.assert_array_equal(actual, self.fake(self.pixels, self.params))
        np.testing.assert_array_equal(second, self.fake(self.pixels + 1, self.params))
        self.assertFalse(np.shares_memory(actual, second))

    def test_worker_setting_and_small_cpu_limit_remain_effective_in_batch(self):
        for value, processors, expected in (('2',8,2), ('99',8,4), ('invalid',8,4), ('4',2,1)):
            with self.subTest(value=value, processors=processors), patch.dict(os.environ, {'OPENRAW_CPU_WORKERS':value}), patch.object(
                cpu_chunks.os, 'cpu_count', return_value=processors
            ), patch.object(cpu_tone_batch, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor) as factory, patch.object(
                compiled_tone, 'render', side_effect=self.fake
            ), cpu_tone_batch.cpu_tone_batch():
                cpu_tone_batch.render(self.pixels, self.params)
                if expected == 1:
                    factory.assert_not_called()
                else:
                    self.assertEqual(factory.call_args.kwargs['max_workers'], expected)

    def test_thread_creation_failure_and_partial_submission_return_complete_serial_frame(self):
        class PartialPool(ThreadPoolExecutor):
            def submit(self, *args, **kwargs):
                if getattr(self, 'submitted', False):
                    raise RuntimeError('cannot start another thread')
                self.submitted = True
                return super().submit(*args, **kwargs)

        for factory in (Mock(side_effect=OSError('no threads')), PartialPool):
            with self.subTest(factory=factory), patch.object(cpu_tone_batch, 'ThreadPoolExecutor', factory), patch.object(
                compiled_tone, 'render', side_effect=self.fake
            ), cpu_tone_batch.cpu_tone_batch():
                actual = cpu_tone_batch.render(self.pixels, self.params)
                np.testing.assert_array_equal(actual, self.fake(self.pixels, self.params))
                self.assertFalse(cpu_chunks._parallel_frame.locked())
                np.testing.assert_array_equal(cpu_tone_batch.render(self.pixels, self.params), actual)

    def test_kernel_failure_before_or_after_warmup_never_leaks_partial_pixels(self):
        parent = get_ident()
        def late(pixels, params):
            return self.fake(pixels, params) if get_ident() == parent else None

        for callback in (lambda *_: None, late):
            with self.subTest(callback=callback), patch.object(compiled_tone, 'render', side_effect=callback), cpu_tone_batch.cpu_tone_batch():
                actual = cpu_tone_batch.render(self.pixels, self.params)
                if callback is late:
                    np.testing.assert_array_equal(actual, self.fake(self.pixels, self.params))
                else:
                    self.assertIsNone(actual)
                self.assertFalse(cpu_chunks._parallel_frame.locked())

    def test_worker_data_error_is_not_hidden_as_compiler_or_pool_failure(self):
        def broken(pixels, params):
            if len(pixels) != 1:
                raise ValueError('bad pixels')
            return self.fake(pixels, params)

        with patch.object(compiled_tone, 'render', side_effect=broken), self.assertRaisesRegex(ValueError, 'bad pixels'):
            with cpu_tone_batch.cpu_tone_batch():
                cpu_tone_batch.render(self.pixels, self.params)

    def test_error_in_unreported_queued_task_survives_submission_failure(self):
        class QueuedFailure:
            def __init__(self, **_kwargs):
                pass
            def submit(self, function, *args):
                try:
                    function(*args)
                except ValueError:
                    pass
                raise RuntimeError('submission failed after enqueue')
            def shutdown(self, wait):
                pass

        def broken(pixels, params):
            if len(pixels) > 1:
                raise ValueError('pixel failure')
            return self.fake(pixels, params)

        with patch.object(cpu_tone_batch, 'ThreadPoolExecutor', QueuedFailure), patch.object(compiled_tone, 'render', side_effect=broken):
            with self.assertRaisesRegex(ValueError, 'pixel failure'), cpu_tone_batch.cpu_tone_batch():
                cpu_tone_batch.render(self.pixels, self.params)

    def test_gpu_bypasses_cpu_pool_and_compiler_failure_uses_reference(self):
        gpu = Mock()
        gpu.name = 'test'
        gpu.tone.return_value = np.zeros(self.pixels.shape, np.uint8)
        with cpu_tone_batch.cpu_tone_batch(), patch.object(cpu_tone_batch, 'ThreadPoolExecutor', side_effect=AssertionError('CPU pool')):
            with patch.object(acceleration, 'get_gpu', return_value=gpu):
                self.assertEqual(acceleration.render_tone(self.pixels, self.params)[1], 'GPU: test')
            with patch.object(acceleration, 'get_gpu', return_value=None), patch.object(compiled_tone, 'render', return_value=None):
                image, backend = acceleration.render_tone(self.pixels, self.params)
                np.testing.assert_array_equal(image, acceleration.tone_cpu(self.pixels, self.params))
                self.assertEqual(backend, 'CPU')

    def test_broken_gpu_falls_back_to_exact_cpu_batch_result(self):
        reference = acceleration.render_tone(self.pixels, self.params)[0]
        gpu = Mock()
        gpu.tone.side_effect = RuntimeError('device lost')
        with patch.object(acceleration, 'get_gpu', return_value=gpu), patch.object(acceleration, 'disable_gpu') as disable:
            with cpu_tone_batch.cpu_tone_batch():
                actual, backend = acceleration.render_tone(self.pixels, self.params)
        disable.assert_called_once()
        self.assertEqual(backend, 'CPU')
        np.testing.assert_array_equal(actual, reference)

    def test_real_pixel_outputs_match_with_color_tone_orientation_and_strided_input(self):
        pixels = self.pixels[::2, ::2].copy()[::2, ::2]
        original = pixels.copy()
        pixels.flags.writeable = False
        for orientation in (1, 3, 6, 8):
            for linear in (False, True):
                photo = InteractivePhoto(pixels, np.array([[1.2,-.1,-.1],[-.1,1.1,0],[0,-.2,1.2]], np.float32),
                                         (1.8,1,1.4), orientation=orientation, linear_saturation=linear)
                for edits in ({}, dict(exposure=.4, contrast=.2, highlights=-.3, shadows=.6, warmth=.12, tint=-.05, saturation=.1)):
                    reference = photo.render(edits)[0]
                    with cpu_tone_batch.cpu_tone_batch():
                        actual = photo.render(edits)[0]
                    self.assertEqual(actual.tobytes(), reference.tobytes())
                    self.assertEqual(actual.size, reference.size)
        np.testing.assert_array_equal(pixels, original)
        self.assertFalse(pixels.flags.writeable)
