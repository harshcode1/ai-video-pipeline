"""Locate ffmpeg/ffprobe without depending on the current shell's PATH
(winget updates the *persistent* User PATH, but an already-running shell
won't see it until restarted -- so fall back to scanning the known
install location instead of failing)."""
import glob
import os
import shutil


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


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")
