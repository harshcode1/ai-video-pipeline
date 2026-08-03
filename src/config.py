"""Single source of truth for paths, voice, and prosody config."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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

TTS_MAX_RETRIES = 5
TTS_BACKOFF_BASE_SECONDS = 2.0

# --- Images ---
IMAGE_SEED = 424242  # fixed across all 69 requests for style consistency
IMAGE_MODEL = "flux"
IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080
POLLINATIONS_MIN_INTERVAL_SECONDS = 15.0  # anonymous rate limit
IMAGE_MAX_RETRIES = 6
IMAGE_BACKOFF_BASE_SECONDS = 3.0

# --- Video ---
FPS = 30
CROSSFADE_SECONDS = 1.0
ZOOM_MAX = 1.25     # was 1.5. The source images are only 1024x576 natively, so
                    # every extra bit of zoom crops+enlarges and MAGNIFIES their
                    # softness. 1.25 still gives clear motion but stops the zoom
                    # from turning mild source-blur into obvious mush.
ZOOM_STEP = 0.0006  # calmer baseline motion (reference video measured ~2.5 mean
                    # frame-diff vs our old 3.5 at step=0.0008; this is the trade)

# --- Cinematic finish (applied to the FINAL 1080p frame, after zoompan) ---
# The whole "looks like an AI slideshow vs. a real video" gap comes down to four
# things stacked here, each A/B-picked by eye on scenes 1 (fog) and 65 (corridor):
#
#   1. SHARPEN  -- the Pollinations source is a soft 1024x576 and the realesrgan
#      *photo* model smooths painterly brushwork further, so the net reads blurry.
#      Unsharp on the final pixels (after zoompan, so we sharpen what's actually
#      shown, not a version the zoom will re-soften) snaps edges back. amount=1.2
#      snaps without haloing. This is why we don't re-upscale.
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

# Real-ESRGAN gives genuine 4096x2304 detail (not bicubic-interpolated), so
# the old scale=8000 pre-zoompan upscale (needed when source was a raw
# 1024x576 Pollinations image) would just re-blur real detail for no benefit.
# 3840 is a mild downscale from the 4096 source -- cheap, still far more
# working resolution than the 1920x1080 output target needs for smooth zoom.
SCALE_PRE_ZOOMPAN_WIDTH = 3840

# Per-scene zoompan motion variants (selected by scene index % 3) so all 69
# clips aren't mechanically identical -- pure center zoom-in was flagged as
# visually monotonous vs. the reference, which varies its motion.
ZOOM_PAN_VARIANTS = [
    {"pan_x": 0, "pan_y": 0},      # variant 0: pure center zoom-in
    {"pan_x": 30, "pan_y": -10},   # variant 1: zoom-in + slow drift right/up
    {"pan_x": -30, "pan_y": 15},   # variant 2: zoom-in + slow drift left/down
]
