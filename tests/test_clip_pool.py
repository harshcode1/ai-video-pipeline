"""Per-scene clip rendering on a capped pool (real ffmpeg, tiny clips)."""
import threading

import pytest

import assemble
import config
import parallel
import render
from conftest import make_jpeg, probe_video, requires_ffmpeg, scene

pytestmark = requires_ffmpeg


@pytest.fixture
def tiny(project, monkeypatch):
    monkeypatch.setattr(config, "FPS", 10)
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 320)
    monkeypatch.setattr(assemble, "OUTPUT_SIZE", (320, 180))


def clip_scenes(n, duration=0.8):
    out = []
    for i in range(1, n + 1):
        key = f"{i:016x}"
        src = config.IMAGES_DIR / f"{key}.jpg"
        make_jpeg(src, seed=i, size=(320, 180))
        s = scene(i)
        s.update(measured_duration=duration, source_key=key,
                 source_file=src.relative_to(config.ROOT).as_posix())
        out.append(s)
    return out


def counting_renders(monkeypatch):
    stats = {"started": 0, "active": 0, "peak": 0}
    lock = threading.Lock()
    real = assemble.render_scene_clip

    def wrapper(s):
        with lock:
            stats["started"] += 1
            stats["active"] += 1
            stats["peak"] = max(stats["peak"], stats["active"])
        try:
            return real(s)
        finally:
            with lock:
                stats["active"] -= 1

    monkeypatch.setattr(assemble, "render_scene_clip", wrapper)
    return stats


def count_ffmpeg_renders(monkeypatch):
    counter = {"n": 0}
    real = assemble._zoompan_command
    lock = threading.Lock()

    def wrapper(*a, **k):
        with lock:
            counter["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(assemble, "_zoompan_command", wrapper)
    return counter


def test_n_scenes_make_n_ffmpeg_renders_and_valid_clips(tiny, monkeypatch):
    renders = count_ffmpeg_renders(monkeypatch)
    paths = assemble.render_all_clips(clip_scenes(6), workers=3)
    assert renders["n"] == 6
    assert len(set(paths)) == 6
    for p in paths:
        info = probe_video(p)
        assert (info["width"], info["height"]) == (320, 180)


def test_identical_clips_are_rendered_once(tiny, monkeypatch):
    renders = count_ffmpeg_renders(monkeypatch)
    scenes = clip_scenes(1) * 1
    twins = [dict(scenes[0], index=1), dict(scenes[0], index=2), dict(scenes[0], index=3)]
    paths = assemble.render_all_clips(twins, workers=3)
    assert renders["n"] == 1 and len(set(paths)) == 1


def test_a_finished_clip_is_never_rendered_again(tiny, monkeypatch):
    assemble.render_all_clips(clip_scenes(3), workers=2)
    renders = count_ffmpeg_renders(monkeypatch)
    assemble.render_all_clips(clip_scenes(5), workers=2)
    assert renders["n"] == 2                                      # only the two new scenes


def test_the_worker_cap_is_respected_and_used(tiny, monkeypatch):
    stats = counting_renders(monkeypatch)
    assemble.render_all_clips(clip_scenes(8), workers=3)
    assert stats["peak"] == 3


def test_resume_after_a_simulated_crash_renders_only_missing_clips(tiny, monkeypatch):
    scenes = clip_scenes(6)
    broken = [dict(s) for s in scenes]
    broken[3]["source_file"] = "build/images/missing.jpg"          # scene 4's render will fail
    with pytest.raises(RuntimeError, match="ffmpeg scene render failed"):
        assemble.render_all_clips(broken, workers=2)
    done = [p for p in config.SCENES_DIR.iterdir() if ".tmp" not in p.name]
    assert all(p.stat().st_size > 0 for p in done)

    renders = count_ffmpeg_renders(monkeypatch)
    assemble.render_all_clips(scenes, workers=2)
    assert renders["n"] == 6 - len(done)                           # nothing finished was redone


def test_a_failure_cancels_clips_that_have_not_started(tiny, monkeypatch):
    scenes = clip_scenes(14)
    scenes[0]["source_file"] = "build/images/missing.jpg"          # fails straight away
    stats = counting_renders(monkeypatch)
    with pytest.raises(RuntimeError, match="ffmpeg scene render failed"):
        assemble.render_all_clips(scenes, workers=2)
    assert stats["started"] < 14
    assert [p for p in config.SCENES_DIR.iterdir() if ".tmp" in p.name] == []   # no half-written files


def test_one_worker_and_four_workers_give_the_same_files(tiny):
    assemble.render_all_clips(clip_scenes(4), workers=1)
    sequential = sorted(p.name for p in config.SCENES_DIR.iterdir())
    for p in config.SCENES_DIR.iterdir():
        p.unlink()
    assemble.render_all_clips(clip_scenes(4), workers=4)
    assert sorted(p.name for p in config.SCENES_DIR.iterdir()) == sequential


def test_default_worker_count_is_about_half_the_cores(monkeypatch):
    monkeypatch.setattr(parallel.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(config, "CLIP_WORKERS", None)
    assert assemble.resolve_clip_workers(None) == 6
    monkeypatch.setattr(config, "CLIP_WORKERS", 3)
    assert assemble.resolve_clip_workers(None) == 3
    assert assemble.resolve_clip_workers(5) == 5                  # explicit beats config


def test_render_run_passes_the_clip_workers_through(tiny, monkeypatch):
    seen = {}

    def fake_all(scenes, workers=None):
        seen["workers"] = workers
        raise RuntimeError("stop here")

    monkeypatch.setattr(render, "render_all_clips", fake_all)
    from conftest import FakeImageProvider, FakeTTS
    with pytest.raises(RuntimeError, match="stop here"):
        render.run([1], config.OUTPUT_DIR / "x.mp4", "x", tts_provider=FakeTTS(),
                   image_provider=FakeImageProvider(), scenes=({}, [scene(1)]), clip_workers=2)
    assert seen["workers"] == 2
