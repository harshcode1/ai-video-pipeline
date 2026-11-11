"""Single source of truth for paths, voice, and prosody config."""
import os
from pathlib import Path

# AVP_ROOT redirects every path (tests and one-off runs use it so they never write
# into the real build/ folder). Importing this module does not touch the disk;
# entry points call ensure_dirs().
ROOT = Path(os.environ.get("AVP_ROOT") or Path(__file__).resolve().parent.parent)

INPUT_DIR = ROOT / "input"
SCRIPT_FILE = INPUT_DIR / "Script.txt"
VISUALS_FILE = INPUT_DIR / "Visuals.txt"

BUILD_DIR = ROOT / "build"
AUDIO_DIR = BUILD_DIR / "audio"
IMAGES_DIR = BUILD_DIR / "images"
IMAGES_UPSCALED_DIR = BUILD_DIR / "images_upscaled"
SCENES_DIR = BUILD_DIR / "scenes"
SCENES_JSON = BUILD_DIR / "scenes.json"
VOICEOVER_FILE = BUILD_DIR / "voiceover.mp3"

OUTPUT_DIR = ROOT / "output"
FINAL_VIDEO = OUTPUT_DIR / "final.mp4"

TOOLS_DIR = ROOT / "tools"
REALESRGAN_EXE = TOOLS_DIR / "realesrgan-ncnn-vulkan" / "realesrgan-ncnn-vulkan.exe"

def ensure_dirs() -> None:
    """Create the working folders. Called by the entry points, never at import."""
    for d in (AUDIO_DIR, IMAGES_DIR, IMAGES_UPSCALED_DIR, SCENES_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)

# --- TTS ---
TTS_VOICE = "hi-IN-SwaraNeural"

# Directive tag -> edge-tts prosody kwargs (rate/volume/pitch).
# Order matters: checked top to bottom, first match wins.
EMOTION_PROSODY = {
    "whispers": {"rate": "-10%", "volume": "-40%"},
    "softly": {"rate": "-10%", "volume": "-40%"},
    "dramatic tone": {"rate": "-15%", "pitch": "-20Hz"},
    "curious": {"pitch": "+15Hz"},
    "thoughtful": {"rate": "-8%"},
}
DEFAULT_PROSODY = {"rate": "-5%"}

# Directive tags that mean "insert silence", not "speak this".
PAUSE_MS = {
    "pause": 400,
    "long pause": 900,
}

# Directive tags that carry no prosody/pause meaning of their own
# (delivery color only) but must still be stripped from spoken text.
# sighs / exhales don't map to an EMOTION_PROSODY entry -> fall back to default.
KNOWN_DIRECTIVES = set(EMOTION_PROSODY) | set(PAUSE_MS) | {"sighs", "exhales"}

# Speech is synthesized for several lines at once. 4 keeps the load on the free
# Edge endpoint modest; --tts-workers overrides it for one run.
TTS_WORKERS = 4

# Per-scene ffmpeg clip renders running at once. None = about half the CPU cores
# (each render is already multi-threaded); --clip-workers overrides it.
CLIP_WORKERS = None

TTS_MAX_RETRIES = 5
TTS_BACKOFF_BASE_SECONDS = 2.0

# --- Images ---
# The provider is one setting. Image generation is PAID everywhere now: no provider
# offers a free tier that also gives native 16:9 at 1080p or above (see the README's
# Technical Decisions for the short history). Valid values: "fal", "gemini".
#
#   fal     -- DEFAULT. FLUX.2 Turbo on fal.ai, ~$0.019 per 2048x1152 image (~$1.30
#              for a 69-scene video). Supports a SEED, which holds one visual style
#              across all the scenes.
#   gemini  -- Optional fallback, ~5-8x the cost ($7-10 per video) and no seed. Needs
#              billing enabled on the Google Cloud project behind the key.
IMAGE_PROVIDER = "fal"

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
IMAGE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
IMAGE_MODEL = "gemini-3.1-flash-image"  # supports 16:9 and up to 4K
IMAGE_ASPECT_RATIO = "16:9"
IMAGE_SIZE = "4K"                       # uppercase K is required by the API
# Tried in order; the first is the default. The docs say both are accepted but a
# live run got a 400 for image/png, so images.py falls back to the other entry if
# the API rejects the mime type. Keep it a tuple.
IMAGE_MIME_TYPES = ("image/jpeg", "image/png")
IMAGE_MIME_TYPE = IMAGE_MIME_TYPES[0]
# Extension of cached pictures. Downstream tools identify the format from the
# file's magic bytes, not its name.
IMAGE_FILE_EXT = ".jpg"

# --- fal.ai / FLUX.2 Turbo (the default provider) ---
FAL_API_KEY_ENV = "FAL_KEY"
FAL_ENDPOINT = "https://fal.run/fal-ai/flux-2/turbo"
# ALWAYS send explicit pixels, never a named preset: "landscape_16_9" on this
# 1-megapixel model means 1024x576, BELOW the 1080p output (that preset trap is
# what made old images blurry). Both axes must be 512-2048 (checked against fal's
# OpenAPI schema); 2048x1152 is true 16:9 and the largest allowed.
FAL_IMAGE_WIDTH = 2048
FAL_IMAGE_HEIGHT = 1152
FAL_OUTPUT_FORMAT = "jpeg"        # enum: jpeg | png | webp
FAL_SYNC_MODE = True              # return the image inline instead of a CDN URL
FAL_GUIDANCE_SCALE = 2.5          # schema default; higher = follows prompt harder
FAL_ENABLE_SAFETY_CHECKER = True

# One fixed seed for every scene: same seed + different prompt keeps the look
# coherent. Change it to reroll the whole video's aesthetic; keep it fixed within
# a video. Still put an identical style suffix on every prompt in Visuals.txt.
IMAGE_SEED = 424242

# One request per this many seconds, shared by every thread (see ratelimit.py). It
# keeps 429s rare and bounds the damage if a run goes wrong: images cost money.
IMAGE_MIN_INTERVAL_SECONDS = 6.0
IMAGE_MAX_RETRIES = 6
IMAGE_BACKOFF_BASE_SECONDS = 3.0

# Resolved for whichever provider is active, so the rest of the code and
# doctor.py don't each need to know the provider matrix.
IMAGE_API_KEY_ENV = FAL_API_KEY_ENV if IMAGE_PROVIDER == "fal" else GEMINI_API_KEY_ENV


def describe_image_settings() -> str:
    """One-line summary of what the active provider will actually generate.
    Used by render.py and doctor.py so neither has to know the provider matrix."""
    if IMAGE_PROVIDER == "fal":
        return (f"FLUX.2 Turbo {FAL_IMAGE_WIDTH}x{FAL_IMAGE_HEIGHT} "
                f"seed={IMAGE_SEED} (~$0.019/image)")
    return f"{IMAGE_MODEL} {IMAGE_ASPECT_RATIO} {IMAGE_SIZE} (no seed)"

# --- Video ---
FPS = 30
CROSSFADE_SECONDS = 1.0
ZOOM_MAX = 1.25     # At a 2048px source this crops to 1638px feeding a 1920px frame:
                    # a ~17% upscale at PEAK zoom only, ramping in gradually. Drop to
                    # 1.07 for zero upscaling (but almost no motion). A LOOK decision:
                    # judge it on a 3-scene sample.
ZOOM_STEP = 0.0006  # calmer baseline motion (reference video measured ~2.5 mean
                    # frame-diff vs our old 3.5 at step=0.0008; this is the trade)

# --- Cinematic finish (applied to the FINAL 1080p frame, after zoompan) ---
# The whole "looks like an AI slideshow vs. a real video" gap comes down to four
# things stacked here, each A/B-picked by eye on scenes 1 (fog) and 65 (corridor):
#
#   1. SHARPEN  -- applied to the final pixels (after zoompan, so we sharpen
#      what's actually shown, not a version the zoom will re-soften).
#      amount=1.2 was tuned by eye; if you see halos on high-contrast edges,
#      try 0.6-0.8. A LOOK decision: judge it on a sample before a full run.
#   2. EQ       -- a gentle contrast S-curve + tiny saturation lift. Flat frames
#      are the #1 AI tell; this gives deep blacks and richer amber.
#   3. VIGNETTE -- darkens the frame edges, pulling the eye to the subject and
#      adding the "shot on a lens" feel. Biggest single premium-ness win.
#   4. GRAIN    -- fine per-frame film grain (allf=t = temporal, so it shimmers
#      like real grain). Kills the smooth-plastic look. Kept low (6) so dark
#      areas don't crawl.
SHARPEN_FILTER = "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=1.2"
COLOR_GRADE_FILTER = "eq=contrast=1.06:saturation=1.05"
VIGNETTE_FILTER = "vignette=angle=PI/4.5"
FILM_GRAIN_FILTER = "noise=alls=6:allf=t+u"
FINISH_FILTER = ",".join([SHARPEN_FILTER, COLOR_GRADE_FILTER, VIGNETTE_FILTER, FILM_GRAIN_FILTER])

# The working resolution the zoompan stage operates at, before the final
# 1920x1080 output.
#
# !! THIS MUST TRACK THE PROVIDER'S OUTPUT WIDTH. It is a no-op passthrough only
# when it equals the generated width -- set it higher and assemble.py upscales
# every source before zooming (spending time to add blur), set it lower and it
# throws away detail you paid for. fal/FLUX.2 Turbo caps at 2048, so 2048 it is.
# If you switch IMAGE_PROVIDER to gemini at 4K, raise this to 3840 to match.
SCALE_PRE_ZOOMPAN_WIDTH = 2048

# Zoom/pan motion variants, chosen from each picture's content hash (assemble.zoom_variant)
# so clips aren't mechanically identical, and so moving a scene never changes its motion.
ZOOM_PAN_VARIANTS = [
    {"pan_x": 0, "pan_y": 0},      # variant 0: pure center zoom-in
    {"pan_x": 30, "pan_y": -10},   # variant 1: zoom-in + slow drift right/up
    {"pan_x": -30, "pan_y": 15},   # variant 2: zoom-in + slow drift left/down
]
