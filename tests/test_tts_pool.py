"""Speech synthesis for all lines runs on a capped pool. Fake provider, no
network. Retry behaviour lives inside the provider and is unchanged."""
import threading
import time

import pytest

import audio
import config
import render
from conftest import FakeTTS, requires_ffmpeg, scene

pytestmark = requires_ffmpeg


class SlowTTS(FakeTTS):
    """Takes a little time per line and tracks how many run at once."""

    def __init__(self, delay=0.05, fail_on=None, **kw):
        super().__init__(**kw)
        self.delay = delay
        self.fail_on = set(fail_on or [])
        self.ok = []
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def synthesize(self, text, emotion, out_path):
        with self._lock:
            self.calls.append((text, emotion, str(out_path)))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            if text in self.fail_on:
                raise RuntimeError(f"simulated fatal TTS failure on {text!r}")
            from conftest import make_tone
            make_tone(out_path, seconds=0.5)
            with self._lock:
                self.ok.append(text)
        finally:
            with self._lock:
                self.active -= 1


def lines(n):
    return [scene(i, f"line number {i} of the script") for i in range(1, n + 1)]


def non_temp(folder):
    return sorted(p.name for p in folder.iterdir() if ".tmp" not in p.name)


def test_n_scenes_make_exactly_n_provider_calls(project):
    tts = SlowTTS()
    paths = audio.build_all_audio(lines(8), tts, workers=4)
    assert len(tts.calls) == 8
    assert len(paths) == 8 and all(p.exists() for p in paths)


def test_every_scene_dict_gets_its_audio_fields(project):
    scenes = lines(3)
    audio.build_all_audio(scenes, SlowTTS(), workers=3)
    for s in scenes:
        assert (config.ROOT / s["audio_file"]).exists()
        assert s["audio_key"] in s["audio_file"]


def test_identical_lines_are_synthesized_once_even_in_a_pool(project):
    tts = SlowTTS()
    scenes = [scene(i, "exactly the same words") for i in range(1, 6)]
    audio.build_all_audio(scenes, tts, workers=4)
    assert len(tts.calls) == 1


def test_the_worker_cap_is_respected_and_actually_used(project):
    tts = SlowTTS(delay=0.08)
    audio.build_all_audio(lines(12), tts, workers=3)
    assert tts.peak == 3


def test_a_finished_scene_is_never_requested_again(project):
    first = SlowTTS()
    audio.build_all_audio(lines(3), first, workers=3)
    second = SlowTTS()
    audio.build_all_audio(lines(6), second, workers=3)
    assert sorted(second.texts) == sorted(s["text"] for s in lines(6)[3:])    # only scenes 4..6
    assert audio.build_all_audio(lines(6), SlowTTS(), workers=3) is not None


def test_resume_after_a_simulated_crash_requests_only_what_is_missing(project):
    texts = [s["text"] for s in lines(8)]
    crashing = SlowTTS(fail_on={texts[4]})
    with pytest.raises(RuntimeError, match="simulated fatal TTS failure"):
        audio.build_all_audio(lines(8), crashing, workers=2)

    healthy = SlowTTS()
    audio.build_all_audio(lines(8), healthy, workers=2)
    # across both runs every line was synthesized successfully exactly once
    assert sorted(crashing.ok + healthy.ok) == sorted(texts)


def test_a_fatal_error_stops_the_run_and_leaves_no_half_written_files(project):
    tts = SlowTTS(delay=0.05, fail_on={"line number 1 of the script"})
    with pytest.raises(RuntimeError, match="simulated fatal"):
        audio.build_all_audio(lines(30), tts, workers=2)
    assert len(tts.calls) < 10                                   # pending lines were cancelled
    # nothing finished was lost, nothing half-written is visible under a real name
    for name in non_temp(config.AUDIO_DIR):
        assert (config.AUDIO_DIR / name).stat().st_size > 0
    assert len([n for n in non_temp(config.AUDIO_DIR) if n.startswith("speech_")]) == len(tts.ok)


def test_one_worker_and_four_workers_produce_the_same_files(project, monkeypatch, tmp_path):
    audio.build_all_audio(lines(5), SlowTTS(delay=0.0), workers=1)
    sequential = non_temp(config.AUDIO_DIR)
    for p in config.AUDIO_DIR.iterdir():
        p.unlink()
    audio.build_all_audio(lines(5), SlowTTS(delay=0.0), workers=4)
    assert non_temp(config.AUDIO_DIR) == sequential


def test_pauses_work_in_the_pool_and_share_the_silence_clip(project):
    scenes = [scene(i, f"paused line {i}", pause_ms=400) for i in range(1, 5)]
    paths = audio.build_all_audio(scenes, SlowTTS(delay=0.0), workers=4)
    assert len(paths) == 4
    silences = [p for p in config.AUDIO_DIR.iterdir() if p.name.startswith("silence_") and ".tmp" not in p.name]
    assert len(silences) == 1


# ------------------------------------------------------------------ settings and CLI

def test_defaults_and_cli_flags_for_the_pool_sizes():
    assert config.TTS_WORKERS == 4
    args = render.build_parser().parse_args(["--all"])
    assert args.tts_workers is None and args.clip_workers is None
    args = render.build_parser().parse_args(["--all", "--tts-workers", "2", "--clip-workers", "3"])
    assert (args.tts_workers, args.clip_workers) == (2, 3)


def test_run_uses_the_configured_tts_workers_by_default(project, monkeypatch):
    seen = {}

    def fake_build_all(scenes, provider, workers=None):
        seen["workers"] = workers
        raise RuntimeError("stop here")

    monkeypatch.setattr(render, "build_all_audio", fake_build_all)
    with pytest.raises(RuntimeError, match="stop here"):
        render.run([1], config.OUTPUT_DIR / "x.mp4", "x", tts_provider=FakeTTS(),
                   scenes=({}, [scene(1)]))
    assert seen["workers"] == config.TTS_WORKERS
    with pytest.raises(RuntimeError, match="stop here"):
        render.run([1], config.OUTPUT_DIR / "x.mp4", "x", tts_provider=FakeTTS(),
                   scenes=({}, [scene(1)]), tts_workers=2)
    assert seen["workers"] == 2
