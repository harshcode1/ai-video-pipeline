"""Local, offline 4x super-resolution via Real-ESRGAN (ncnn-vulkan build --
no PyTorch/CUDA dependency, runs on any Vulkan-capable GPU including Intel
integrated/Arc). Zero-cost, no API, no account.

Pollinations' anonymous tier silently caps every image at 1024x576
regardless of requested width/height (verified empirically: 4 different
width/height combos and 5 model names all returned identical 1024x576).
Naively bicubic-upscaling that to 1080p is a real source of softness.
Real-ESRGAN reconstructs plausible detail instead of just blurring pixels.
"""
import random
import subprocess
import time
from pathlib import Path

import config

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
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(config.REALESRGAN_EXE.parent))
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


def get_upscaled_image(scene: dict) -> Path:
    raw_path = config.ROOT / scene["image_file"]
    out_path = config.IMAGES_UPSCALED_DIR / f"{scene['index']:02d}.png"
    if _is_valid_output(out_path):
        print(f"  [UPSCALE] scene {scene['index']:02d} ({scene['timestamp']}) cached, skipping")
        return out_path
    print(f"  [UPSCALE] scene {scene['index']:02d} ({scene['timestamp']}) 1024x576 -> 4x")
    return upscale(raw_path, out_path)
