"""Every external-tool call goes through ffmpeg_utils.run(): an argument list
(never a shell), -nostdin, stdin closed, UTF-8 output decoded safely."""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ffmpeg_utils
from conftest import FakeImageProvider, FakeTTS, requires_ffmpeg, scene

SRC = Path(__file__).resolve().parent.parent / "src"


# ------------------------------------------------------------------ the wrapper itself

class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", rec)
    return rec


def test_run_adds_nostdin_right_after_ffmpeg(recorder):
    ffmpeg_utils.run(["C:/tools/ffmpeg.exe", "-y", "-i", "a.mp3", "b.mp3"])
    assert recorder.calls[0][0][:3] == ["C:/tools/ffmpeg.exe", "-nostdin", "-y"]


def test_ffprobe_is_not_given_nostdin_because_it_does_not_support_it(recorder):
    ffmpeg_utils.run(["/usr/bin/ffprobe", "-v", "error", "a.mp3"])
    assert "-nostdin" not in recorder.calls[0][0]
    assert recorder.calls[0][1]["stdin"] == subprocess.DEVNULL          # closed instead


@requires_ffmpeg
def test_the_real_ffprobe_accepts_the_commands_we_build(tmp_path):
    from conftest import make_tone
    import timing
    tone = tmp_path / "t.mp3"
    make_tone(tone, 1.0)
    assert timing.probe_duration(tone) == pytest.approx(1.0, abs=0.1)


def test_run_does_not_duplicate_nostdin_or_touch_other_programs(recorder):
    ffmpeg_utils.run(["ffmpeg", "-nostdin", "-i", "a"])
    ffmpeg_utils.run(["realesrgan-ncnn-vulkan.exe", "-i", "a", "-o", "b"])
    assert recorder.calls[0][0].count("-nostdin") == 1
    assert "-nostdin" not in recorder.calls[1][0]


def test_run_never_uses_a_shell_and_closes_stdin_and_decodes_utf8_safely(recorder):
    ffmpeg_utils.run(["ffmpeg", "-i", "a"], cwd="somewhere")
    cmd, kwargs = recorder.calls[0]
    assert isinstance(cmd, list)
    assert not kwargs.get("shell")
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "replace"
    assert kwargs["capture_output"] is True
    assert kwargs["cwd"] == "somewhere"


def test_undecodable_output_does_not_crash():
    # bytes 0x81/0x8d are undefined in Windows' default cp1252 codepage, which is
    # what text=True used to decode with
    code = "import sys; sys.stderr.buffer.write(b'cafe \\x81\\x8d \\xc3\\xa9'); sys.exit(3)"
    result = ffmpeg_utils.run([sys.executable, "-c", code])
    assert result.returncode == 3
    assert "cafe" in result.stderr


# ------------------------------------------------------------------ importing without ffmpeg

def test_importing_the_pipeline_without_ffmpeg_works_and_fails_clearly_on_use():
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "LOCALAPPDATA")}
    env.update({"PATH": "", "LOCALAPPDATA": "", "PYTHONPATH": str(SRC)})
    code = (
        "import ffmpeg_utils, assemble, timing, loudness\n"            # all import fine
        "print('available', ffmpeg_utils.available())\n"
        "try:\n"
        "    ffmpeg_utils.FFMPEG\n"
        "except FileNotFoundError as e:\n"
        "    print('error', 'winget install' in str(e))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "available False" in result.stdout
    assert "error True" in result.stdout


# ------------------------------------------------------------------ source scan

def test_no_source_file_uses_shell_true_or_calls_subprocess_directly_for_ffmpeg():
    allowed_direct = {"ffmpeg_utils.py", "doctor.py", "watch.py"}      # doctor/watch run python itself
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                offenders.append(f"{path.name}: shell= keyword")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                    and node.func.attr in ("run", "Popen", "call", "check_output", "check_call")
                    and path.name not in allowed_direct):
                offenders.append(f"{path.name}:{node.lineno}: direct subprocess.{node.func.attr}")
    assert offenders == []


# ------------------------------------------------------------------ a whole run, observed

@requires_ffmpeg
def test_every_ffmpeg_and_ffprobe_call_in_a_real_run_is_safe(project, monkeypatch):
    import assemble
    import config
    import render

    monkeypatch.setattr(config, "FPS", 10)
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 320)
    monkeypatch.setattr(assemble, "OUTPUT_SIZE", (320, 180))

    seen = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        seen.append((list(cmd), kwargs))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", spy)
    scenes = [scene(1, "First scene here", pause_ms=400, prompt="a"), scene(2, "Second scene here", prompt="b")]
    render.run([1, 2], config.OUTPUT_DIR / "t.mp4", "t", tts_provider=FakeTTS(),
               image_provider=FakeImageProvider(), scenes=({}, scenes))

    tools = [(c, k) for c, k in seen if Path(c[0]).stem.lower() in ("ffmpeg", "ffprobe")]
    assert len(tools) >= 10, "expected silence, concat, probes, clips, chain, mix, mux"
    for cmd, kwargs in tools:
        if Path(cmd[0]).stem.lower() == "ffmpeg":
            assert cmd[1] == "-nostdin", cmd
        else:
            assert "-nostdin" not in cmd                  # ffprobe has no such option
        assert not kwargs.get("shell")
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["encoding"] == "utf-8"
    assert {Path(c[0]).stem.lower() for c, _ in tools} == {"ffmpeg", "ffprobe"}
