"""Run independent jobs on a small, capped thread pool.

Plain concurrent.futures -- no asyncio, no extra services. Used for work that is
safe to overlap: speech synthesis (network-bound, one request per line) and
per-scene ffmpeg clip renders (CPU-bound, each in its own process).

Behaviour that matters:
  * results come back in input order;
  * at most `workers` jobs run at once;
  * the FIRST failure stops the run: jobs that have not started are cancelled,
    jobs already running are allowed to finish (so nothing is left half-written;
    outputs are written atomically anyway), and the ORIGINAL exception is raised.
"""
import os
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait


def default_clip_workers() -> int:
    """Each ffmpeg render is already multi-threaded, so about half the cores keeps
    the machine busy without oversubscribing it."""
    return max(1, (os.cpu_count() or 1) // 2)


def run_pool(items: list, fn, workers: int) -> list:
    """Return [fn(item) for item in items], running up to `workers` at a time."""
    if workers < 1:
        raise ValueError(f"workers must be at least 1, got {workers}")
    items = list(items)
    if not items:
        return []

    if workers == 1 or len(items) == 1:
        return [fn(item) for item in items]              # plain sequential, same fail-fast

    executor = ThreadPoolExecutor(max_workers=min(workers, len(items)))
    try:
        futures = [executor.submit(fn, item) for item in items]
        wait(futures, return_when=FIRST_EXCEPTION)
        for future in futures:                           # earliest-submitted failure wins
            if future.done() and not future.cancelled() and future.exception() is not None:
                raise future.exception()
        return [future.result() for future in futures]
    finally:
        # Cancel anything not yet started; wait for what is running to finish.
        executor.shutdown(wait=True, cancel_futures=True)
