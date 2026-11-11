"""The whole point of content-hash caching, tested end to end: change one thing
and ONLY the stages that depend on it run again. Real ffmpeg, tiny synthetic
inputs, fake TTS and image providers (no network, no credits).

Each test counts how many times every stage actually did work."""
import dataclasses

import pytest

import assemble
import config
import loudness
import render
from conftest import FakeImageProvider, FakeTTS, probe_video, requires_ffmpeg, scene

pytestmark = requires_ffmpeg


@dataclasses.dataclass
class Work:
    tts: int = 0
    images: int = 0
    clips: int = 0
    chains: int = 0
    mixes: int = 0
    muxes: int = 0

    def as_tuple(self):
        return (self.tts, self.images, self.clips, self.chains, self.mixes, self.muxes)


@pytest.fixture
def pipeline(project, monkeypatch):
    """Returns run(scenes, tts=None, images=None) -> Work: executes render.run on the
    given scenes and reports how many times each stage really worked."""
    monkeypatch.setattr(config, "FPS", 10)
    monkeypatch.setattr(assemble, "OUTPUT_SIZE", (320, 180))          # tiny frames keep ffmpeg fast; the
                                                                       # end-to-end test checks real 1920x1080
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 320)       # fixture images are 320 wide (16:9 divides exactly)

    counters = {"clips": 0, "chains": 0, "mixes": 0, "muxes": 0}

    def counting(name, key, fn):
        def wrapper(*a, **k):
            counters[key] += 1
            return fn(*a, **k)
        monkeypatch.setattr(name[0], name[1], wrapper)

    counting((assemble, "_zoompan_command"), "clips", assemble._zoompan_command)
    counting((render, "xfade_chain"), "chains", render.xfade_chain)
    counting((render, "normalize_loudness"), "mixes", render.normalize_loudness)
    counting((render, "mux_audio"), "muxes", render.mux_audio)

    state = {"tts": FakeTTS(), "images": FakeImageProvider()}

    def run(scenes, tts=None, images=None, label="t"):
        tts = tts or state["tts"]
        images = images or state["images"]
        before = dict(counters)
        t0, i0 = len(tts.calls), len(images.calls)
        out = config.OUTPUT_DIR / f"{label}.mp4"
        render.run([s["index"] for s in scenes], out, label,
                   tts_provider=tts, image_provider=images, scenes=({}, [dict(s) for s in scenes]))
        work = Work(tts=len(tts.calls) - t0, images=len(images.calls) - i0,
                    clips=counters["clips"] - before["clips"], chains=counters["chains"] - before["chains"],
                    mixes=counters["mixes"] - before["mixes"], muxes=counters["muxes"] - before["muxes"])
        work.output = out
        return work

    run.state = state
    return run


FOUR = [
    scene(1, "The first scene of four", prompt="a foggy harbour"),
    scene(2, "A second and rather longer scene", prompt="a sinking ship"),
    scene(3, "Third scene", prompt="a lighthouse"),
    scene(4, "The very last scene in this set", prompt="an empty sea"),
]


def edited(scenes, idx, **changes):
    return [dict(s, **changes) if s["index"] == idx else dict(s) for s in scenes]


def renumber(scenes):
    return [dict(s, index=i) for i, s in enumerate(scenes, start=1)]


# ------------------------------------------------------------------ baseline

def test_first_run_does_all_the_work_and_makes_the_video(pipeline):
    w = pipeline(FOUR)
    assert w.as_tuple() == (4, 4, 4, 1, 1, 1)
    info = probe_video(w.output)
    assert (info["width"], info["height"]) == assemble.OUTPUT_SIZE


def test_second_identical_run_does_no_work_at_all(pipeline):
    pipeline(FOUR)
    w = pipeline(FOUR)
    assert w.as_tuple() == (0, 0, 0, 0, 0, 0)
    assert w.output.exists()                                  # the output is still delivered


# ------------------------------------------------------------------ the scenarios

def test_editing_one_line_reruns_only_that_scenes_tts_clip_and_the_final_assembly(pipeline):
    pipeline(FOUR)
    w = pipeline(edited(FOUR, 2, text="A second scene, now with different and longer narration text"))
    # 1 TTS, 0 images, 1 clip (its length changed), then chain + mix + mux once each
    assert w.as_tuple() == (1, 0, 1, 1, 1, 1)


def test_editing_one_image_prompt_reruns_only_that_image_its_clip_and_the_video(pipeline):
    pipeline(FOUR)
    w = pipeline(edited(FOUR, 3, image_prompt="a lighthouse in a storm"))
    # no TTS, no audio mix (audio unchanged): 1 image, 1 clip, video chain + mux
    assert w.as_tuple() == (0, 1, 1, 1, 0, 1)


def test_inserting_a_scene_at_the_start_reuses_every_other_scene(pipeline):
    pipeline(FOUR)
    new = [scene(1, "A brand new opening scene", prompt="a lighthouse at dawn")] + FOUR
    w = pipeline(renumber(new))
    assert w.as_tuple() == (1, 1, 1, 1, 1, 1)                 # only the NEW scene did any work


def test_reordering_scenes_reruns_only_the_final_assembly(pipeline):
    pipeline(FOUR)
    w = pipeline(renumber([FOUR[2], FOUR[0], FOUR[3], FOUR[1]]))
    assert w.as_tuple() == (0, 0, 0, 1, 1, 1)


def test_deleting_a_scene_reuses_everything_else(pipeline):
    pipeline(FOUR)
    w = pipeline(renumber([FOUR[0], FOUR[1], FOUR[3]]))
    assert w.as_tuple() == (0, 0, 0, 1, 1, 1)


# ------------------------------------------------------------------ global settings

def test_changing_fps_reruns_every_clip_but_not_tts_or_images_or_audio(pipeline, monkeypatch):
    pipeline(FOUR)
    monkeypatch.setattr(config, "FPS", 12)
    w = pipeline(FOUR)
    assert w.as_tuple() == (0, 0, 4, 1, 0, 1)


def test_changing_the_working_resolution_reruns_every_clip(pipeline, monkeypatch):
    pipeline(FOUR)
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 288)
    w = pipeline(FOUR)
    assert w.as_tuple() == (0, 0, 4, 1, 0, 1)


def test_changing_the_image_seed_regenerates_every_image_and_clip(pipeline):
    pipeline(FOUR)
    w = pipeline(FOUR, images=FakeImageProvider(seed=2))
    assert w.as_tuple() == (0, 4, 4, 1, 0, 1)


def test_changing_the_voice_reruns_tts_and_the_audio_mix_but_not_the_clips(pipeline):
    pipeline(FOUR)
    w = pipeline(FOUR, tts=FakeTTS(voice="another-voice"))
    # durations are unchanged by the fake, so clips are reused; audio is new
    assert w.as_tuple() == (4, 0, 0, 0, 1, 1)


def test_changing_the_loudness_target_reruns_only_the_mix_and_the_mux(pipeline, monkeypatch):
    pipeline(FOUR)
    monkeypatch.setattr(loudness, "TARGET_I", -16.0)
    w = pipeline(FOUR)
    assert w.as_tuple() == (0, 0, 0, 0, 1, 1)


def test_changing_the_crossfade_reruns_only_the_video_chain_and_the_mux(pipeline, monkeypatch):
    pipeline(FOUR)
    monkeypatch.setattr(config, "CROSSFADE_SECONDS", 0.5)
    w = pipeline(FOUR)
    assert w.as_tuple() == (0, 0, 0, 1, 0, 1)


# ------------------------------------------------------------------ crashes and leftovers

def test_leftover_temp_files_from_a_crash_are_ignored(pipeline):
    pipeline(FOUR)
    for folder in (config.AUDIO_DIR, config.IMAGES_DIR, config.SCENES_DIR, config.BUILD_DIR / "final"):
        (folder / "abcdef0123456789.4242-deadbeef.tmp.mp4").write_bytes(b"half-written" * 200)
    w = pipeline(FOUR)
    assert w.as_tuple() == (0, 0, 0, 0, 0, 0)


def test_a_crash_midway_then_a_rerun_never_re_requests_finished_images(pipeline):
    images = FakeImageProvider(fail_on_prompt="a lighthouse")       # scene 3's picture fails
    with pytest.raises(RuntimeError, match="simulated provider failure"):
        pipeline(FOUR, images=images)
    assert images.prompts == ["a foggy harbour", "a sinking ship"]  # 1 and 2 were fetched and paid for

    healthy = FakeImageProvider()
    w = pipeline(FOUR, images=healthy)
    assert healthy.prompts == ["a lighthouse", "an empty sea"]       # only the missing two
    assert w.tts == 0                                                # speech finished in the first run
    assert w.output.exists()


def test_old_index_named_files_are_never_used_or_deleted_and_the_user_is_told(pipeline, capsys):
    legacy = {config.AUDIO_DIR / "01.mp3": b"OLD AUDIO", config.IMAGES_DIR / "01.jpg": b"OLD IMAGE",
              config.IMAGES_UPSCALED_DIR / "01.png": b"OLD UPSCALE", config.SCENES_DIR / "01.mp4": b"OLD CLIP"}
    for path, data in legacy.items():
        path.write_bytes(data * 50)
    w = pipeline(FOUR)
    assert w.as_tuple() == (4, 4, 4, 1, 1, 1)                        # none of it was reused
    for path, data in legacy.items():
        assert path.read_bytes() == data * 50                         # none of it was deleted
    assert "no longer used" in capsys.readouterr().out
