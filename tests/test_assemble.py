"""Plain-assert unit tests for the xfade offset math (no pytest dependency).
Run: python tests/test_assemble.py
A bug in compute_offsets cascades through all 69 joins in the real render,
so this must pass before assemble.py ever touches ffmpeg."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from assemble import compute_offsets, build_xfade_filter


def approx(a, b, eps=1e-9):
    return abs(a - b) < eps


def test_two_clips_equal_length():
    # two 6s clips (5s content + 1s pad), 1s fade
    offsets = compute_offsets([6.0, 6.0], fade=1.0)
    assert offsets == [5.0], offsets


def test_three_clips_equal_length():
    # three 6s clips, 1s fade -> total timeline = 6+6+6 - 2*1 = 16
    offsets = compute_offsets([6.0, 6.0, 6.0], fade=1.0)
    assert offsets == [5.0, 10.0], offsets


def test_matches_manual_formula_varying_lengths():
    durations = [3.5, 7.2, 2.1, 9.9, 4.0]
    fade = 1.0
    offsets = compute_offsets(durations, fade)

    # manual reconstruction: cumulative timeline position at start of each xfade
    cum = durations[0]
    expected = []
    for d in durations[1:]:
        expected.append(cum - fade)
        cum = cum + d - fade
    assert len(offsets) == len(durations) - 1
    for o, e in zip(offsets, expected):
        assert approx(o, e), (o, e)

    # total timeline length == sum(durations) - (n-1)*fade
    total = cum
    assert approx(total, sum(durations) - (len(durations) - 1) * fade)


def test_single_clip_no_offsets():
    assert compute_offsets([5.0], fade=1.0) == []


def test_offsets_are_monotonically_increasing():
    durations = [4.5, 6.0, 3.2, 8.8]
    offsets = compute_offsets(durations, fade=1.0)
    for a, b in zip(offsets, offsets[1:]):
        assert b > a, offsets


def test_offset_never_exceeds_next_clip_start_content():
    # sanity: offset for join i must be < cumulative length before that join
    # (i.e. the fade starts inside clip i's own timeline, not after it)
    durations = [5.0, 5.0, 5.0, 5.0]
    fade = 1.0
    offsets = compute_offsets(durations, fade)
    cum = durations[0]
    for i, off in enumerate(offsets):
        assert off < cum, (i, off, cum)
        cum = cum + durations[i + 1] - fade


def test_build_xfade_filter_five_clips_matches_offsets():
    durations = [6.0, 13.0, 6.0, 6.0, 6.0]  # mirrors the 1,2,5,34,65 checkpoint shape
    offsets = compute_offsets(durations, fade=1.0)
    filter_str, out_label = build_xfade_filter(durations, fade=1.0)
    assert out_label == "vout"
    parts = filter_str.split(";")
    assert len(parts) == 4
    for i, part in enumerate(parts):
        assert f"offset={offsets[i]:.3f}" in part, (part, offsets[i])
    assert parts[0].startswith("[0:v][1:v]xfade")
    assert parts[-1].endswith("[vout]")


def test_build_xfade_filter_single_clip():
    filter_str, out_label = build_xfade_filter([5.0], fade=1.0)
    assert filter_str is None
    assert out_label == "0:v"


def run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
