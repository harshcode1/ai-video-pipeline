"""Generate real silence clips via ffmpeg anullsrc (edge-tts/SSML <break>
cannot produce clean silence -- <break> is stripped by the Edge endpoint)."""
import subprocess
from pathlib import Path

import config
import ffmpeg_utils


def get_silence(ms: int) -> Path:
    path = config.AUDIO_DIR / f"_silence_{ms}ms.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path

    duration_s = ms / 1000.0
    cmd = [
        ffmpeg_utils.FFMPEG, "-y",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_s),
        "-q:a", "9",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed generating silence: {result.stderr}")
    return path
