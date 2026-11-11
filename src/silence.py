"""Generate real silence clips via ffmpeg anullsrc (edge-tts/SSML <break>
cannot produce clean silence -- <break> is stripped by the Edge endpoint)."""
import threading
from pathlib import Path

import cache
import config
import ffmpeg_utils

# Part of the scene-audio cache key: changing how silence is made re-makes it.
SILENCE_PARAMS = {"sample_rate": 24000, "channels": "mono", "mp3_quality": 9}


def silence_path(ms: int) -> Path:
    key = cache.short_hash("silence", ms, SILENCE_PARAMS)
    return config.AUDIO_DIR / f"silence_{key}.mp3"


_locks_guard = threading.Lock()
_locks: dict = {}


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


def get_silence(ms: int) -> Path:
    """Many scenes can share one silence clip, and scenes are built on several
    threads. One thread makes it; the others wait and then reuse it."""
    path = silence_path(ms)
    with _lock_for(path):
        if cache.is_valid(path):
            return path
        return _make_silence(ms, path)


def _make_silence(ms: int, path: Path) -> Path:
    def make(tmp: Path) -> None:
        cmd = [
            ffmpeg_utils.FFMPEG, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r={SILENCE_PARAMS['sample_rate']}:cl={SILENCE_PARAMS['channels']}",
            "-t", str(ms / 1000.0),
            "-q:a", str(SILENCE_PARAMS["mp3_quality"]),
            str(tmp),
        ]
        result = ffmpeg_utils.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed generating silence: {result.stderr}")

    return cache.atomic_write(path, make)
