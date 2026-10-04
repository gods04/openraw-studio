"""Bounded scheduling for independent, GIL-releasing native image strips."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event, Lock

MIN_PARALLEL_PIXELS = 2_000_000
MAX_SCRATCH_BYTES = 128 * 1024 * 1024
_parallel_frame = Lock()


def _worker_limit(pixel_count, strips, scratch_bytes, *, minimum_pixels=None):
    minimum_pixels = MIN_PARALLEL_PIXELS if minimum_pixels is None else minimum_pixels
    if pixel_count < minimum_pixels or strips < 2:
        return 1
    processors = max(1, (os.cpu_count() or 1) - 1)
    try:
        requested = int(os.environ.get("OPENRAW_CPU_WORKERS", "4"))
    except ValueError:
        requested = 4
    return max(1, min(4, requested, processors, strips, MAX_SCRATCH_BYTES // max(1, scratch_bytes)))


def render_chunks(render, *, height, width, chunk_rows, scratch_bytes):
    """Render disjoint strips; callback returns whether compiled kernels worked.

    Inputs must remain read-only and each callback writes only its output rows.
    Warm the first strip before sharing compiled kernels. Previews and reference
    fallback stay serial; at most one large frame per process owns a worker pool.
    """
    if min(height, width, chunk_rows, scratch_bytes) < 1:
        raise ValueError("CPU strip dimensions and scratch estimate must be positive")
    compiled = render(0, min(height, chunk_rows))
    starts = range(chunk_rows, height, chunk_rows)
    workers = _worker_limit(height * width, len(starts), scratch_bytes) if compiled else 1
    if workers == 1 or not _parallel_frame.acquire(blocking=False):
        for start in starts:
            render(start, min(height, start + chunk_rows))
        return

    completed = [False] * len(starts)
    stop = Event()
    errors = []

    def lane(index):
        try:
            for position in range(index, len(starts), workers):
                if stop.is_set():
                    break
                start = starts[position]
                compiled = render(start, min(height, start + chunk_rows))
                completed[position] = True
                if not compiled:
                    stop.set()
        except BaseException as error:
            errors.append(error)
            stop.set()
            raise

    pool, futures = None, []
    try:
        try:
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="openraw-cpu")
            for index in range(workers):
                futures.append(pool.submit(lane, index))
        except (OSError, RuntimeError):
            # Thread creation may fail after some lanes have already started.
            stop.set()
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
        if errors:
            raise errors[0]
        for future in futures:
            future.result()
        # A compiler or pool failure resumes only unfinished strips, serially.
        for position, start in enumerate(starts):
            if not completed[position]:
                render(start, min(height, start + chunk_rows))
    finally:
        _parallel_frame.release()
