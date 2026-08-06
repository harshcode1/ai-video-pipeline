"""Preflight environment check. Run this BEFORE a render -- it answers
"will this machine actually complete a run, and with or without upscaling?"
deterministically, instead of finding out two stages in.

Exits 0 if a render is possible (see the recommended command it prints),
1 if something is genuinely blocking.

  python src/doctor.py                 # fast checks only
  python src/doctor.py --test-upscale   # also does one real ~45s GPU upscale
"""
import argparse
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# module name -> (pip name, needed for a plain render?)
DEPS = {
    "edge_tts": ("edge-tts", True),
    "requests": ("requests", True),
    "PIL": ("pillow", True),
    "watchdog": ("watchdog", False),
    "numpy": ("numpy", False),
    "librosa": ("librosa", False),
    "pyloudnorm": ("pyloudnorm", False),
    "parselmouth": ("praat-parselmouth", False),
    "cv2": ("opencv-python", False),
}

results = []


def report(status, label, detail=""):
    results.append((status, label, detail))
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))


def check_python():
    v = sys.version_info
    label = f"Python {v.major}.{v.minor}.{v.micro}"
    # `str | None` unions in the source require 3.10+
    if (v.major, v.minor) >= (3, 10):
        report(PASS, label)
    else:
        report(FAIL, label, "need 3.10 or newer (source uses `X | None` unions)")


def check_ffmpeg():
    try:
        import ffmpeg_utils  # raises at import time if either binary is missing
        report(PASS, "ffmpeg", ffmpeg_utils.FFMPEG)
        report(PASS, "ffprobe", ffmpeg_utils.FFPROBE)
    except FileNotFoundError as e:
        report(FAIL, "ffmpeg/ffprobe", str(e).split(". ")[0]
               + ". Install: winget install --id Gyan.FFmpeg -e")
    except Exception as e:
        report(FAIL, "ffmpeg/ffprobe", f"unexpected error: {e}")


def check_deps():
    for mod, (pip_name, required) in DEPS.items():
        try:
            importlib.import_module(mod)
            report(PASS, f"import {mod}")
        except ImportError:
            hint = f"pip install {pip_name}"
            if required:
                report(FAIL, f"import {mod}", f"required -- {hint}")
            else:
                report(WARN, f"import {mod}", f"optional (forensics/watch only) -- {hint}")


def check_image_api():
    """The image provider needs a key since Aug 2026 (Pollinations went paid --
    see config.py). Without it a render dies at stage 3, after the TTS work."""
    import os
    import config
    key = os.environ.get(config.IMAGE_API_KEY_ENV, "").strip()
    if not key:
        report(FAIL, "image API key",
               f"{config.IMAGE_API_KEY_ENV} is not set. Create a key at "
               f"https://aistudio.google.com/apikey (billing must be enabled on its "
               f"project -- image generation has no free tier since Aug 2026), then "
               f'run: setx {config.IMAGE_API_KEY_ENV} "your-key"')
        return
    report(PASS, "image API key", f"{config.IMAGE_API_KEY_ENV} set ({len(key)} chars)")
    report(PASS, "image settings",
           f"{config.IMAGE_MODEL} @ {config.IMAGE_ASPECT_RATIO} {config.IMAGE_SIZE}")
    # A valid key is NOT sufficient any more, and this is the failure people
    # actually hit. Image generation has no free tier as of Aug 2026, so an
    # unbilled project returns a zero quota. We can't detect that without
    # spending a real request, so say it plainly rather than let a render get
    # two stages in and die.
    report(WARN, "image billing",
           "a key alone is not enough -- image generation has no free tier since "
           "Aug 2026. If the key's Google Cloud project has no billing enabled, "
           "every image fails with a zero quota. Est. cost at these settings: "
           "~$0.15/image (~$10 for a 69-scene video).")


def check_inputs():
    script = ROOT / "input" / "Script.txt"
    visuals = ROOT / "input" / "Visuals.txt"
    for p in (script, visuals):
        if p.exists() and p.stat().st_size > 0:
            report(PASS, f"input/{p.name}", f"{p.stat().st_size:,} bytes")
        else:
            report(WARN, f"input/{p.name}", "missing -- see input/README.md for the format")

    if not (script.exists() and visuals.exists()):
        return
    # parse.py is the real validator: it enforces that the two timestamp sets
    # match exactly, so just let it speak.
    # encoding is pinned to utf-8: parse.py echoes the script text, and on a
    # Windows cp1252 console the default decode blows up on Devanagari (or any
    # non-Latin-1 script) and silently hands back stdout=None.
    proc = subprocess.run([sys.executable, str(ROOT / "src" / "parse.py")],
                          capture_output=True, text=True, cwd=str(ROOT),
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    if proc.returncode == 0:
        scene_line = [l for l in stdout.splitlines() if "scenes ->" in l]
        report(PASS, "parse.py", scene_line[0].strip() if scene_line else "parsed OK")
        for line in stderr.splitlines():
            if "WARNING" in line:
                report(WARN, "parse.py", line.strip())
    else:
        tail = (stderr or stdout).strip().splitlines()
        report(FAIL, "parse.py", tail[-1] if tail else "failed")


def _run_test_file(name: str, label: str):
    proc = subprocess.run([sys.executable, str(ROOT / "tests" / name)],
                          capture_output=True, text=True, cwd=str(ROOT),
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    summary = [l for l in (proc.stdout or "").splitlines() if "passed" in l]
    detail = summary[-1].strip() if summary else ("passed" if proc.returncode == 0 else "failed")
    report(PASS if proc.returncode == 0 else FAIL, label, detail)


def check_unit_tests(ffmpeg_ok: bool):
    """Two things worth proving before spending hours on a full run: the xfade
    offset math (a silent bug there corrupts every join in the video), and the
    image-API error classification (a misclassified failure wastes minutes per
    scene and reports the wrong cause)."""
    # The image tests stub out the network and never import assemble, so they
    # run with or without ffmpeg.
    _run_test_file("test_images.py", "unit tests (images)")

    if not ffmpeg_ok:
        # These are pure math, but they import assemble, which imports
        # ffmpeg_utils at module level -- so they cannot even load without
        # ffmpeg present. Report honestly as skipped rather than as a failure.
        report(WARN, "unit tests (assemble)",
               "skipped -- cannot import assemble.py without ffmpeg installed")
        return
    _run_test_file("test_assemble.py", "unit tests (assemble)")


def check_upscaler(test_upscale=False):
    import config
    exe = config.REALESRGAN_EXE
    if not exe.exists():
        # Not a problem any more: images arrive at 3840x2160, so upscale.py
        # auto-skips regardless of whether this binary exists.
        report(PASS, "Real-ESRGAN binary", "absent, and not needed at 4K sources")
        return
    models = exe.parent / "models"
    if not (models / "realesrgan-x4plus.bin").exists():
        report(PASS, "Real-ESRGAN models", "incomplete, and not needed at 4K sources")
        return
    report(PASS, "Real-ESRGAN binary", str(exe))

    if not test_upscale:
        report(WARN, "Real-ESRGAN GPU test", "not run -- pass --test-upscale to verify "
                                             "the GPU actually completes an image (~45s)")
        return

    # Any extension: sources are JPEG now, and were PNG before (see config.py).
    sample = next((p for p in sorted((ROOT / "build" / "images").iterdir())
                   if p.is_file()), None) if (ROOT / "build" / "images").exists() else None
    sample = sample or exe.parent / "input.jpg"
    if not Path(sample).exists():
        report(WARN, "Real-ESRGAN GPU test", "no sample image available to test with")
        return

    from upscale import upscale as do_upscale
    out = ROOT / "build" / "_doctor_upscale_test.png"
    try:
        do_upscale(Path(sample), out)
        report(PASS, "Real-ESRGAN GPU test", f"{out.stat().st_size:,} bytes from {Path(sample).name}")
    except Exception as e:
        report(WARN, "Real-ESRGAN GPU test", f"failed ({type(e).__name__}) -- harmless, "
                                             f"it is skipped at 4K anyway. Detail: {str(e)[:160]}")
    finally:
        if out.exists():
            out.unlink()


def check_disk():
    try:
        free_gb = shutil.disk_usage(str(ROOT)).free / 1024 ** 3
    except OSError as e:
        report(WARN, "disk space", f"could not determine: {e}")
        return
    # A full ~18min run measured ~1.5GB of build/ intermediates plus the output.
    if free_gb >= 10:
        report(PASS, "disk space", f"{free_gb:.1f} GB free")
    elif free_gb >= 4:
        report(WARN, "disk space", f"only {free_gb:.1f} GB free -- a full run needs ~5GB "
                                   "of intermediates in build/")
    else:
        report(FAIL, "disk space", f"{free_gb:.1f} GB free -- not enough for a full run")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-upscale", action="store_true",
                    help="Also run one real Real-ESRGAN upscale (~45s) to prove the GPU works")
    args = ap.parse_args()

    print("=" * 68)
    print("PREFLIGHT: ai-video-pipeline")
    print("=" * 68)
    print("\nRuntime")
    check_python()
    check_ffmpeg()
    print("\nPython packages")
    check_deps()
    print("\nImage generation")
    check_image_api()
    print("\nUpscaler (no longer needed -- sources are natively 4K)")
    check_upscaler(args.test_upscale)
    print("\nInputs")
    check_inputs()
    print("\nSystem")
    check_disk()
    check_unit_tests(ffmpeg_ok=any(r[0] == PASS and r[1] == "ffmpeg" for r in results))

    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    upscale_ok = any(r[0] == PASS and r[1] == "Real-ESRGAN binary" for r in results)
    inputs_ok = any(r[0] == PASS and r[1] == "parse.py" for r in results)

    print("\n" + "=" * 68)
    print(f"VERDICT: {len(results) - len(fails) - len(warns)} pass, {len(warns)} warn, {len(fails)} fail")
    print("=" * 68)

    if fails:
        print("\nBLOCKED. Fix these before rendering:")
        for _, label, detail in fails:
            print(f"  - {label}: {detail}")
        return 1

    # No --no-upscale flag is suggested any more: sources arrive at 3840x2160
    # and upscale.py skips itself, whether or not the binary is installed.
    print("\nReady to render.")
    if not inputs_ok:
        print("\nNext: create input/Script.txt + input/Visuals.txt (see input/README.md),")
        print("      then re-run this check.")
    else:
        print("\nCheckpoint (fast, do this first):\n  python src/render.py --indices 1,2,3")
        print("\nFull run (long -- hours):\n  python src/render.py --all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
