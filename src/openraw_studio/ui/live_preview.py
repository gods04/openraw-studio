"""One preview worker with a replaceable pending request and stale-frame rejection."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from openraw_studio.raw.native.interactive import prepare_interactive_photo


@dataclass(frozen=True)
class LiveFrame:
    revision: int
    source: Path
    adjustments: dict
    image: object = None
    backend: str = "CPU"
    elapsed_ms: float = 0
    error: str | None = None
    reference: bool = False
    original_image: object = None


class LivePreviewWorker:
    def __init__(self, processor, *, prepare=prepare_interactive_photo):
        self.processor = processor
        self.prepare = prepare
        self._condition = threading.Condition()
        self._revision = 0
        self._generation = 0
        self._source = None
        self._pending = None
        self._completed = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="openraw-live-preview"
        )
        self._thread.start()

    def submit(self, source, adjustments):
        with self._condition:
            source = Path(source)
            if source != self._source:
                self._generation += 1
                self._completed = None
                self._source = source
            self._revision += 1
            self._pending = (
                self._revision,
                source,
                dict(adjustments),
                perf_counter(),
                self._generation,
            )
            self._condition.notify()
            return self._revision

    def invalidate(self):
        with self._condition:
            self._revision += 1
            self._generation += 1
            self._pending = self._completed = None

    def take(self):
        with self._condition:
            frame, self._completed = self._completed, None
            return frame

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = self._completed = None
            self._condition.notify()

    def _run(self):
        key = photo = original = None
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed or self._pending is not None
                )
                if self._closed:
                    return
                revision, source, adjustments, requested_at, generation = self._pending
                self._pending = None
            try:
                stat = source.stat()
                current_key = (source.resolve(), stat.st_size, stat.st_mtime_ns)
                if current_key != key:
                    if source.suffix.lower() in {".nef", ".nrw"}:
                        self._show_camera_reference(
                            revision, source, adjustments, requested_at
                        )
                    photo = self.prepare(self.processor, source)
                    original, _backend = photo.render({})
                    key = current_key
                with self._condition:
                    if revision != self._revision or self._closed:
                        continue
                image, backend = photo.render(adjustments)
                frame = LiveFrame(
                    revision,
                    source,
                    adjustments,
                    image,
                    backend,
                    (perf_counter() - requested_at) * 1000,
                    original_image=original,
                )
            except Exception as exc:
                frame = LiveFrame(revision, source, adjustments, error=str(exc))
            with self._condition:
                # A finished frame is useful during a drag even if a newer edit is
                # pending. Source changes/invalidation still reject all old work.
                if generation == self._generation and not self._closed:
                    self._completed = frame

    def _show_camera_reference(self, revision, source, adjustments, requested_at):
        from io import BytesIO

        from PIL import Image

        from openraw_studio.raw.native.dng import DngMetadataReader
        from openraw_studio.raw.native.nikon import _apply_exif_orientation

        try:
            embedded = DngMetadataReader().read_embedded_jpeg_preview(source)
            with Image.open(BytesIO(embedded.data)) as opened:
                image = opened.convert("RGB")
            orientation = DngMetadataReader().read(source).summary.get("orientation", 1)
            image = _apply_exif_orientation(image, orientation)
            image.thumbnail((960, 960))
        except (OSError, ValueError):
            return
        with self._condition:
            if revision == self._revision and not self._closed:
                self._completed = LiveFrame(
                    revision,
                    source,
                    adjustments,
                    image,
                    "Camera Preview",
                    (perf_counter() - requested_at) * 1000,
                    reference=True,
                )
