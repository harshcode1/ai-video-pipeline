"""Shared pytest setup. The pipeline modules live in src/ and import each other
by bare name (`import config`), so src/ goes on sys.path for every test."""
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe")) or _winget_ffmpeg()


def _winget_ffmpeg() -> bool:
    try:
        import ffmpeg_utils
        return ffmpeg_utils.available()
    except Exception:
        return False


requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch):
    """Retry backoff and the image throttle would make tests sleep for real.
    Stub them in the modules that sleep; tests that need to observe timing use
    their own injected clock instead."""
    import time
    import types

    import config
    monkeypatch.setattr(config, "IMAGE_MIN_INTERVAL_SECONDS", 0.0)
    stub = types.SimpleNamespace(sleep=lambda *_: None, time=time.time, monotonic=time.monotonic)
    for name in ("images", "tts"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "time"):
            monkeypatch.setattr(module, "time", stub)


# ------------------------------------------------------------------ shared helpers

@pytest.fixture
def project(tmp_path, monkeypatch):
    """An isolated project root: every path in config points into tmp_path, so
    tests never touch the real build/ or output/ folders."""
    import config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(config, "SCRIPT_FILE", tmp_path / "input" / "Script.txt")
    monkeypatch.setattr(config, "VISUALS_FILE", tmp_path / "input" / "Visuals.txt")
    build = tmp_path / "build"
    monkeypatch.setattr(config, "BUILD_DIR", build)
    monkeypatch.setattr(config, "AUDIO_DIR", build / "audio")
    monkeypatch.setattr(config, "IMAGES_DIR", build / "images")
    monkeypatch.setattr(config, "IMAGES_UPSCALED_DIR", build / "images_upscaled")
    monkeypatch.setattr(config, "SCENES_DIR", build / "scenes")
    monkeypatch.setattr(config, "SCENES_JSON", build / "scenes.json")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(config, "FINAL_VIDEO", tmp_path / "output" / "final.mp4")
    config.ensure_dirs()
    (tmp_path / "input").mkdir(exist_ok=True)
    return tmp_path


def make_tone(path, seconds: float, freq: int = 440, amplitude: float = 0.5) -> None:
    """A real mp3 of a sine tone, via ffmpeg. Stands in for synthesized speech."""
    import ffmpeg_utils
    cmd = [ffmpeg_utils.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
           "-i", f"sine=frequency={freq}:sample_rate=24000:duration={seconds}",
           "-af", f"volume={amplitude}", "-q:a", "5", str(path)]
    result = ffmpeg_utils.run(cmd)
    assert result.returncode == 0, result.stderr


def make_jpeg(path, seed: int = 0, size=(256, 144)) -> None:
    """A small synthetic JPEG whose pixels depend on `seed` (so two seeds give
    two visibly different images)."""
    from PIL import Image
    img = Image.new("RGB", size)
    px = img.load()
    for x in range(size[0]):
        for y in range(size[1]):
            px[x, y] = ((x * 3 + seed * 40) % 256, (y * 5 + seed * 70) % 256, (x + y + seed * 25) % 256)
    img.save(path, "JPEG", quality=90)


class FakeTTS:
    """Writes a tone whose length depends on the text. Records every call."""

    def __init__(self, voice: str = "fake-voice"):
        self.voice = voice
        self.calls = []

    def fingerprint(self) -> dict:
        return {"provider": "fake-tts", "voice": self.voice}

    def synthesize(self, text, emotion, out_path):
        self.calls.append((text, emotion, str(out_path)))
        make_tone(out_path, seconds=max(0.5, round(len(text) * 0.04, 2)))

    @property
    def texts(self):
        return [c[0] for c in self.calls]


def scene(i, text="line", emotion=None, pause_ms=0, prompt=None):
    """A minimal scene dict as parse.build_scenes() would make (keys are by content now)."""
    return {"index": i, "timestamp": f"00:{i:02d}", "seconds": i, "text": text, "emotion": emotion,
            "pause_ms": pause_ms, "raw_directives": [], "image_prompt": prompt or f"prompt {i}"}


def probe_video(path) -> dict:
    """width, height, duration (s), fps of the first video stream, via ffprobe."""
    import json

    import ffmpeg_utils
    result = ffmpeg_utils.run(
        [ffmpeg_utils.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)])
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    num, den = data["streams"][0]["r_frame_rate"].split("/")
    return {"width": data["streams"][0]["width"], "height": data["streams"][0]["height"],
            "fps": int(num) / int(den), "duration": float(data["format"]["duration"])}


class FakeImageProvider:
    """Writes a small synthetic JPEG whose pixels depend on the prompt. Records calls."""

    def __init__(self, seed: int = 1, fail_on_prompt: str | None = None):
        self.seed = seed
        self.calls = []
        self.fail_on_prompt = fail_on_prompt

    def fingerprint(self) -> dict:
        return {"provider": "fake-img", "seed": self.seed}

    def fetch(self, prompt, out_path):
        if self.fail_on_prompt is not None and prompt == self.fail_on_prompt:
            raise RuntimeError(f"simulated provider failure on {prompt!r}")
        self.calls.append(prompt)
        make_jpeg(out_path, seed=abs(hash(prompt)) % 7, size=(320, 180))

    @property
    def prompts(self):
        return self.calls
