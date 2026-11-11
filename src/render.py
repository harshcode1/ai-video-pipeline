"""Pipeline orchestrator. Every stage is resumable: its output is named by a hash
of everything that affects it (see cache.py), and a stage is skipped when that
exact file already exists. Rerunning after a crash, or after editing the script,
redoes only what actually changed.

Usage:
  python src/render.py --indices 1,2,5,34,65   # checkpoint / partial run
  python src/render.py --all                    # full run
  python src/render.py --all --no-upscale       # force-skip Real-ESRGAN (usually
                                                # unnecessary -- it auto-skips when the
                                                # source already meets the working width)

Requires an image API key for the active provider (FAL_KEY by default; see
README). Run src/doctor.py to verify first.
"""
import argparse
import re
import shutil
from pathlib import Path

import cache
import config
import parse
import timing
from assemble import final_key, mux_audio, render_scene_clip, video_key, xfade_chain
from audio import build_all_audio
from images import get_image, make_provider
from loudness import mix_key
from loudness import normalize as normalize_loudness
from tts import EdgeTTSProvider
from upscale import get_upscaled_image, use_raw_source

# Files named like 01.mp3 / 01_speech.mp3 / 01.jpg were produced by versions that
# cached by scene number. They are never used (they may belong to different
# content) and never deleted.
_LEGACY_NAME = re.compile(r"^\d+(_speech)?\.\w+$")


def _note_legacy_files() -> None:
    found = []
    for folder in (config.AUDIO_DIR, config.IMAGES_DIR, config.IMAGES_UPSCALED_DIR, config.SCENES_DIR):
        if folder.is_dir():
            found += [p for p in folder.iterdir() if p.is_file() and _LEGACY_NAME.match(p.name)]
    if found:
        example = sorted(found)[0].relative_to(config.ROOT).as_posix()
        print(f"  note: {len(found)} index-named file(s) from earlier builds (e.g. {example}) "
              f"are no longer used. They are safe to delete; nothing deletes them for you.\n")


def _build_mix(audio_paths: list, out_tmp: Path) -> None:
    merged = cache.temp_path_for(out_tmp.with_suffix(".mp3"))
    try:
        print(f"  concatenating {len(audio_paths)} audio file(s)")
        timing.concat_audio_files(audio_paths, merged)
        print("  loudness-normalizing (two-pass, target -14 LUFS)")
        normalize_loudness(merged, out_tmp)
    finally:
        merged.unlink(missing_ok=True)


def assemble_final(selected: list, clip_paths: list, output_path: Path) -> None:
    """Crossfade the clips, build the normalized voiceover, mux. Each of the three
    products is cached under its own key, so e.g. changing only the loudness
    target redoes the audio and the mux but not the video chain."""
    final_dir = config.BUILD_DIR / "final"
    vkey = video_key([s["clip_key"] for s in selected])
    mkey = mix_key([s["audio_key"] for s in selected])
    video_path = final_dir / f"video_{vkey}.mp4"
    mix_path = final_dir / f"mix_{mkey}.wav"
    final_path = final_dir / f"final_{final_key(vkey, mkey)}.mp4"

    if cache.is_valid(final_path):
        print("  final video already built for these exact inputs, reusing it")
    else:
        if cache.is_valid(video_path):
            print(f"  crossfaded video cached ({video_path.name})")
        else:
            print(f"  xfade-chaining {len(clip_paths)} clip(s) -> {video_path.name}")
            cache.atomic_write(video_path, lambda tmp: xfade_chain(clip_paths, tmp))

        audio_paths = [config.ROOT / s["audio_file"] for s in selected]
        if cache.is_valid(mix_path):
            print(f"  mixed voiceover cached ({mix_path.name})")
        else:
            cache.atomic_write(mix_path, lambda tmp: _build_mix(audio_paths, tmp))

        print(f"  muxing -> {final_path.name}")
        cache.atomic_write(final_path, lambda tmp: mux_audio(video_path, mix_path, tmp))

    print(f"  delivering -> {output_path}")
    cache.atomic_write(output_path, lambda tmp: shutil.copyfile(final_path, tmp))


def run(indices: list, output_path, label: str, upscale: bool = True,
        tts_provider=None, image_provider=None, scenes=None,
        tts_workers=None, clip_workers=None):
    """tts_provider / image_provider / scenes can be injected (tests use fakes)."""
    config.ensure_dirs()
    output_path = Path(output_path)
    print(f"=== render.py: {label} ({len(indices)} scene(s): {indices}) ===\n")

    meta, all_scenes = scenes if scenes is not None else parse.build_scenes()
    by_index = {s["index"]: s for s in all_scenes}
    for i in indices:
        if i not in by_index:
            raise ValueError(f"Scene index {i} does not exist (valid range 1-{len(all_scenes)})")

    selected = [by_index[i] for i in indices]
    _note_legacy_files()

    tts_workers = config.TTS_WORKERS if tts_workers is None else tts_workers
    print(f"--- Stage 1/5: TTS (edge-tts, per-line, {tts_workers} at a time) ---")
    tts_provider = tts_provider or EdgeTTSProvider()
    build_all_audio(selected, tts_provider, workers=tts_workers)

    print("\n--- Stage 2/5: Timing (ffprobe measured duration) ---")
    for s in selected:
        s["measured_duration"] = timing.probe_duration(config.ROOT / s["audio_file"])
    timing.print_timing_table(selected, all_scenes)

    image_provider = image_provider or make_provider()
    print(f"\n--- Stage 3/5: Images ({image_provider.fingerprint()['provider']}, cached by content) ---")
    for s in selected:
        get_image(s, image_provider)

    if upscale:
        print("\n--- Stage 4/5: Upscale (Real-ESRGAN 4x, local/offline, cached) ---")
        for s in selected:
            get_upscaled_image(s)
    else:
        print("\n--- Stage 4/5: Upscale SKIPPED (--no-upscale) -> using the original pictures ---")
        for s in selected:
            use_raw_source(s)

    print("\n--- Stage 5/5: Assemble (ffmpeg: per-scene clip -> xfade -> mux) ---")
    clip_paths = [render_scene_clip(s) for s in selected]
    assemble_final(selected, clip_paths, output_path)

    print(f"\n=== Done: {output_path} ===")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--indices", type=str, help="Comma-separated scene indices, e.g. 1,2,5,34,65")
    group.add_argument("--all", action="store_true", help="Render every scene in the script")
    ap.add_argument("--label", type=str, default=None, help="Label used to name the output file")
    ap.add_argument("--out", type=str, default=None, help="Output mp4 path override")
    ap.add_argument("--no-upscale", action="store_true",
                    help="Force-skip the Real-ESRGAN stage. Rarely needed: when the "
                         "source already meets the working width the upscaler skips itself.")
    ap.add_argument("--tts-workers", type=int, default=None,
                    help=f"Speech lines synthesized at once (default {config.TTS_WORKERS})")
    ap.add_argument("--clip-workers", type=int, default=None,
                    help="Scene clips rendered at once (default: about half the CPU cores)")
    return ap


def main():
    args = build_parser().parse_args()

    if args.all:
        meta, all_scenes = parse.build_scenes()
        indices = [s["index"] for s in all_scenes]
        label = args.label or "full"
        out = config.ROOT / args.out if args.out else config.FINAL_VIDEO
    else:
        indices = [int(x.strip()) for x in args.indices.split(",") if x.strip()]
        label = args.label or "checkpoint_" + "-".join(str(i) for i in indices)
        out = config.ROOT / args.out if args.out else config.OUTPUT_DIR / f"{label}.mp4"

    run(indices, out, label, upscale=not args.no_upscale,
        tts_workers=args.tts_workers, clip_workers=args.clip_workers)


if __name__ == "__main__":
    main()
