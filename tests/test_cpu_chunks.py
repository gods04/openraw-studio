from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Lock, current_thread, get_ident
import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.raw.native import cpu_chunks
from openraw_studio.raw.native.chroma import reduce_color_noise
from openraw_studio.raw.native.luminance import reduce_luminance_noise
from openraw_studio.raw.native.fullres import render_bayer_full_resolution


class CpuChunkSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "4"}))
        self.enterContext(patch.object(cpu_chunks.os, "cpu_count", return_value=8))
        self.enterContext(patch.object(cpu_chunks, "MIN_PARALLEL_PIXELS", 1))

    def render(self, callback, **kwargs):
        cpu_chunks.render_chunks(callback, height=19, width=23, chunk_rows=3, scratch_bytes=1024, **kwargs)

    def test_worker_limits_reserve_cpu_and_bound_scratch(self):
        self.assertEqual(cpu_chunks._worker_limit(100, 10, 1024), 4)
        with patch.object(cpu_chunks.os, "cpu_count", return_value=2):
            self.assertEqual(cpu_chunks._worker_limit(100, 10, 1024), 1)
        with patch.object(cpu_chunks.os, "cpu_count", return_value=None):
            self.assertEqual(cpu_chunks._worker_limit(100, 10, 1024), 1)
        self.assertEqual(cpu_chunks._worker_limit(100, 2, 1024), 2)
        self.assertEqual(cpu_chunks._worker_limit(100, 20, 70 * 1024 * 1024), 1)
        for value, expected in (("1", 1), ("0", 1), ("-1", 1), ("2", 2), ("99", 4), ("invalid", 4)):
            with self.subTest(value=value), patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": value}):
                self.assertEqual(cpu_chunks._worker_limit(100, 10, 1024), expected)

    def test_first_strip_warms_before_independent_lanes_and_rows_are_exactly_once(self):
        parent = get_ident()
        barrier = Barrier(4, timeout=5)
        seen, threads = [], set()
        lock = Lock()

        def render(start, end):
            if start == 0:
                self.assertEqual(get_ident(), parent)
            else:
                self.assertTrue(seen and seen[0] == (0, 3))
                with lock:
                    initial = get_ident() not in threads
                    threads.add(get_ident())
                if initial:
                    barrier.wait()
                self.assertTrue(current_thread().name.startswith("openraw-cpu"))
            with lock:
                seen.append((start, end))
            return True

        self.render(render)
        self.assertEqual(len(threads), 4)
        self.assertEqual(sorted(seen), [(i, min(19, i + 3)) for i in range(0, 19, 3)])
        self.assertFalse(cpu_chunks._parallel_frame.locked())

    def test_previews_fallback_and_explicit_serial_mode_do_not_create_threads(self):
        for compiled, threshold, workers in ((True, 2_000_000, "4"), (False, 1, "4"), (True, 1, "1")):
            with self.subTest(compiled=compiled, threshold=threshold, workers=workers), patch.object(
                cpu_chunks, "MIN_PARALLEL_PIXELS", threshold
            ), patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": workers}), patch.object(
                cpu_chunks, "ThreadPoolExecutor", side_effect=AssertionError("unexpected pool")
            ):
                seen = []
                self.render(lambda start, end: seen.append((start, end)) or compiled)
                self.assertEqual(seen, [(i, min(19, i + 3)) for i in range(0, 19, 3)])

    def test_another_large_frame_does_not_wait_or_create_another_pool(self):
        cpu_chunks._parallel_frame.acquire()
        try:
            with patch.object(cpu_chunks, "ThreadPoolExecutor", side_effect=AssertionError("nested pool")):
                self.render(lambda *_: True)
            self.assertTrue(cpu_chunks._parallel_frame.locked())
        finally:
            cpu_chunks._parallel_frame.release()

    def test_thread_creation_failure_retries_only_unfinished_strips(self):
        class PartiallyStarted(ThreadPoolExecutor):
            calls = 0

            def submit(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("cannot start another thread")
                return super().submit(*args, **kwargs)

        for factory in (PartiallyStarted, None):
            with self.subTest(factory=factory):
                seen = []
                context = patch.object(cpu_chunks, "ThreadPoolExecutor", factory) if factory else patch.object(
                    cpu_chunks, "ThreadPoolExecutor", side_effect=OSError("threads unavailable"))
                with context:
                    self.render(lambda start, end: seen.append((start, end)) or True)
                self.assertEqual(sorted(seen), [(i, min(19, i + 3)) for i in range(0, 19, 3)])
                self.assertFalse(cpu_chunks._parallel_frame.locked())

    def test_late_compiler_failure_finishes_remaining_strips_serially(self):
        parent = get_ident()
        seen = []

        def render(start, end):
            seen.append((start, end, get_ident()))
            return start == 0

        self.render(render)
        self.assertEqual(sorted((start, end) for start, end, _ in seen),
                         [(i, min(19, i + 3)) for i in range(0, 19, 3)])
        self.assertTrue(any(thread == parent for start, _, thread in seen if start != 0))

    def test_worker_errors_are_not_hidden_and_pool_is_released(self):
        def broken(start, _end):
            if start > 0:
                raise ValueError("invalid pixels")
            return True

        with self.assertRaisesRegex(ValueError, "invalid pixels"):
            self.render(broken)
        self.assertFalse(cpu_chunks._parallel_frame.locked())
        self.render(lambda *_: True)

    def test_invalid_dimensions_never_call_renderer(self):
        for key in ("height", "width", "chunk_rows", "scratch_bytes"):
            args = dict(height=19, width=23, chunk_rows=3, scratch_bytes=1024)
            args[key] = 0
            with self.subTest(key=key), self.assertRaises(ValueError):
                cpu_chunks.render_chunks(lambda *_: self.fail("should not render"), **args)

    def test_error_in_a_lane_queued_by_failed_submission_is_not_lost(self):
        class FailingSubmission:
            def __init__(self, **_kwargs):
                pass

            def submit(self, function, index):
                try:
                    function(index)
                except ValueError:
                    pass
                raise RuntimeError("thread startup failed after enqueue")

            def shutdown(self, wait):
                pass

        def render(start, end):
            if start:
                raise ValueError("pixel failure")
            return True

        with patch.object(cpu_chunks, "ThreadPoolExecutor", FailingSubmission), self.assertRaisesRegex(ValueError, "pixel failure"):
            self.render(render)
        self.assertFalse(cpu_chunks._parallel_frame.locked())


class CpuParallelPixelTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(cpu_chunks, "MIN_PARALLEL_PIXELS", 1))
        self.enterContext(patch.object(cpu_chunks.os, "cpu_count", return_value=8))

    def test_bayer_parallel_bytes_match_serial_for_both_precisions_and_methods(self):
        raw = np.random.default_rng(612).integers(0, 16384, (37, 49), dtype=np.uint16).tobytes()
        for pattern in ((0, 1, 1, 2), (1, 0, 2, 1), (1, 2, 0, 1), (2, 1, 1, 0)):
            for bits in (8, 16):
                for method in ("bilinear", "malvar"):
                    with self.subTest(pattern=pattern, bits=bits, method=method):
                        args = dict(source_width=49, source_height=37, crop=(1, 1, 45, 33),
                                    cfa_pattern=pattern, black_levels=(32, 48, 64, 80), white_level=16383,
                                    channel_gains=(1.6, 1, 1.3), camera_to_linear_srgb=None,
                                    highlights=-.3, shadows=.4, saturation=.2, demosaic=method,
                                    bit_depth=bits, chunk_rows=5, use_gpu=False)
                        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "1"}):
                            expected = render_bayer_full_resolution(raw, **args)
                        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "4"}):
                            actual = render_bayer_full_resolution(raw, **args)
                        self.assertEqual(actual, expected)

    def test_noise_parallel_bytes_match_serial_with_immutable_strided_input(self):
        for dtype in (np.uint8, np.uint16):
            pixels = np.random.default_rng(613).integers(0, np.iinfo(dtype).max + 1, (73, 99, 3), dtype=dtype)[::2, ::2]
            original = pixels.copy()
            pixels.flags.writeable = False
            for function in (reduce_luminance_noise, reduce_color_noise):
                for amount in (.01, .7, 1):
                    with self.subTest(dtype=dtype, function=function.__name__, amount=amount):
                        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "1"}):
                            expected = function(pixels, amount, use_gpu=False, chunk_rows=5)
                        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "4"}):
                            actual = function(pixels, amount, use_gpu=False, chunk_rows=5)
                        np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(pixels, original)
            self.assertFalse(pixels.flags.writeable)

    def test_reference_only_rendering_stays_serial(self):
        pixels = np.zeros((17, 23, 3), np.uint16)
        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "4"}), patch.object(
            cpu_chunks, "ThreadPoolExecutor", side_effect=AssertionError("reference pool")
        ):
            for function in (reduce_luminance_noise, reduce_color_noise):
                np.testing.assert_array_equal(function(pixels, .7, use_gpu=False, use_compiled=False, chunk_rows=3), pixels)

    def test_concurrent_callers_keep_separate_noise_outputs(self):
        pixels = np.random.default_rng(614).integers(0, 65536, (139, 191, 3), dtype=np.uint16)
        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "1"}):
            expected = [reduce_color_noise(pixels, amount, use_gpu=False, chunk_rows=17) for amount in (.2, .9)]
        with patch.dict(os.environ, {"OPENRAW_CPU_WORKERS": "4"}), ThreadPoolExecutor(max_workers=2) as callers:
            jobs = [callers.submit(reduce_color_noise, pixels, amount, use_gpu=False, chunk_rows=17) for amount in (.2, .9)]
            actual = [job.result(timeout=20) for job in jobs]
        self.assertFalse(np.shares_memory(*actual))
        for result, reference in zip(actual, expected):
            np.testing.assert_array_equal(result, reference)
        self.assertFalse(cpu_chunks._parallel_frame.locked())
