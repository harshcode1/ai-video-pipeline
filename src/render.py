"""Pipeline orchestrator. Every stage is resumable/idempotent -- rerunning
after a crash only redoes what's missing.

Usage:
  python src/render.py --indices 1,2,5,34,65   # checkpoint / partial run
  python src/render.py --all                    # full 69-scene run
  python src/render.py --all --no-upscale       # force-skip Real-ESRGAN (usually
                                                # unnecessary -- it auto-skips at 4K)

Requires GEMINI_API_KEY for image generation -- free key from
https://aistudio.google.com/apikey. Run src/doctor.py to verify first.
"""
import argparse

import config
import parse
import timing
from assemble import mux_audio, render_scene_clip, xfade_chain
from audio import build_scene_audio
from images import get_image, make_provider
from loudness import normalize as normalize_loudness
from tts import EdgeTTSProvider
from upscale import get_upscaled_image


def run(indices: list, output_path, label: str, upscale: bool = True):
    print(f"=== render.py: {label} ({len(indices)} scene(s): {indices}) ===\n")

    meta, all_scenes = parse.build_scenes()
    by_index = {s["index"]: s for s in all_scenes}
    for i in indices:
        if i not in by_index:
            raise ValueError(f"Scene index {i} does not exist (valid range 1-{len(all_scenes)})")

    selected = [by_index[i] for i in indices]

    print("--- Stage 1/5: TTS (edge-tts, per-line) ---")
    tts_provider = EdgeTTSProvider()
    for s in selected:
        build_scene_audio(s, tts_provider)

    print("\n--- Stage 2/5: Timing (ffprobe measured duration) ---")
    for s in selected:
        audio_path = config.ROOT / s["audio_file"]
        s["measured_duration"] = timing.probe_duration(audio_path)
    timing.print_timing_table(selected, all_scenes)

    img_provider = make_provider()
    print(f"\n--- Stage 3/5: Images ({config.IMAGE_PROVIDER}: "
          f"{config.describe_image_settings()}, cached) ---")
    for s in selected:
        get_image(s, img_provider)

    if upscale:
        print("\n--- Stage 4/5: Upscale (Real-ESRGAN 4x, local/offline, cached) ---")
        for s in selected:
            get_upscaled_image(s)
    else:
        # assemble.render_scene_clip() falls back to the raw build/images/NN.*
        # when no upscaled file exists, so skipping here is safe. Note this flag
        # is now largely redundant: at native 4K sources get_upscaled_image()
        # skips itself anyway, so the two paths produce identical output.
        print("\n--- Stage 4/5: Upscale SKIPPED (--no-upscale) -> using raw sources ---")

    print("\n--- Stage 5/5: Assemble (ffmpeg: per-scene clip -> xfade -> mux) ---")
    clip_paths = [render_scene_clip(s) for s in selected]

    merged_video = config.BUILD_DIR / f"_merged_video_{label}.mp4"
    merged_audio = config.BUILD_DIR / f"_merged_audio_{label}.mp3"
    print(f"  xfade-chaining {len(clip_paths)} clip(s) -> {merged_video.name}")
    xfade_chain(clip_paths, merged_video)

    audio_paths = [config.ROOT / s["audio_file"] for s in selected]
    print(f"  concatenating {len(audio_paths)} audio file(s) -> {merged_audio.name}")
    timing.concat_audio_files(audio_paths, merged_audio)

    normalized_audio = config.BUILD_DIR / f"_normalized_audio_{label}.wav"
    print(f"  loudness-normalizing (two-pass, target -14 LUFS) -> {normalized_audio.name}")
    normalize_loudness(merged_audio, normalized_audio)

    print(f"  muxing -> {output_path}")
    mux_audio(merged_video, normalized_audio, output_path)

    print(f"\n=== Done: {output_path} ===")


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--indices", type=str, help="Comma-separated scene indices, e.g. 1,2,5,34,65")
    group.add_argument("--all", action="store_true", help="Render all 69 scenes")
    ap.add_argument("--label", type=str, default=None, help="Label for merged intermediate/output filenames")
    ap.add_argument("--out", type=str, default=None, help="Output mp4 path override")
    ap.add_argument("--no-upscale", action="store_true",
                    help="Force-skip the Real-ESRGAN stage. Rarely needed: at native "
                         "4K sources the upscaler already skips itself.")
    args = ap.parse_args()

    if args.all:
        meta, all_scenes = parse.build_scenes()
        indices = [s["index"] for s in all_scenes]
        label = args.label or "full"
        out = config.ROOT / args.out if args.out else config.FINAL_VIDEO
    else:
        indices = [int(x.strip()) for x in args.indices.split(",") if x.strip()]
        label = args.label or "checkpoint_" + "-".join(str(i) for i in indices)
        out = config.ROOT / args.out if args.out else config.OUTPUT_DIR / f"{label}.mp4"

    run(indices, out, label, upscale=not args.no_upscale)


if __name__ == "__main__":
    main()
