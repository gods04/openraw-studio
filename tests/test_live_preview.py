import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fixtures_nikon import embedded_jpeg_bytes, synthetic_nikon_nef_metadata_bytes
from openraw_studio.raw.native.dng import DngMetadataReader

from openraw_studio.ui.live_preview import LivePreviewWorker
from openraw_studio.ui.viewport import DetailView


class LivePreviewWorkerTests(unittest.TestCase):
    def test_large_camera_reference_uses_reduced_jpeg_decode_and_correct_orientation(self):
        entered, release = threading.Event(), threading.Event()
        converted_sizes = []
        convert = Image.Image.convert

        class Photo:
            def render(self, values):
                return "native", "CPU"

        def prepare(*_):
            entered.set()
            release.wait(3)
            return Photo()

        def tracked(image, *args, **kwargs):
            if image.format == "JPEG":
                converted_sizes.append(image.size)
            return convert(image, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.NEF"
            original = synthetic_nikon_nef_metadata_bytes(
                embedded_jpeg=embedded_jpeg_bytes(4800, 3200), orientation=6,
            )
            source.write_bytes(original)
            with patch.object(Image.Image, "convert", tracked):
                worker = LivePreviewWorker(None, prepare=prepare)
                try:
                    worker.submit(source, {})
                    self.assertTrue(entered.wait(2))
                    frame = self.wait_for_frame(worker)
                    self.assertTrue(frame.reference)
                    self.assertEqual(frame.image.size, (640, 960))
                    self.assertEqual(converted_sizes, [(2400, 1600)])
                    self.assertEqual(source.read_bytes(), original)
                    release.set()
                    self.assertEqual(self.wait_for_frame(worker).image, "native")
                finally:
                    release.set()
                    worker.close()
                    worker._thread.join(3)

    def test_unreadable_or_oversized_camera_jpeg_does_not_block_native_raw(self):
        class Photo:
            def render(self, values):
                return "native", "CPU"

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.NEF"
            for jpeg, pixel_limit in ((b"\xff\xd8invalid", None), (embedded_jpeg_bytes(), 1)):
                source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=jpeg))
                with patch.object(Image, "MAX_IMAGE_PIXELS", pixel_limit):
                    worker = LivePreviewWorker(None, prepare=lambda *_: Photo())
                    try:
                        worker.submit(source, {})
                        frame = self.wait_for_frame(worker)
                        self.assertEqual(frame.image, "native")
                        self.assertIsNone(frame.error)
                        self.assertFalse(frame.reference)
                    finally:
                        worker.close()
                        worker._thread.join(3)

    def test_slider_edit_during_camera_read_does_not_discard_first_picture(self):
        reading, allow_read = threading.Event(), threading.Event()
        preparing, allow_prepare = threading.Event(), threading.Event()
        reader = DngMetadataReader.read_embedded_jpeg_preview

        def read(instance, source):
            reading.set()
            allow_read.wait(3)
            return reader(instance, source)

        class Photo:
            def render(self, values):
                return Image.new("RGB", (3, 2)), "CPU"

        def prepare(*_):
            preparing.set()
            allow_prepare.wait(3)
            return Photo()

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes()))
            with patch.object(DngMetadataReader, "read_embedded_jpeg_preview", read):
                worker = LivePreviewWorker(None, prepare=prepare)
                try:
                    first_revision = worker.submit(source, {})
                    self.assertTrue(reading.wait(2))
                    latest = worker.submit(source, {"exposure": 1})
                    allow_read.set()
                    self.assertTrue(preparing.wait(2))
                    first = self.wait_for_frame(worker)
                    self.assertTrue(first.reference)
                    self.assertEqual(first.revision, first_revision)
                    allow_prepare.set()
                    final = self.wait_for_frame(worker)
                    self.assertFalse(final.reference)
                    self.assertEqual(final.revision, latest)
                finally:
                    allow_read.set()
                    allow_prepare.set()
                    worker.close()
                    worker._thread.join(3)

    def test_source_switch_during_camera_read_skips_stale_raw_preparation(self):
        reading, allow_read = threading.Event(), threading.Event()
        preparing, allow_prepare = threading.Event(), threading.Event()
        reader = DngMetadataReader.read_embedded_jpeg_preview
        prepared = []

        def read(instance, source):
            if source.name == "first.NEF":
                reading.set()
                allow_read.wait(3)
            return reader(instance, source)

        class Photo:
            def render(self, values):
                return Image.new("RGB", (3, 2)), "CPU"

        def prepare(processor, source):
            prepared.append(source)
            preparing.set()
            allow_prepare.wait(3)
            return Photo()

        with tempfile.TemporaryDirectory() as folder:
            first, second = (Path(folder) / name for name in ("first.NEF", "second.NEF"))
            for source in (first, second):
                source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes()))
            with patch.object(DngMetadataReader, "read_embedded_jpeg_preview", read):
                worker = LivePreviewWorker(None, prepare=prepare)
                try:
                    worker.submit(first, {})
                    self.assertTrue(reading.wait(2))
                    latest = worker.submit(second, {})
                    allow_read.set()
                    self.assertTrue(preparing.wait(2))
                    frame = self.wait_for_frame(worker)
                    self.assertTrue(frame.reference)
                    self.assertEqual(frame.source, second)
                    self.assertEqual(frame.revision, latest)
                    self.assertEqual(prepared, [second])
                    allow_prepare.set()
                    self.assertEqual(self.wait_for_frame(worker).source, second)
                finally:
                    allow_read.set()
                    allow_prepare.set()
                    worker.close()
                    worker._thread.join(3)

    def test_camera_reference_precedes_raw_and_uses_one_oriented_preview_read(self):
        entered, release = threading.Event(), threading.Event()

        class Photo:
            def render(self, edits):
                return Image.new("RGB", (2, 3), "red"), "CPU"

        def prepare(*_):
            entered.set()
            release.wait(3)
            return Photo()

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(
                embedded_jpeg=embedded_jpeg_bytes(), orientation=6,
            ))
            with patch("openraw_studio.raw.native.dng.DngMetadataReader.read", side_effect=AssertionError("Repeated metadata read")):
                worker = LivePreviewWorker(None, prepare=prepare)
                try:
                    revision = worker.submit(source, {"exposure": 1})
                    self.assertTrue(entered.wait(2))
                    first = self.wait_for_frame(worker)
                    self.assertTrue(first.reference)
                    self.assertEqual(first.backend, "Camera Preview")
                    self.assertEqual(first.image.size, (2, 3))
                    self.assertEqual(first.revision, revision)
                    self.assertIsNone(worker.get_prepared_photo(source))
                    release.set()
                    final = self.wait_for_frame(worker)
                    self.assertFalse(final.reference)
                    self.assertEqual(final.image.getpixel((0, 0)), (255, 0, 0))
                    self.assertEqual(final.adjustments, {"exposure": 1})
                finally:
                    release.set()
                    worker.close()
                    worker._thread.join(3)

    def test_inflight_detail_cannot_replace_new_fit_view(self):
        entered, release = threading.Event(), threading.Event()

        class Photo:
            def render(self, values):
                return "fit", "CPU"

        class Detail:
            size = (6000, 4000)

            def render_region(self, values, region):
                entered.set()
                release.wait(3)
                return "detail"

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.DNG"
            source.write_bytes(b"sample")
            worker = LivePreviewWorker(None, prepare=lambda *_: Photo(), prepare_detail=lambda *_: Detail())
            try:
                worker.submit(source, {}, detail_view=DetailView((800, 600)))
                self.assertTrue(entered.wait(2))
                latest = worker.submit(source, {})
                release.set()
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.revision, latest)
                self.assertEqual(frame.image, "fit")
                self.assertIsNone(frame.detail_view)
            finally:
                release.set()
                worker.close()
                worker._thread.join(3)

    def test_detail_frames_reuse_original_region_and_leave_fit_proxy_available(self):
        calls, prepared = [], []

        class Photo:
            def render(self, edits):
                return "fit", "CPU"

        class Detail:
            size = (6000, 4000)

            def render_region(self, edits, region):
                calls.append((dict(edits), region))
                return (edits.get("exposure", 0), region)

        def prepare_detail(*_):
            prepared.append(True)
            return Detail()

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.DNG"
            source.write_bytes(b"sample")
            worker = LivePreviewWorker(None, prepare=lambda *_: Photo(), prepare_detail=prepare_detail)
            try:
                view = DetailView((800, 600))
                worker.submit(source, {"exposure": 1}, detail_view=view)
                first = self.wait_for_frame(worker)
                self.assertEqual(first.region, (2600, 1700, 800, 600))
                self.assertEqual(first.native_size, (6000, 4000))
                self.assertEqual(first.detail_view, view)
                self.assertIsNotNone(worker.get_prepared_photo(source))
                worker.submit(source, {"exposure": 2}, detail_view=view)
                second = self.wait_for_frame(worker)
                self.assertIs(first.original_image, second.original_image)
                self.assertEqual(len(calls), 3)
                self.assertEqual(len(prepared), 1)
                worker.submit(source, {"exposure": 2})
                self.assertIsNone(self.wait_for_frame(worker).detail_view)
            finally:
                worker.close()
                worker._thread.join(3)

    def test_detail_failure_keeps_request_identity_and_fit_recovers(self):
        class Photo:
            def render(self, edits):
                return "fit", "CPU"

        def fail(*_):
            raise ValueError("unsupported detail")

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.DNG"
            source.write_bytes(b"sample")
            worker = LivePreviewWorker(None, prepare=lambda *_: Photo(), prepare_detail=fail)
            try:
                view = DetailView((800, 600))
                worker.submit(source, {}, detail_view=view)
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.detail_view, view)
                self.assertEqual(frame.error, "unsupported detail")
                worker.submit(source, {})
                frame = self.wait_for_frame(worker)
                self.assertEqual(frame.image, "fit")
                self.assertIsNone(frame.error)
            finally:
                worker.close()
                worker._thread.join(3)

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
