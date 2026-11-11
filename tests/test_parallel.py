"""run_pool: simple ThreadPoolExecutor wrapper with a worker cap, ordered results,
and fail-fast cancellation."""
import threading
import time

import pytest

import parallel


class Boom(RuntimeError):
    pass


def test_results_come_back_in_input_order_whatever_order_they_finish():
    def slow_first(i):
        time.sleep(0.05 if i == 0 else 0.0)
        return i * 10

    assert parallel.run_pool(list(range(6)), slow_first, workers=4) == [0, 10, 20, 30, 40, 50]


def test_every_item_is_processed_exactly_once():
    seen = []
    lock = threading.Lock()

    def fn(i):
        with lock:
            seen.append(i)

    parallel.run_pool(list(range(25)), fn, workers=4)
    assert sorted(seen) == list(range(25))


def test_empty_input_returns_an_empty_list():
    assert parallel.run_pool([], lambda x: x, workers=4) == []


def test_concurrency_never_exceeds_the_cap_and_does_reach_it():
    active = 0
    peak = 0
    lock = threading.Lock()

    def fn(i):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.04)
        with lock:
            active -= 1

    parallel.run_pool(list(range(16)), fn, workers=3)
    assert peak == 3


def test_one_worker_runs_items_one_at_a_time_in_order():
    order = []
    parallel.run_pool([1, 2, 3, 4], order.append, workers=1)
    assert order == [1, 2, 3, 4]


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_worker_count_is_rejected(bad):
    with pytest.raises(ValueError):
        parallel.run_pool([1], lambda x: x, workers=bad)


def test_the_first_failure_stops_pending_work_and_the_original_error_is_raised():
    started = []
    lock = threading.Lock()

    def fn(i):
        with lock:
            started.append(i)
        if i == 2:
            raise Boom("fatal on item 2")
        time.sleep(0.05)

    t0 = time.monotonic()
    with pytest.raises(Boom, match="fatal on item 2"):
        parallel.run_pool(list(range(40)), fn, workers=2)
    assert len(started) < 10, f"pending items kept starting after the failure: {started}"
    assert time.monotonic() - t0 < 1.5                          # did not grind through all 40


def test_run_pool_returns_only_after_in_flight_work_has_finished():
    running = threading.Event()
    finished = []

    def fn(i):
        if i == 0:
            running.set()
            time.sleep(0.15)                  # in flight while item 1 fails
            finished.append(i)
        else:
            running.wait()
            raise Boom("fail while item 0 is still running")

    with pytest.raises(Boom):
        parallel.run_pool([0, 1], fn, workers=2)
    assert finished == [0]                    # item 0 was allowed to finish cleanly


def test_a_failure_in_sequential_mode_also_stops_immediately():
    seen = []

    def fn(i):
        seen.append(i)
        if i == 1:
            raise Boom("stop")

    with pytest.raises(Boom):
        parallel.run_pool([0, 1, 2, 3], fn, workers=1)
    assert seen == [0, 1]


def test_default_clip_workers_is_about_half_the_cores(monkeypatch):
    for cores, expected in [(16, 8), (8, 4), (4, 2), (2, 1), (1, 1), (None, 1)]:
        monkeypatch.setattr(parallel.os, "cpu_count", lambda c=cores: c)
        assert parallel.default_clip_workers() == expected
