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
import os
import subprocess
import tempfile
from pathlib import Path

import cache
import config
import ffmpeg_utils
import timing


CLIP_PAD_SECONDS = 1.0          # see the module docstring: keeps video and audio totals in sync
OUTPUT_SIZE = (1920, 1080)
CLIP_ENCODING = {"codec": "libx264", "pix_fmt": "yuv420p"}


def zoom_variant(source_key: str) -> int:
    """Which of the zoom/pan motions this picture gets. Derived from the picture's
    own hash (not the scene number), so inserting or reordering scenes never
    changes a scene's motion -- and so never invalidates its cached clip."""
    return int(cache.short_hash("zoom-variant", source_key)[:8], 16) % len(config.ZOOM_PAN_VARIANTS)


def clip_key(source_key: str, duration: float) -> str:
    """Everything that shapes a clip's pixels: the picture, the voiceover length,
    the motion, and the global visual settings."""
    variant = config.ZOOM_PAN_VARIANTS[zoom_variant(source_key)]
    return cache.short_hash(
        cache.STAGE_VERSIONS["clip"], source_key, round(duration, 3), variant,
        {"fps": config.FPS, "zoom_max": config.ZOOM_MAX, "zoom_step": config.ZOOM_STEP,
         "finish_filter": config.FINISH_FILTER, "work_width": config.SCALE_PRE_ZOOMPAN_WIDTH,
         "output_size": OUTPUT_SIZE, "pad_seconds": CLIP_PAD_SECONDS, "encoding": CLIP_ENCODING})


def _zoompan_command(source: Path, duration: float, source_key: str, out_path: Path) -> list:
    clip_dur = duration + CLIP_PAD_SECONDS
    frames = max(1, round(clip_dur * config.FPS))

    variant = config.ZOOM_PAN_VARIANTS[zoom_variant(source_key)]
    pan_x, pan_y = variant["pan_x"], variant["pan_y"]
    zoom_expr = f"min(zoom+{config.ZOOM_STEP},{config.ZOOM_MAX})"
    x_expr = f"iw/2-(iw/zoom/2)+({pan_x}*on/{frames})"
    y_expr = f"ih/2-(ih/zoom/2)+({pan_y}*on/{frames})"

    # Normalize at the pre-zoompan WORKING resolution (not 1920x1080) so a source
    # that is already that wide is never downscaled and then rescaled up again.
    work_w = config.SCALE_PRE_ZOOMPAN_WIDTH
    work_h = round(work_w * 1080 / 1920)
    # Sharpen AFTER zoompan (last step) so it acts on the final 1080p pixels the
    # viewer actually sees -- sharpening the source pre-zoom would get softened
    # again by the zoom's resampling.
    filter_chain = (
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=decrease,"
        f"pad={work_w}:{work_h}:-1:-1,setsar=1,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:"
        f"s={OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}:fps={config.FPS},"
        f"{config.FINISH_FILTER},"
        f"setsar=1"
    )
    # NOTE: -t must be an OUTPUT option here, not an input option. With
    # `-loop 1 -t X -i img.png`, the image2 loop demuxer feeds multiple input
    # frames over the X-second window and zoompan's `d` duplicates per input
    # frame -- exploding to hundreds of thousands of output frames. Putting -t on
    # the output side truncates the encoded result at the correct duration.
    return [
        ffmpeg_utils.FFMPEG, "-y",
        "-loop", "1", "-i", str(source),
        "-filter_complex", filter_chain,
        "-t", str(clip_dur),
        "-c:v", CLIP_ENCODING["codec"], "-pix_fmt", CLIP_ENCODING["pix_fmt"], "-r", str(config.FPS),
        str(out_path),
    ]


def render_scene_clip(scene: dict) -> Path:
    """Render (or reuse) this scene's zoompan clip. The picture to use is
    scene["source_file"] (set by the upscale stage); the clip is named by its key."""
    source_key = scene["source_key"]
    dur = scene["measured_duration"]
    key = clip_key(source_key, dur)
    out_path = config.SCENES_DIR / f"{key}.mp4"
    scene["clip_key"] = key
    scene["scene_file"] = out_path.relative_to(config.ROOT).as_posix()

    label = f"scene {scene['index']:02d} ({scene['timestamp']})"
    if cache.is_valid(out_path):
        print(f"  [CLIP] {label} cached, skipping")
        return out_path

    print(f"  [CLIP] {label} dur={dur:.2f}s clip={dur + CLIP_PAD_SECONDS:.2f}s")
    source = config.ROOT / scene["source_file"]

    def render(tmp: Path) -> None:
        result = subprocess.run(_zoompan_command(source, dur, source_key, tmp),
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg scene render failed for scene {scene['index']}: "
                               f"{result.stderr[-2000:]}")

    return cache.atomic_write(out_path, render)


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

    # A unique, self-removing script file: two chains running at once (or a leftover
    # from a crashed run) can never read each other's filter.
    config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    fd, script_name = tempfile.mkstemp(prefix="_xfade_", suffix=".txt", dir=config.BUILD_DIR)
    script_path = Path(script_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(filter_str)

        inputs = []
        for p in clip_paths:
            inputs += ["-i", str(p)]

        cmd = [
            ffmpeg_utils.FFMPEG, "-y",
            *inputs,
            "-filter_complex_script", str(script_path),
            "-map", f"[{out_label}]",
            "-c:v", CLIP_ENCODING["codec"], "-pix_fmt", CLIP_ENCODING["pix_fmt"], "-r", str(config.FPS),
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg xfade chain failed: {result.stderr[-3000:]}")
    finally:
        script_path.unlink(missing_ok=True)


def video_key(clip_keys: list) -> str:
    """Key of the crossfaded video: the ordered clips plus the join settings."""
    return cache.short_hash(cache.STAGE_VERSIONS["video"], list(clip_keys),
                            config.CROSSFADE_SECONDS, config.FPS, CLIP_ENCODING)


MUX_SETTINGS = {"video": "copy", "audio": "aac", "audio_bitrate": "192k", "shortest": True}


def final_key(video_key_: str, mix_key_: str) -> str:
    """Key of the finished file: its video and its mixed audio plus the mux settings."""
    return cache.short_hash(cache.STAGE_VERSIONS["final"], video_key_, mix_key_, MUX_SETTINGS)


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
