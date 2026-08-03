"""Raw ffmpeg assembly (no MoviePy -- avoids memory leaks on long renders).

Per scene: render an intermediate clip at duration+1s (image normalized to
1920x1080, then zoompan). The +1s pad is deliberate: chaining N clips with
a 1s xfade removes exactly 1s of timeline at each of the (N-1) joins, so
padding every clip by 1s keeps the crossfaded total in sync with the
un-padded audio total (to within ~1s on the very last clip, trimmed later
by -shortest at mux time).

compute_offsets() is the pure offset-math function -- unit-tested in
tests/test_assemble.py before ever touching a real ffmpeg process, since a
bug here cascades through all 69 joins.
"""
import subprocess
from pathlib import Path

import config
import ffmpeg_utils
import timing


def render_scene_clip(scene: dict) -> Path:
    out_path = config.ROOT / scene["scene_file"]
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"  [CLIP] scene {scene['index']:02d} ({scene['timestamp']}) cached, skipping")
        return out_path

    img_path = config.IMAGES_UPSCALED_DIR / f"{scene['index']:02d}.png"
    if not img_path.exists():
        img_path = config.ROOT / scene["image_file"]  # fallback: raw (non-upscaled)

    dur = scene["measured_duration"]
    clip_dur = dur + 1.0
    frames = max(1, round(clip_dur * config.FPS))

    variant = config.ZOOM_PAN_VARIANTS[scene["index"] % len(config.ZOOM_PAN_VARIANTS)]
    pan_x, pan_y = variant["pan_x"], variant["pan_y"]
    zoom_expr = f"min(zoom+{config.ZOOM_STEP},{config.ZOOM_MAX})"
    x_expr = f"iw/2-(iw/zoom/2)+({pan_x}*on/{frames})"
    y_expr = f"ih/2-(ih/zoom/2)+({pan_y}*on/{frames})"

    # Normalize at the pre-zoompan WORKING resolution (not 1920x1080) so a
    # real upscaled 4096x2304 source never gets downscaled-then-rescaled-up
    # again -- that round trip would throw away Real-ESRGAN's actual detail
    # and replace it with the same bicubic blur we upscaled to avoid.
    work_w = config.SCALE_PRE_ZOOMPAN_WIDTH
    work_h = round(work_w * 1080 / 1920)
    # Sharpen AFTER zoompan (last step) so it acts on the final 1080p pixels the
    # viewer actually sees -- sharpening the source pre-zoom would get softened
    # again by the zoom's resampling.
    filter_chain = (
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=decrease,"
        f"pad={work_w}:{work_h}:-1:-1,setsar=1,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1920x1080:fps={config.FPS},"
        f"{config.FINISH_FILTER},"
        f"setsar=1"
    )

    print(f"  [CLIP] scene {scene['index']:02d} ({scene['timestamp']}) dur={dur:.2f}s clip={clip_dur:.2f}s")
    # NOTE: -t must be an OUTPUT option here, not an input option. With
    # `-loop 1 -t X -i img.png`, the image2 loop demuxer feeds multiple
    # input frames over the X-second window (default ~25fps), and zoompan's
    # `d` duplicates per input frame -- exploding to hundreds of thousands
    # of output frames. Putting -t on the output side truncates the encoded
    # result at the correct wall-clock duration regardless of how many
    # frames zoompan generates internally.
    cmd = [
        ffmpeg_utils.FFMPEG, "-y",
        "-loop", "1", "-i", str(img_path),
        "-filter_complex", filter_chain,
        "-t", str(clip_dur),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg scene render failed for scene {scene['index']}: {result.stderr[-2000:]}")
    return out_path


def compute_offsets(durations: list, fade: float) -> list:
    """durations[i] = length of clip i (including its +1s pad).
    Returns xfade offset for each join i->i+1, len(durations)-1 items."""
    if len(durations) < 2:
        return []
    offsets = []
    cumulative = durations[0]
    for d in durations[1:]:
        offset = cumulative - fade
        offsets.append(offset)
        cumulative = cumulative + d - fade
    return offsets


def build_xfade_filter(clip_durations: list, fade: float = config.CROSSFADE_SECONDS):
    n = len(clip_durations)
    if n == 1:
        return None, "0:v"
    offsets = compute_offsets(clip_durations, fade)
    parts = []
    label = "0:v"
    for i in range(1, n):
        out_label = f"v{i}" if i < n - 1 else "vout"
        parts.append(
            f"[{label}][{i}:v]xfade=transition=fade:duration={fade}:offset={offsets[i-1]:.3f}[{out_label}]"
        )
        label = out_label
    return ";".join(parts), ("vout" if n > 1 else "0:v")


def xfade_chain(clip_paths: list, out_path: Path) -> None:
    if len(clip_paths) == 1:
        cmd = [ffmpeg_utils.FFMPEG, "-y", "-i", str(clip_paths[0]), "-c", "copy", str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg single-clip copy failed: {result.stderr[-2000:]}")
        return

    clip_durations = [timing.probe_duration(p) for p in clip_paths]
    filter_str, out_label = build_xfade_filter(clip_durations)

    script_path = config.BUILD_DIR / "_xfade_filter.txt"
    script_path.write_text(filter_str, encoding="utf-8")

    inputs = []
    for p in clip_paths:
        inputs += ["-i", str(p)]

    cmd = [
        ffmpeg_utils.FFMPEG, "-y",
        *inputs,
        "-filter_complex_script", str(script_path),
        "-map", f"[{out_label}]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg xfade chain failed: {result.stderr[-3000:]}")


def mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    cmd = [
        ffmpeg_utils.FFMPEG, "-y",
        "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {result.stderr[-2000:]}")
