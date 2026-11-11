"""Per-scene audio build: [silence if pause_ms>0] + [TTS speech] -> one mp3.

Files are named by a hash of their content (see cache.py), not by scene number:
the speech file by what was said and how, the scene file by that plus the pause.
So editing one line re-synthesizes one line, and inserting or reordering scenes
re-synthesizes nothing. Resumable: a stage is skipped when its output exists.
"""
import shutil
from pathlib import Path

import cache
import config
import ffmpeg_utils
import parallel
import silence
from silence import get_silence
from tts import TTSProvider, speech_key


def _concat_two(a: Path, b: Path, out_path: Path) -> None:
    """Concat two audio files via filter_complex (re-encodes -- avoids mp3
    concat-demuxer frame/sample-rate mismatch pitfalls entirely)."""
    cmd = [
        ffmpeg_utils.FFMPEG, "-y",
        "-i", str(a), "-i", str(b),
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[aout]",
        "-map", "[aout]",
        str(out_path),
    ]
    result = ffmpeg_utils.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")


def audio_key(scene: dict, provider: TTSProvider) -> str:
    """Key of the finished scene audio: the speech plus the pause in front of it."""
    pause = scene["pause_ms"]
    return cache.short_hash(cache.STAGE_VERSIONS["audio"],
                            speech_key(scene["text"], scene["emotion"], provider),
                            pause, silence.SILENCE_PARAMS if pause else None)


def _ensure_speech(scene: dict, provider: TTSProvider) -> Path:
    """Synthesize this line's speech unless a file with the same key exists."""
    skey = speech_key(scene["text"], scene["emotion"], provider)
    speech_path = config.AUDIO_DIR / f"speech_{skey}.mp3"
    label = f"scene {scene['index']:02d} ({scene['timestamp']})"
    if cache.is_valid(speech_path):
        print(f"  [TTS] {label} speech cached, skipping")
    else:
        print(f"  [TTS] {label} emotion={scene['emotion'] or 'default'}")
        cache.atomic_write(speech_path,
                           lambda tmp: provider.synthesize(scene["text"], scene["emotion"], tmp))
    return speech_path


def build_all_audio(scenes: list, provider: TTSProvider, workers: int | None = None) -> list:
    """Build the audio for every scene, synthesizing on a capped thread pool.

    Lines with identical speech (same text, prosody, voice) are synthesized once,
    and a line whose file already exists is never requested again. The retry
    behaviour is the provider's own and is unchanged. The first failure stops the
    run; everything already finished stays cached for the next attempt.
    """
    workers = config.TTS_WORKERS if workers is None else workers

    unique_speech = {}
    for s in scenes:
        unique_speech.setdefault(speech_key(s["text"], s["emotion"], provider), s)
    parallel.run_pool(list(unique_speech.values()), lambda s: _ensure_speech(s, provider), workers)

    unique_audio = {}
    for s in scenes:
        unique_audio.setdefault(audio_key(s, provider), s)
    parallel.run_pool(list(unique_audio.values()), lambda s: build_scene_audio(s, provider), workers)

    # Everything is cached now; this just records each scene's file and key.
    return [build_scene_audio(s, provider) for s in scenes]


def build_scene_audio(scene: dict, provider: TTSProvider) -> Path:
    akey = audio_key(scene, provider)
    final_path = config.AUDIO_DIR / f"{akey}.mp3"
    scene["audio_key"] = akey
    scene["audio_file"] = final_path.relative_to(config.ROOT).as_posix()

    if cache.is_valid(final_path):
        return final_path

    speech_path = _ensure_speech(scene, provider)

    if scene["pause_ms"] > 0:
        silence_path = get_silence(scene["pause_ms"])
        cache.atomic_write(final_path, lambda tmp: _concat_two(silence_path, speech_path, tmp))
    else:
        cache.atomic_write(final_path, lambda tmp: shutil.copyfile(speech_path, tmp))

    return final_path
