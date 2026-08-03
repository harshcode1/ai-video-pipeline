"""Two-pass EBU R128 loudness normalization (ffmpeg loudnorm), applied once
to the final concatenated voiceover before mux. Every scene currently sits
at -16 to -21 LUFS integrated; YouTube's platform target is -14 LUFS --
without this, the narration reads as weak/quiet regardless of voice quality.
"""
import json
import re
import subprocess

import ffmpeg_utils

TARGET_I = -14.0    # integrated loudness (LUFS), YouTube target
TARGET_TP = -1.0    # true peak ceiling (dBTP)
TARGET_LRA = 11.0   # loudness range


def _measure(in_path) -> dict:
    cmd = [
        ffmpeg_utils.FFMPEG, "-i", str(in_path),
        "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm's JSON report is printed to stderr, trailing after other logs
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.DOTALL)
    if not match:
        raise RuntimeError(f"loudnorm measurement pass failed to produce JSON: {result.stderr[-1500:]}")
    return json.loads(match.group(0))


def normalize(in_path, out_path) -> None:
    measured = _measure(in_path)
    loudnorm_filter = (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary"
    )
    cmd = [
        ffmpeg_utils.FFMPEG, "-y", "-i", str(in_path),
        "-af", loudnorm_filter,
        "-ar", "48000",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"loudnorm apply pass failed: {result.stderr[-2000:]}")
