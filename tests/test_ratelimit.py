"""The image provider limit (1 request per N seconds) must hold even when several
threads ask at once, and across provider instances: the limiter is shared."""
import threading
import time

import pytest

import config
import images
from images import FalProvider, GeminiProvider
from ratelimit import RateLimiter

INTERVAL = 0.06          # tiny, so real-time tests stay fast
EPS = 0.012              # scheduler jitter allowance


def start_times_when_n_threads_ask_at_once(limiter, n):
    starts = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()                        # everyone asks at the same moment
        limiter.wait()
        with lock:
            starts.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return sorted(starts)


def test_threads_asking_at_once_are_spaced_at_least_one_interval_apart():
    starts = start_times_when_n_threads_ask_at_once(RateLimiter(INTERVAL), 8)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 8
    assert min(gaps) >= INTERVAL - EPS, gaps
    assert starts[-1] - starts[0] >= 7 * INTERVAL - 7 * EPS      # not squeezed together


def test_the_first_request_goes_out_immediately():
    limiter = RateLimiter(5.0)
    t0 = time.monotonic()
    limiter.wait()
    assert time.monotonic() - t0 < 0.05


def test_sequential_requests_are_spaced_too():
    limiter = RateLimiter(INTERVAL)
    starts = []
    for _ in range(4):
        limiter.wait()
        starts.append(time.monotonic())
    assert all(b - a >= INTERVAL - EPS for a, b in zip(starts, starts[1:]))


def test_zero_interval_never_waits():
    limiter = RateLimiter(0.0)
    t0 = time.monotonic()
    for _ in range(50):
        limiter.wait()
    assert time.monotonic() - t0 < 0.2


def test_the_interval_can_be_supplied_as_a_callable_and_is_read_at_call_time():
    box = {"v": 0.0}
    limiter = RateLimiter(lambda: box["v"])
    limiter.wait()
    box["v"] = INTERVAL
    t0 = time.monotonic()
    limiter.wait()
    limiter.wait()
    assert time.monotonic() - t0 >= INTERVAL - EPS


def test_a_slow_waiter_does_not_hold_the_lock_so_other_threads_can_still_reserve_slots():
    limiter = RateLimiter(0.3)
    limiter.wait()                                    # use the free first slot
    results = []

    def asker():
        t0 = time.monotonic()
        limiter.wait()
        results.append(time.monotonic() - t0)

    threads = [threading.Thread(target=asker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    results.sort()
    assert results[0] == pytest.approx(0.3, abs=0.12)     # first asker waited ~1 interval
    assert results[1] == pytest.approx(0.6, abs=0.15)     # second ~2 (reserved while the first slept)


# ------------------------------------------------------------------ the providers share one limiter

def test_every_provider_instance_uses_the_same_shared_limiter():
    assert FalProvider(api_key="a")._limiter is GeminiProvider(api_key="b")._limiter
    assert FalProvider(api_key="a")._limiter is images.IMAGE_LIMITER


def test_the_shared_limiter_follows_the_configured_interval(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_MIN_INTERVAL_SECONDS", INTERVAL)
    limiter = images.IMAGE_LIMITER
    limiter.reset()
    starts = start_times_when_n_threads_ask_at_once(limiter, 3)
    assert min(b - a for a, b in zip(starts, starts[1:])) >= INTERVAL - EPS


def test_requests_from_two_providers_in_two_threads_never_exceed_the_limit(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_MIN_INTERVAL_SECONDS", INTERVAL)
    images.IMAGE_LIMITER.reset()
    sent = []
    lock = threading.Lock()

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"images": [{"url": "data:image/jpeg;base64,"}]}

    def fake_post(*a, **k):
        with lock:
            sent.append(time.monotonic())
        return FakeResponse()

    monkeypatch.setattr(images.requests, "post", fake_post)
    monkeypatch.setattr(images, "_extract_fal_image", lambda payload: "data:image/jpeg;base64,AAAA")
    monkeypatch.setattr(images.FalProvider, "_resolve_bytes", lambda self, url: b"x" * 6000)
    monkeypatch.setattr(images.GeminiProvider, "_check_response", lambda self, resp: None)
    monkeypatch.setattr(images, "_extract_image_bytes", lambda payload: b"x" * 6000)

    tmp_files = []

    def go(provider, name):
        import tempfile
        path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
        tmp_files.append(path)
        from pathlib import Path
        provider.fetch(f"prompt {name}", Path(path))

    jobs = [(FalProvider(api_key="a"), "f1"), (GeminiProvider(api_key="b"), "g1"),
            (FalProvider(api_key="a"), "f2"), (GeminiProvider(api_key="b"), "g2")]
    threads = [threading.Thread(target=go, args=j) for j in jobs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    sent.sort()
    assert len(sent) == 4
    assert min(b - a for a, b in zip(sent, sent[1:])) >= INTERVAL - EPS
