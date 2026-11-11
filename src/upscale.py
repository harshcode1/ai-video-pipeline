"""Local, offline 4x super-resolution via Real-ESRGAN (ncnn-vulkan build --
no PyTorch/CUDA dependency, runs on any Vulkan-capable GPU including Intel
integrated/Arc). Zero-cost, no API, no account.

Rarely used now: the default image provider (fal) generates 2048x1152, which already
meets the working resolution of the zoompan stage (config.SCALE_PRE_ZOOMPAN_WIDTH), so
get_upscaled_image() detects that and returns the source untouched. The code is kept
working for a provider that returns smaller images (e.g. 1024x576 sources, where
Real-ESRGAN reconstructs plausible detail instead of just blurring pixels).
"""
import random
import time
from pathlib import Path

import cache
import config
import ffmpeg_utils

# Part of the upscaled picture's cache key (see upscale_key).
UPSCALER_SETTINGS = {"model": "realesrgan-x4plus", "scale": 4}

MIN_VALID_BYTES = 500_000  # a real 4x upscale of a painting is multi-MB; a
                            # fully-dead GPU buffer lands under 30KB.

# ROOT CAUSE of the `vkQueueSubmit failed -4` (VK_ERROR_DEVICE_LOST) storms:
# realesrgan-ncnn-vulkan auto-picks a tile size from reported GPU memory, and
# on this shared-memory Intel Arc iGPU it picks one too large to actually
# submit -- the queue dies mid-image and the un-rendered tiles come back as
# exactly-zero buffers. Measured directly on one image:
#     tile auto -> 13-22 faults, output CORRUPT (18/36 dead blocks)
#     tile 200  -> 22 faults,    output CORRUPT   (8s)
#     tile 128  -> 0 faults,     output VALID    (46s)
#     tile 64   -> 0 faults,     output VALID
# So this is a memory-pressure bug, not a transient driver hiccup, and no
# amount of waiting/backoff fixes it -- only a smaller tile does. Retries
# therefore step the tile DOWN rather than just sleeping longer. Backoff is
# kept small and only covers genuinely transient cases (e.g. the GPU being
# suspended by a laptop lid close mid-render, which we also hit).
TILE_SIZES = [128, 96, 64, 32]
BACKOFF_BASE_SECONDS = 2.0

# vkQueueSubmit device-lost faults observed mid-render produce PARTIAL
# corruption too -- e.g. the top third of a frame rendered fine, the rest is
# an exactly-zero dead GPU buffer. A whole-image std check averages right
# past that (the good top drags the mean up). Grid-check instead: every
# block must clear the threshold, so a dead region anywhere gets caught.
# Threshold is deliberately low (0.5) so legitimately near-black artistic
# regions (this whole style is dark/noir) don't false-positive -- real
# content always has at least faint film-grain-level variation; a dead
# GPU buffer is exactly 0.0 std, not just dark.
_GRID = 6
_DEAD_BLOCK_STD = 0.5


def _is_valid_output(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < MIN_VALID_BYTES:
        return False
    try:
        from PIL import Image
        import numpy as np
        arr = np.array(Image.open(path).convert("RGB")).astype(np.float32)
        h, w = arr.shape[:2]
        for i in range(_GRID):
            for j in range(_GRID):
                block = arr[i * h // _GRID:(i + 1) * h // _GRID, j * w // _GRID:(j + 1) * w // _GRID]
                if block.std() < _DEAD_BLOCK_STD:
                    return False
        return True
    except Exception:
        return False


def upscale(in_path: Path, out_path: Path) -> Path:
    if _is_valid_output(out_path):
        return out_path

    if not config.REALESRGAN_EXE.exists():
        raise FileNotFoundError(
            f"realesrgan-ncnn-vulkan.exe not found at {config.REALESRGAN_EXE}. "
            f"Download from https://github.com/xinntao/Real-ESRGAN/releases"
        )

    last_err = None
    for attempt, tile in enumerate(TILE_SIZES, start=1):
        if out_path.exists():
            out_path.unlink()
        cmd = [
            str(config.REALESRGAN_EXE),
            "-i", str(in_path),
            "-o", str(out_path),
            "-n", "realesrgan-x4plus",
            "-t", str(tile),
        ]
        result = ffmpeg_utils.run(cmd, cwd=str(config.REALESRGAN_EXE.parent))
        if result.returncode == 0 and _is_valid_output(out_path):
            return out_path

        faults = result.stderr.count("vkQueueSubmit failed") if result.stderr else 0
        last_err = f"tile={tile} returncode={result.returncode} vkQueueSubmit_faults={faults}"
        if attempt < len(TILE_SIZES):
            sleep_s = BACKOFF_BASE_SECONDS * attempt + random.uniform(0, 1)
            print(f"    upscale attempt {attempt}/{len(TILE_SIZES)} failed ({last_err}); "
                  f"retrying with tile={TILE_SIZES[attempt]} in {sleep_s:.1f}s")
            time.sleep(sleep_s)

    raise RuntimeError(
        f"realesrgan upscale failed at every tile size {TILE_SIZES} for {in_path}: {last_err}. "
        f"If vkQueueSubmit_faults is high at tile=32, the GPU is likely wedged "
        f"(e.g. suspended by a laptop lid close) -- reset the graphics driver "
        f"(Win+Ctrl+Shift+B) or reboot, then rerun. All completed work is cached."
    )


def _source_width(path: Path) -> int | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size[0]
    except Exception:
        return None


def upscale_key(source_key: str) -> str:
    """Key of an upscaled picture: the source picture's key plus the upscaler
    settings. A new source picture can therefore never pick up an old upscale."""
    return cache.short_hash(cache.STAGE_VERSIONS["upscale"], source_key, UPSCALER_SETTINGS)


def use_raw_source(scene: dict) -> Path:
    """Point the scene at its un-upscaled picture (used when upscaling is skipped)."""
    scene["source_file"] = scene["image_file"]
    scene["source_key"] = scene["image_key"]
    return config.ROOT / scene["source_file"]


def get_upscaled_image(scene: dict) -> Path:
    """Decide which picture the clip stage will use, and make it exist.

    Sets scene["source_file"] / scene["source_key"]. The clip stage reads those
    -- it never looks a file up by scene number, so a stale upscaled file from an
    earlier script can never win over the current picture.
    """
    raw_path = config.ROOT / scene["image_file"]
    label = f"scene {scene['index']:02d} ({scene['timestamp']})"

    # fal's 2048px images already meet the working resolution the zoompan stage
    # runs at, so upscaling would cost ~46s per image to add only resampling
    # softness. Real-ESRGAN runs only for a smaller source.
    width = _source_width(raw_path)
    if width is not None and width >= config.SCALE_PRE_ZOOMPAN_WIDTH:
        print(f"  [UPSCALE] {label} source already {width}px wide -- no upscale needed")
        return use_raw_source(scene)

    ukey = upscale_key(scene["image_key"])
    out_path = config.IMAGES_UPSCALED_DIR / f"{ukey}.png"
    scene["source_key"] = ukey
    scene["source_file"] = out_path.relative_to(config.ROOT).as_posix()
    if _is_valid_output(out_path):
        print(f"  [UPSCALE] {label} cached, skipping")
        return out_path

    print(f"  [UPSCALE] {label} {width or '?'}px -> 4x")
    return cache.atomic_write(out_path, lambda tmp: upscale(raw_path, tmp))
