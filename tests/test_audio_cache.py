"""Per-scene audio is cached by CONTENT (text, voice, prosody, pause), never by
scene index, and every write is atomic."""
import config
import timing
from audio import audio_key, build_scene_audio
from conftest import FakeTTS, requires_ffmpeg, scene
from tts import speech_key

pytestmark = requires_ffmpeg


def names(folder):
    return sorted(p.name for p in folder.iterdir())


def build_all(scenes, tts):
    return [build_scene_audio(s, tts) for s in scenes]


THREE = [scene(1, "Welcome to the harbour"), scene(2, "The ship sinks slowly", emotion="dramatic tone"),
         scene(3, "The end")]


def test_files_are_named_by_hash_not_by_scene_index(project):
    tts = FakeTTS()
    paths = build_all(THREE, tts)
    assert len(tts.calls) == 3
    for p in paths:
        assert p.parent == config.AUDIO_DIR
        assert not p.stem.isdigit(), f"{p.name} still looks index-named"
    assert not (config.AUDIO_DIR / "01.mp3").exists()


def test_second_run_makes_zero_tts_calls(project):
    tts = FakeTTS()
    build_all(THREE, tts)
    tts.calls.clear()
    build_all(THREE, tts)
    assert tts.calls == []


def test_editing_one_line_re_synthesizes_only_that_scene(project):
    tts = FakeTTS()
    build_all(THREE, tts)
    tts.calls.clear()
    edited = [THREE[0], scene(2, "The ship sinks QUICKLY", emotion="dramatic tone"), THREE[2]]
    build_all(edited, tts)
    assert tts.texts == ["The ship sinks QUICKLY"]


def test_inserting_a_scene_at_the_start_reuses_every_other_scene(project):
    tts = FakeTTS()
    build_all(THREE, tts)
    tts.calls.clear()
    shifted = [scene(1, "A brand new opening")] + [dict(s, index=s["index"] + 1) for s in THREE]
    build_all(shifted, tts)
    assert tts.texts == ["A brand new opening"]


def test_reordering_scenes_makes_zero_tts_calls(project):
    tts = FakeTTS()
    build_all(THREE, tts)
    tts.calls.clear()
    build_all([THREE[2], THREE[0], THREE[1]], tts)
    assert tts.calls == []


def test_identical_lines_share_one_file(project):
    tts = FakeTTS()
    a, b = build_all([scene(1, "same words"), scene(2, "same words")], tts)
    assert a == b
    assert len(tts.calls) == 1


def test_changing_the_voice_re_synthesizes_everything(project):
    tts = FakeTTS(voice="voice-A")
    build_all(THREE, tts)
    other = FakeTTS(voice="voice-B")
    build_all(THREE, other)
    assert len(other.calls) == 3


def test_changing_a_prosody_setting_only_affects_scenes_using_it(project, monkeypatch):
    tts = FakeTTS()
    build_all(THREE, tts)
    tts.calls.clear()
    prosody = dict(config.EMOTION_PROSODY)
    prosody["dramatic tone"] = {"rate": "-30%", "pitch": "-40Hz"}
    monkeypatch.setattr(config, "EMOTION_PROSODY", prosody)
    build_all(THREE, tts)
    assert tts.texts == ["The ship sinks slowly"]           # only the dramatic scene


def test_changing_the_default_prosody_affects_scenes_without_an_emotion(project, monkeypatch):
    tts = FakeTTS()
    build_all(THREE, tts)
    tts.calls.clear()
    monkeypatch.setattr(config, "DEFAULT_PROSODY", {"rate": "-20%"})
    build_all(THREE, tts)
    assert sorted(tts.texts) == ["The end", "Welcome to the harbour"]


def test_changing_only_the_pause_does_not_call_tts_again(project):
    tts = FakeTTS()
    base = build_scene_audio(scene(1, "Welcome to the harbour"), tts)
    tts.calls.clear()
    paused = build_scene_audio(scene(1, "Welcome to the harbour", pause_ms=900), tts)
    assert tts.calls == []                                   # the speech file is reused
    assert paused != base
    assert timing.probe_duration(paused) > timing.probe_duration(base) + 0.8


def test_speech_and_audio_keys_depend_on_the_right_things():
    tts = FakeTTS()
    s = scene(1, "hello", emotion="whispers")
    assert speech_key(s["text"], s["emotion"], tts) == speech_key("hello", "whispers", tts)
    assert speech_key("hello", "whispers", tts) != speech_key("hello", None, tts)
    # pause_ms is NOT part of the speech key (it would force a pointless re-synthesis)...
    assert audio_key(s, tts) != audio_key(dict(s, pause_ms=400), tts)
    # ...and the scene index is not part of any key.
    assert audio_key(s, tts) == audio_key(dict(s, index=99), tts)


def test_synthesis_writes_to_a_temp_path_never_the_final_one(project):
    tts = FakeTTS()
    final = build_scene_audio(scene(1, "hello there"), tts)
    written_to = tts.calls[0][2]
    assert ".tmp" in written_to
    assert written_to != str(final)


def test_a_crash_during_synthesis_leaves_no_valid_file_and_the_retry_works(project):
    class Crashing(FakeTTS):
        def synthesize(self, text, emotion, out_path):
            out_path.write_bytes(b"half-written mp3 bytes" * 100)
            raise RuntimeError("process killed")

    s = scene(1, "hello there")
    try:
        build_scene_audio(s, Crashing())
    except RuntimeError:
        pass
    assert [p for p in config.AUDIO_DIR.iterdir() if ".tmp" not in p.name] == []

    good = FakeTTS()
    path = build_scene_audio(s, good)
    assert len(good.calls) == 1
    assert timing.probe_duration(path) > 0


def test_a_leftover_temp_file_from_an_earlier_crash_is_ignored(project):
    tts = FakeTTS()
    s = scene(1, "hello there")
    key = speech_key(s["text"], s["emotion"], tts)
    junk = config.AUDIO_DIR / f"speech_{key}.9999-deadbeef.tmp.mp3"
    junk.write_bytes(b"garbage left by a killed process" * 50)
    path = build_scene_audio(s, tts)
    assert len(tts.calls) == 1                               # junk was not mistaken for the speech
    assert timing.probe_duration(path) > 0


def test_silence_clips_are_hash_named_and_made_once(project):
    import silence
    first = silence.get_silence(400)
    mtime = first.stat().st_mtime_ns
    second = silence.get_silence(400)
    assert first == second
    assert not first.stem.endswith("ms")                     # no longer the shared fixed name
    assert second.stat().st_mtime_ns == mtime                # not regenerated
    assert [p for p in config.AUDIO_DIR.iterdir() if ".tmp" in p.name] == []
    assert timing.probe_duration(first) > 0.35
    assert silence.get_silence(900) != first                 # different length, different file


def test_many_threads_asking_for_the_same_silence_make_it_once_without_errors(project, monkeypatch):
    import threading

    import ffmpeg_utils
    import silence

    runs = []
    real_run = ffmpeg_utils.run

    def counting(cmd, **kw):
        runs.append(cmd)
        return real_run(cmd, **kw)

    monkeypatch.setattr(ffmpeg_utils, "run", counting)
    errors, paths = [], []

    def ask():
        try:
            paths.append(silence.get_silence(400))
        except Exception as e:                     # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=ask) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(set(paths)) == 1
    assert len(runs) == 1                          # one ffmpeg run, not eight
