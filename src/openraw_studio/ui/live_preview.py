"""One preview worker with a replaceable pending request and stale-frame rejection."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from openraw_studio.raw.native.detail import prepare_detail_photo
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.ui.viewport import DetailView
from openraw_studio.core.subject import validate_subject_source


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
    detail_view: DetailView | None = None
    native_size: tuple[int, int] | None = None
    region: tuple[int, int, int, int] | None = None


class LivePreviewWorker:
    def __init__(self, processor, *, prepare=prepare_interactive_photo, prepare_detail=prepare_detail_photo):
        self.processor = processor
        self.prepare = prepare
        self.prepare_detail = prepare_detail
        self._condition = threading.Condition()
        self._revision = 0
        self._generation = 0
        self._source = None
        self._detail_mode = False
        self._pending = None
        self._completed = None
        self._prepared = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="openraw-live-preview"
        )
        self._thread.start()

    def submit(self, source, adjustments, *, detail_view=None):
        with self._condition:
            source = Path(source)
            detail_mode = detail_view is not None
            if source != self._source or detail_mode != self._detail_mode:
                self._generation += 1
                self._completed = None
                if source != self._source:
                    self._prepared = None
                self._source = source
                self._detail_mode = detail_mode
            self._revision += 1
            self._pending = (
                self._revision,
                source,
                deepcopy(adjustments),
                perf_counter(),
                self._generation,
                detail_view,
            )
            self._condition.notify()
            return self._revision

    def invalidate(self):
        with self._condition:
            self._revision += 1
            self._generation += 1
            self._pending = self._completed = None
            self._prepared = None

    def get_prepared_photo(self, source):
        """Share the unedited proxy read-only; never reuse a changed source."""
        try:
            source = Path(source)
            stat = source.stat()
            key = (source.resolve(), stat.st_size, stat.st_mtime_ns)
        except (OSError, RuntimeError):
            return None
        with self._condition:
            if not self._closed and self._prepared is not None and self._prepared[0] == key:
                return self._prepared[1]
        return None

    def take(self):
        with self._condition:
            frame, self._completed = self._completed, None
            return frame

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = self._completed = None
            self._prepared = None
            self._condition.notify()

    def _run(self):
        key = photo = original = None
        detail_photo = detail_original = detail_region = None
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed or self._pending is not None
                )
                if self._closed:
                    return
                revision, source, adjustments, requested_at, generation, detail_view = self._pending
                self._pending = None
            try:
                validate_subject_source(adjustments.get("subject"), source)
                stat = source.stat()
                current_key = (source.resolve(), stat.st_size, stat.st_mtime_ns)
                if current_key != key:
                    if source.suffix.lower() in {".nef", ".nrw"}:
                        self._show_camera_reference(
                            revision, source, adjustments, requested_at, generation
                        )
                    with self._condition:
                        if generation != self._generation or self._closed:
                            continue
                    photo = self.prepare(self.processor, source)
                    original, _backend = photo.render({})
                    key = current_key
                    detail_photo = detail_original = detail_region = None
                with self._condition:
                    if generation == self._generation and not self._closed:
                        self._prepared = (key, photo)
                    if revision != self._revision or self._closed:
                        continue
                if detail_view is not None:
                    if detail_photo is None:
                        detail_photo = self.prepare_detail(self.processor, source)
                    region = detail_view.region(detail_photo.size)
                    if region != detail_region:
                        detail_original = detail_photo.render_region({}, region)
                        detail_region = region
                    image = (
                        detail_photo.render_region(adjustments, region)
                        if any(adjustments.values()) else detail_original
                    )
                    backend = "Native detail"
                else:
                    image, backend = photo.render(adjustments)
                frame = LiveFrame(
                    revision,
                    source,
                    adjustments,
                    image,
                    backend,
                    (perf_counter() - requested_at) * 1000,
                    original_image=detail_original if detail_view is not None else original,
                    detail_view=detail_view,
                    native_size=detail_photo.size if detail_view is not None else None,
                    region=region if detail_view is not None else None,
                )
            except Exception as exc:  # noqa: BLE001 - Report worker failures to the UI.
                frame = LiveFrame(revision, source, adjustments, error=str(exc), detail_view=detail_view)
            with self._condition:
                # A finished frame is useful during a drag even if a newer edit is
                # pending. Source changes/invalidation still reject all old work.
                if generation == self._generation and not self._closed:
                    self._completed = frame

    def _show_camera_reference(self, revision, source, adjustments, requested_at, generation):
        from io import BytesIO

        from PIL import Image

        from openraw_studio.raw.native.dng import DngMetadataReader
        from openraw_studio.raw.native.nikon import _apply_exif_orientation

        try:
            embedded = DngMetadataReader().read_embedded_jpeg_preview(source)
            with Image.open(BytesIO(embedded.data)) as opened:
                # This temporary Fit reference only needs roughly twice the
                # display resolution before allocating decoded RGB pixels.
                scale = min(1, 1920 / max(opened.size))
                opened.draft("RGB", tuple(max(1, round(side * scale)) for side in opened.size))
                image = opened.convert("RGB")
            image = _apply_exif_orientation(image, embedded.orientation)
            image.thumbnail((960, 960))
        except (OSError, ValueError, Image.DecompressionBombError):
            return
        with self._condition:
            if generation == self._generation and not self._closed:
                self._completed = LiveFrame(
                    revision,
                    source,
                    adjustments,
                    image,
                    "Camera Preview",
                    (perf_counter() - requested_at) * 1000,
                    reference=True,
                )
