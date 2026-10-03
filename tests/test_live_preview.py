import tempfile
import threading
import time
import unittest
from pathlib import Path

from openraw_studio.ui.live_preview import LivePreviewWorker


class LivePreviewWorkerTests(unittest.TestCase):
    def wait_for_frame(self, worker):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = worker.take()
            if frame is not None:
                return frame
            time.sleep(0.005)
        self.fail("Preview worker did not produce a frame")

    def test_inflight_frame_survives_new_edit_but_not_source_change(self):
        entered = threading.Event()
        release = threading.Event()
        next_entered = threading.Event()
        next_release = threading.Event()

        class Photo:
            def render(self, adjustments):
                if not adjustments:
                    return "original", "CPU"
                first = adjustments["exposure"] == 0
                (entered if first else next_entered).set()
                (release if first else next_release).wait(3)
                return adjustments["exposure"], "CPU"

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "one.DNG"
            other = Path(temp) / "two.DNG"
            source.write_bytes(b"one")
            other.write_bytes(b"two")
            worker = LivePreviewWorker(None, prepare=lambda *_: Photo())
            try:
                first_revision = worker.submit(source, {"exposure": 0})
                self.assertTrue(entered.wait(2))
                self.assertIsNotNone(worker.get_prepared_photo(source))
                worker.submit(source, {"exposure": 1})
                release.set()
                self.assertTrue(next_entered.wait(2))
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.revision, first_revision)
                self.assertEqual(frame.image, 0)
                worker.invalidate()
                self.assertIsNone(worker.get_prepared_photo(source))
                latest = worker.submit(other, {"exposure": 2})
                next_release.set()
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.revision, latest)
                self.assertEqual(frame.source, other)
                self.assertEqual(frame.image, 2)
                self.assertIsNone(worker.get_prepared_photo(source))
                self.assertIsNotNone(worker.get_prepared_photo(other))
            finally:
                release.set()
                next_release.set()
                worker.close()
                worker._thread.join(3)
                self.assertFalse(worker._thread.is_alive())

    def test_only_latest_slider_request_is_displayed_and_proxy_is_reused(self):
        entered = threading.Event()
        release = threading.Event()
        prepared = []
        renders = []

        class Photo:
            def render(self, adjustments):
                if not adjustments:
                    return "original", "CPU"
                renders.append(adjustments["exposure"])
                return adjustments["exposure"], "CPU"

        def prepare(processor, source):
            prepared.append(source)
            entered.set()
            release.wait(3)
            return Photo()

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fake.DNG"
            source.write_bytes(b"test")
            worker = LivePreviewWorker(None, prepare=prepare)
            try:
                worker.submit(source, {"exposure": 0})
                self.assertTrue(entered.wait(2))
                self.assertIsNone(worker.get_prepared_photo(source))
                for value in range(1, 30):
                    revision = worker.submit(source, {"exposure": value})
                release.set()
                deadline = time.monotonic() + 3
                frame = None
                while frame is None and time.monotonic() < deadline:
                    frame = worker.take()
                    time.sleep(0.005)
                self.assertIsNotNone(frame)
                self.assertEqual(frame.revision, revision)
                self.assertEqual(frame.image, 29)
                self.assertEqual(frame.original_image, "original")
                self.assertEqual(renders, [29])
                self.assertEqual(prepared, [source])
                self.assertIsNotNone(worker.get_prepared_photo(source))
                worker.invalidate()
                self.assertIsNone(worker.take())
                self.assertIsNone(worker.get_prepared_photo(source))
            finally:
                release.set()
                worker.close()
                worker._thread.join(3)
                self.assertFalse(worker._thread.is_alive())

    def test_shared_proxy_rejects_changed_missing_and_closed_sources(self):
        class Photo:
            def render(self, values):
                return "original", "CPU"

        photo = Photo()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.DNG"
            source.write_bytes(b"before")
            worker = LivePreviewWorker(None, prepare=lambda *_: photo)
            try:
                worker.submit(source, {})
                self.wait_for_frame(worker)
                self.assertIs(worker.get_prepared_photo(source), photo)
                source.write_bytes(b"changed-size")
                self.assertIsNone(worker.get_prepared_photo(source))
                worker.submit(source, {})
                self.wait_for_frame(worker)
                self.assertIs(worker.get_prepared_photo(source), photo)
                self.assertIsNone(worker.get_prepared_photo(Path(temp) / "missing.DNG"))
                worker.close()
                self.assertIsNone(worker.get_prepared_photo(source))
            finally:
                worker.close()
                worker._thread.join(3)

    def test_invalidated_inflight_preparation_cannot_publish_shared_proxy(self):
        entered, release = threading.Event(), threading.Event()

        class Photo:
            def render(self, values):
                return "original", "CPU"

        def prepare(*_):
            entered.set()
            release.wait(3)
            return Photo()

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.DNG"
            source.write_bytes(b"sample")
            worker = LivePreviewWorker(None, prepare=prepare)
            try:
                worker.submit(source, {})
                self.assertTrue(entered.wait(2))
                worker.invalidate()
                release.set()
                # Queue a sentinel source so its completion proves the stale
                # preparation has finished without timing-dependent sleeps.
                other = Path(temp) / "missing.DNG"
                worker.submit(other, {})
                self.wait_for_frame(worker)
                self.assertIsNone(worker.get_prepared_photo(source))
            finally:
                release.set()
                worker.close()
                worker._thread.join(3)
