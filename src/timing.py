"""Timing is derived from measured audio duration, never from Script.txt's
own [MM:SS] markers -- those are only used here for the comparison table,
never to drive scene length. No forced alignment (WhisperX/aeneas/MFA):
per-line TTS makes drift impossible by construction."""
import json
import subprocess

import config
import ffmpeg_utils


def probe_duration(path) -> float:
    cmd = [
        ffmpeg_utils.FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def scripted_duration(all_scenes: list, index: int) -> float | None:
    """Gap between this scene's script timestamp and the next scene's --
    i.e. what the original script assumed the line would take."""
    pos = index - 1
    if pos + 1 < len(all_scenes):
        return all_scenes[pos + 1]["seconds"] - all_scenes[pos]["seconds"]
    return None


def print_timing_table(rendered_scenes: list, all_scenes: list) -> None:
    print()
    print(f"{'#':>3} {'ts':>6} {'scripted':>9} {'measured':>9} {'delta':>8}")
    print("-" * 42)
    total_scripted = 0.0
    total_measured = 0.0
    for s in rendered_scenes:
        scripted = scripted_duration(all_scenes, s["index"])
        measured = s["measured_duration"]
        delta = (measured - scripted) if scripted is not None else float("nan")
        scripted_str = f"{scripted:.2f}s" if scripted is not None else "n/a"
        total_measured += measured
        if scripted is not None:
            total_scripted += scripted
        print(f"{s['index']:>3} {s['timestamp']:>6} {scripted_str:>9} {measured:>8.2f}s {delta:>+7.2f}s")
    print("-" * 42)
    print(f"{'totals':>20} {total_scripted:>8.2f}s {total_measured:>8.2f}s {total_measured - total_scripted:>+7.2f}s")


def concat_audio_files(paths: list, out_path) -> None:
    """Concat N mp3 files via filter_complex (re-encode -- robust to any
    minor format inconsistency across per-scene files)."""
    inputs = []
    for p in paths:
        inputs += ["-i", str(p)]
    n = len(paths)
    filter_str = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"
    cmd = [ffmpeg_utils.FFMPEG, "-y", *inputs, "-filter_complex", filter_str, "-map", "[aout]", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio concat failed: {result.stderr}")
