"""Reuse a bounded CPU pool during multi-candidate Auto rendering only."""

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from threading import local

import numpy as np

from openraw_studio.raw.native import cpu_chunks


MIN_BATCH_PIXELS = 200_000
_local = local()


class _ToneBatch:
    def __init__(self):
        self.pool = None
        self.owns_frame = False
        self.disabled = False

    def close(self):
        try:
            if self.pool is not None:
                self.pool.shutdown(wait=True)
        finally:
            self.pool = None
            if self.owns_frame:
                self.owns_frame = False
                cpu_chunks._parallel_frame.release()

    def render(self, pixels, params):
        from openraw_studio.raw.native import compiled_tone

        if self.disabled or pixels.ndim != 3 or pixels.shape[-1] != 3:
            return compiled_tone.render(pixels, params)
        height, width, _ = pixels.shape
        workers = cpu_chunks._worker_limit(
            height * width, min(4, height - 1), width * ((height + 3) // 4) * 15,
            minimum_pixels=MIN_BATCH_PIXELS,
        )
        if workers == 1:
            return compiled_tone.render(pixels, params)
        if not self.owns_frame:
            self.owns_frame = cpu_chunks._parallel_frame.acquire(blocking=False)
            if not self.owns_frame:
                return compiled_tone.render(pixels, params)
        # Compile/load the shared read-only signature before worker threads run.
        first = compiled_tone.render(pixels[:1], params)
        if first is None:
            self.disabled = True
            self.close()
            return None
        output = np.empty(pixels.shape, np.uint8)
        output[:1] = first
        edges = np.linspace(1, height, min(4, height - 1) + 1, dtype=int)
        errors = []

        def draw(start, end):
            try:
                result = compiled_tone.render(pixels[start:end], params)
                if result is None:
                    return False
                output[start:end] = result
                return True
            except BaseException as error:
                errors.append(error)
                raise

        futures = []
        submitted = True
        try:
            if self.pool is None:
                self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="openraw-auto")
            for start, end in zip(edges, edges[1:]):
                futures.append(self.pool.submit(draw, start, end))
        except (OSError, RuntimeError):
            submitted = False
        # A submission can fail after other strips have started. Join them and
        # surface pixel errors before retrying serially; never return partial RGB.
        wait(futures)
        if not submitted:
            self.disabled = True
            self.close()
        if errors:
            raise errors[0]
        completed = [future.result() for future in futures]
        if not submitted or not all(completed):
            self.disabled = True
            self.close()
            return compiled_tone.render(pixels, params)
        return output


@contextmanager
def cpu_tone_batch():
    """Keep one optional pool per Auto call; nested calls share the outer scope."""
    if getattr(_local, "batch", None) is not None:
        yield
        return
    batch = _ToneBatch()
    _local.batch = batch
    try:
        yield
    finally:
        try:
            batch.close()
        finally:
            del _local.batch


def render(pixels, params):
    from openraw_studio.raw.native import compiled_tone

    batch = getattr(_local, "batch", None)
    return compiled_tone.render(pixels, params) if batch is None else batch.render(pixels, params)
