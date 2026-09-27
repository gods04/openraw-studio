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
                worker.submit(source, {"exposure": 1})
                release.set()
                self.assertTrue(next_entered.wait(2))
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.revision, first_revision)
                self.assertEqual(frame.image, 0)
                worker.invalidate()
                latest = worker.submit(other, {"exposure": 2})
                next_release.set()
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.revision, latest)
                self.assertEqual(frame.source, other)
                self.assertEqual(frame.image, 2)
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
                self.assertEqual(renders, [29])
                self.assertEqual(prepared, [source])
                worker.invalidate()
                self.assertIsNone(worker.take())
            finally:
                release.set()
                worker.close()
                worker._thread.join(3)
                self.assertFalse(worker._thread.is_alive())
