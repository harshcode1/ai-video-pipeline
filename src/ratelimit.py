"""A small thread-safe rate limiter: at most one request per `min_interval` seconds,
no matter how many threads ask or how many provider objects exist.

Each caller reserves the next free time slot while holding a lock, then sleeps
OUTSIDE the lock until its slot arrives. So concurrent callers queue up one
interval apart, and a sleeping caller never blocks others from reserving.
"""
import threading
import time


class RateLimiter:
    def __init__(self, min_interval):
        """min_interval: seconds, or a zero-argument callable returning seconds
        (read on every call, so changing the setting takes effect immediately)."""
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def _interval(self) -> float:
        return float(self._min_interval() if callable(self._min_interval) else self._min_interval)

    def wait(self) -> None:
        """Block until this caller's slot arrives (immediately if none is queued)."""
        interval = self._interval()
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)

    def reset(self) -> None:
        """Forget queued slots (used by tests)."""
        with self._lock:
            self._next_slot = 0.0
