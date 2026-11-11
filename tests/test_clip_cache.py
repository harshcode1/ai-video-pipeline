"""Per-scene clips are keyed by everything that shapes their pixels, and the
zoom/pan variant comes from the picture's content, so moving a scene around
never changes (or re-renders) its clip."""
import pytest

import assemble
import cache
import config
from conftest import make_jpeg, probe_video, requires_ffmpeg

SRC = "aaaa111122223333"


def key(**over):
    args = {"source_key": SRC, "duration": 6.25}
    args.update(over)
    return assemble.clip_key(**args)


# ------------------------------------------------------------------ pure key rules

def test_same_inputs_same_key():
    assert key() == key()


def test_scene_index_is_not_part_of_the_key():
    # clip_key has no index parameter at all: moving a scene can't change its clip
    import inspect
    assert "index" not in inspect.signature(assemble.clip_key).parameters


def test_different_picture_or_duration_changes_the_key():
    assert key(source_key="bbbb111122223333") != key()
    assert key(duration=6.26) != key()


def test_duration_is_compared_to_the_millisecond():
    assert key(duration=6.25) == key(duration=6.2500001)


@pytest.mark.parametrize("setting,value", [
    ("FPS", 24),
    ("ZOOM_MAX", 1.1),
    ("ZOOM_STEP", 0.001),
    ("FINISH_FILTER", "eq=contrast=1.2"),
    ("SCALE_PRE_ZOOMPAN_WIDTH", 1920),
])
def test_every_visual_setting_changes_the_key(monkeypatch, setting, value):
    before = key()
    monkeypatch.setattr(config, setting, value)
    assert key() != before


def test_the_pan_values_of_the_chosen_variant_change_the_key(monkeypatch):
    before = key()
    monkeypatch.setattr(config, "ZOOM_PAN_VARIANTS", [{"pan_x": 99, "pan_y": 99}] * 3)
    assert key() != before


def test_the_pad_and_codec_settings_change_the_key(monkeypatch):
    before = key()
    monkeypatch.setattr(assemble, "CLIP_PAD_SECONDS", 2.0)
    assert key() != before


def test_bumping_the_clip_stage_version_invalidates(monkeypatch):
    before = key()
    monkeypatch.setitem(cache.STAGE_VERSIONS, "clip", "2")
    assert key() != before


# ------------------------------------------------------------------ variant

def test_variant_depends_only_on_the_picture():
    assert assemble.zoom_variant(SRC) == assemble.zoom_variant(SRC)
    assert 0 <= assemble.zoom_variant(SRC) < len(config.ZOOM_PAN_VARIANTS)


def test_all_variants_are_used_across_many_pictures():
    seen = {assemble.zoom_variant(cache.short_hash("picture", i)) for i in range(60)}
    assert seen == set(range(len(config.ZOOM_PAN_VARIANTS)))


# ------------------------------------------------------------------ real render

@pytest.fixture
def small_clips(project, monkeypatch):
    """Tiny settings so a real ffmpeg render takes well under a second."""
    monkeypatch.setattr(config, "FPS", 10)
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 320)


def clip_scene(source_key="srcsrcsrc0000001", duration=1.0, seed=1):
    src = config.IMAGES_DIR / f"{source_key}.jpg"
    make_jpeg(src, seed=seed, size=(320, 180))
    return {"index": 1, "timestamp": "00:01", "measured_duration": duration,
            "source_key": source_key, "source_file": src.relative_to(config.ROOT).as_posix()}


@requires_ffmpeg
def test_renders_a_1080p_clip_named_by_its_key(small_clips):
    s = clip_scene(duration=1.0)
    path = assemble.render_scene_clip(s)
    assert path.parent == config.SCENES_DIR
    assert path.stem == assemble.clip_key(s["source_key"], 1.0)
    assert s["clip_key"] == path.stem
    info = probe_video(path)
    assert (info["width"], info["height"]) == (1920, 1080)
    assert abs(info["duration"] - (1.0 + assemble.CLIP_PAD_SECONDS)) <= 1 / config.FPS + 0.01


@requires_ffmpeg
def test_second_render_is_skipped(small_clips):
    s = clip_scene()
    first = assemble.render_scene_clip(s)
    mtime = first.stat().st_mtime_ns
    again = assemble.render_scene_clip(dict(s))
    assert again == first and again.stat().st_mtime_ns == mtime


@requires_ffmpeg
def test_a_different_duration_renders_a_new_clip(small_clips):
    a = assemble.render_scene_clip(clip_scene(duration=1.0))
    b = assemble.render_scene_clip(clip_scene(duration=1.5))
    assert a != b and a.exists() and b.exists()


@requires_ffmpeg
def test_a_leftover_temp_file_and_a_stale_index_clip_are_ignored(small_clips):
    s = clip_scene()
    key_ = assemble.clip_key(s["source_key"], s["measured_duration"])
    (config.SCENES_DIR / f"{key_}.9999-dead.tmp.mp4").write_bytes(b"half a video" * 100)
    stale = config.SCENES_DIR / "01.mp4"
    stale.write_bytes(b"A CLIP OF AN OLD SCENE" * 100)
    path = assemble.render_scene_clip(s)
    assert probe_video(path)["width"] == 1920                 # a real, freshly rendered clip
    assert stale.read_bytes().startswith(b"A CLIP OF AN OLD")


@requires_ffmpeg
def test_a_failed_render_leaves_no_file(small_clips):
    s = clip_scene()
    s["source_file"] = "build/images/does-not-exist.jpg"
    with pytest.raises(RuntimeError):
        assemble.render_scene_clip(s)
    assert list(config.SCENES_DIR.iterdir()) == []
