"""Per-scene audio build: [silence if pause_ms>0] + [TTS speech] -> build/audio/NN.mp3.
Resumable: skips any stage whose output file already exists."""
import shutil
import subprocess
from pathlib import Path

import config
import ffmpeg_utils
from silence import get_silence
from tts import TTSProvider


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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")


def build_scene_audio(scene: dict, provider: TTSProvider) -> Path:
    final_path = config.ROOT / scene["audio_file"]
    if final_path.exists() and final_path.stat().st_size > 0:
        return final_path

    speech_path = config.AUDIO_DIR / f"{scene['index']:02d}_speech.mp3"
    if not speech_path.exists() or speech_path.stat().st_size == 0:
        print(f"  [TTS] scene {scene['index']:02d} ({scene['timestamp']}) emotion={scene['emotion'] or 'default'}")
        provider.synthesize(scene["text"], scene["emotion"], speech_path)
    else:
        print(f"  [TTS] scene {scene['index']:02d} ({scene['timestamp']}) speech cached, skipping")

    if scene["pause_ms"] > 0:
        silence_path = get_silence(scene["pause_ms"])
        _concat_two(silence_path, speech_path, final_path)
    else:
        shutil.copyfile(speech_path, final_path)

    return final_path
