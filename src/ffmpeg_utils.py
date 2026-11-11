"""Locate ffmpeg/ffprobe and run external tools safely.

Finding them does not depend on the current shell's PATH (winget updates the
*persistent* User PATH, but an already-running shell won't see it until
restarted -- so fall back to scanning the known install location instead of
failing).

Importing this module never fails: a missing ffmpeg is reported (with the fix)
the first time FFMPEG / FFPROBE is actually used. That lets the pure-logic parts
of the pipeline and their tests load on a machine without ffmpeg, and lets
doctor.py report the problem instead of crashing on import.

Every ffmpeg/ffprobe/Real-ESRGAN call goes through run(): an argument list
(never a shell), -nostdin, stdin closed, and UTF-8 decoding that can't crash.
"""
import glob
import os
import shutil
import subprocess
from pathlib import Path


def _find(exe_name: str) -> str:
    found = shutil.which(exe_name)
    if found:
        return found

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    fallback_globs = [
        f"{local_app_data}\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg*\\**\\{exe_name}.exe",
        f"C:\\ffmpeg\\bin\\{exe_name}.exe",
    ]
    for pattern in fallback_globs:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    raise FileNotFoundError(
        f"{exe_name} not found on PATH or in known winget install location. "
        f"Install via: winget install --id Gyan.FFmpeg -e"
    )


_cache: dict = {}


def __getattr__(name: str):
    """FFMPEG / FFPROBE are looked up on first use, not at import."""
    if name in ("FFMPEG", "FFPROBE"):
        if name not in _cache:
            _cache[name] = _find(name.lower())
        return _cache[name]
    raise AttributeError(f"module 'ffmpeg_utils' has no attribute {name!r}")


def available() -> bool:
    """True if both ffmpeg and ffprobe can be found."""
    try:
        return bool(__getattr__("FFMPEG") and __getattr__("FFPROBE"))
    except FileNotFoundError:
        return False


def run(cmd: list, cwd=None) -> subprocess.CompletedProcess:
    """Run an external tool and capture its output.

    - `cmd` is an argument list; no shell is ever involved.
    - ffmpeg gets -nostdin so it never reads or blocks on the terminal (important
      once several run at once). ffprobe does NOT support that option (verified:
      it errors "Option not found"), but it never reads stdin interactively, and
      stdin is closed for every tool anyway.
    - Output is decoded as UTF-8 with undecodable bytes replaced. text=True alone
      uses the Windows locale codepage and can raise on ffmpeg's output.
    """
    cmd = [str(part) for part in cmd]
    if Path(cmd[0]).stem.lower() == "ffmpeg" and "-nostdin" not in cmd:
        cmd = [cmd[0], "-nostdin", *cmd[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, cwd=cwd)
