"""Keys for the assembly stages (video chain, mixed audio, final mux), and the
xfade filter script no longer being a shared fixed file."""
import threading

import pytest

import assemble
import cache
import config
import ffmpeg_utils
import loudness
from conftest import probe_video, requires_ffmpeg

CLIPS = ["c1", "c2", "c3"]
AUDIOS = ["a1", "a2", "a3"]


# ------------------------------------------------------------------ video key

def test_video_key_depends_on_the_ordered_clips():
    base = assemble.video_key(CLIPS)
    assert assemble.video_key(CLIPS) == base
    assert assemble.video_key(["c3", "c1", "c2"]) != base           # order matters
    assert assemble.video_key(["c1", "c2", "cX"]) != base           # one clip changed
    assert assemble.video_key(CLIPS + ["c4"]) != base               # one more clip


def test_video_key_depends_on_crossfade_and_fps(monkeypatch):
    base = assemble.video_key(CLIPS)
    monkeypatch.setattr(config, "CROSSFADE_SECONDS", 2.0)
    assert assemble.video_key(CLIPS) != base
    monkeypatch.undo()
    monkeypatch.setattr(config, "FPS", 24)
    assert assemble.video_key(CLIPS) != base


# ------------------------------------------------------------------ mix key

def test_mix_key_depends_on_the_ordered_audio_and_the_loudness_targets(monkeypatch):
    base = loudness.mix_key(AUDIOS)
    assert loudness.mix_key(AUDIOS) == base
    assert loudness.mix_key(["a2", "a1", "a3"]) != base
    assert loudness.mix_key(["a1", "a2", "aX"]) != base
    monkeypatch.setattr(loudness, "TARGET_I", -16.0)
    assert loudness.mix_key(AUDIOS) != base


def test_mix_key_ignores_the_clips():
    assert loudness.mix_key(AUDIOS) == loudness.mix_key(list(AUDIOS))


# ------------------------------------------------------------------ final key

def test_final_key_follows_both_inputs_and_the_stage_version(monkeypatch):
    v, m = assemble.video_key(CLIPS), loudness.mix_key(AUDIOS)
    base = assemble.final_key(v, m)
    assert assemble.final_key(v, m) == base
    assert assemble.final_key(assemble.video_key(["c1", "c2", "cX"]), m) != base
    assert assemble.final_key(v, loudness.mix_key(["a1", "a2", "aX"])) != base
    monkeypatch.setitem(cache.STAGE_VERSIONS, "final", "2")
    assert assemble.final_key(v, m) != base


# ------------------------------------------------------------------ xfade filter script

def make_clip(path, seconds, rate=10, color="testsrc"):
    result = ffmpeg_utils.run([ffmpeg_utils.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                               "-i", f"{color}=size=160x90:rate={rate}:duration={seconds}",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    assert result.returncode == 0, result.stderr


@requires_ffmpeg
def test_concurrent_xfade_chains_do_not_share_a_filter_file(project):
    sets = {}
    for tag, lengths in (("p", (2.0, 2.0, 2.0)), ("q", (3.0, 3.0))):
        clips = []
        for i, sec in enumerate(lengths):
            c = project / f"{tag}{i}.mp4"
            make_clip(c, sec, color="testsrc" if tag == "p" else "smptebars")
            clips.append(c)
        sets[tag] = clips

    errors, outs = [], {}

    def run(tag):
        try:
            out = project / f"out_{tag}.mp4"
            assemble.xfade_chain(sets[tag], out)
            outs[tag] = out
        except Exception as e:                           # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(t,)) for t in sets]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    fade = config.CROSSFADE_SECONDS
    assert probe_video(outs["p"])["duration"] == pytest.approx(6.0 - 2 * fade, abs=0.3)
    assert probe_video(outs["q"])["duration"] == pytest.approx(6.0 - 1 * fade, abs=0.3)
    leftovers = [p.name for p in config.BUILD_DIR.iterdir() if "xfade" in p.name]
    assert leftovers == [], "filter scripts must be removed after use"
