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
