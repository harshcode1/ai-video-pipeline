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
# Provider: Google Gemini image generation (free tier, key from Google AI Studio).
#
# WHY WE LEFT POLLINATIONS (verified live 2026-08-05, not assumed):
#   1. It stopped being free. Every genuinely new prompt now returns
#      402 PAYMENT_REQUIRED -- "This request costs ~0.0001 pollen, but your
#      available balance is 0.0000." The `flux` model was removed from the
#      anonymous model list entirely (only `sana` remains, and it bills too).
#      Their marketing still says "Flux -- free, unlimited"; the live API
#      disagrees. Note that previously-generated prompts still return instantly
#      from their cache, which makes the service look alive when nothing new
#      can be made -- don't be fooled by a passing spot-check.
#   2. It was the real cause of the soft/blurry look. Pollinations silently
#      capped EVERY response at 1024x576 regardless of the width/height asked
#      for (the server echoed its own smaller size back in the response). A
#      1080p frame needs ~2.07M pixels; 1024x576 is 0.59M. The missing ~72%
#      was Real-ESRGAN inventing plausible detail. No sharpen/grain tuning can
#      recover detail that was never generated -- the fix had to be a bigger
#      source image, not a better filter.
# !! IMAGE GENERATION IS NO LONGER FREE ANYWHERE. Verified Aug 2026 against
# Google's own pricing page: EVERY image model -- gemini-3.1-flash-image,
# gemini-3.1-flash-lite-image, gemini-3-pro-image, gemini-2.5-flash-image and
# Imagen 4 -- lists "Free Tier: not available". A valid API key on a
# non-billed account is not broken; it simply has a quota of exactly zero, and
# no amount of waiting or retrying changes that. Billing must be enabled on the
# Google Cloud project behind the key. Google's $300 new-account credit covers
# roughly 28 full videos at the settings below.
#
# Cost per image at 16:9 (standard, non-batch):
#     1K  $0.067   -> 1024x576, BELOW our 1080p output. Never use it: this is
#                    exactly the resolution that made Pollinations look blurry.
#     2K  $0.101   -> 2048x1152. Above 1080p, but at ZOOM_MAX=1.25 the crop
#                    leaves only 1638px feeding a 1920px frame, so the end of
#                    each zoom softens slightly.
#     4K  $0.151   -> 3840x2160. Full headroom at any zoom. This is the default.
# A 69-scene video therefore costs about $10.42 at 4K, or $6.97 at 2K.
IMAGE_API_KEY_ENV = "GEMINI_API_KEY"
IMAGE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
IMAGE_MODEL = "gemini-3.1-flash-image"  # supports 16:9 and up to 4K (flash-lite caps at 1K)
IMAGE_ASPECT_RATIO = "16:9"
# Uppercase K is required by the API ("4K", not "4k"). 4K = 3840x2160, which is
# EXACTLY SCALE_PRE_ZOOMPAN_WIDTH below -- so the zoompan stage resamples
# nothing, and Real-ESRGAN becomes unnecessary (upscale.py now auto-skips when
# the source is already this big, cutting ~50 min off a full render).
IMAGE_SIZE = "4K"
# Tried in order; the first is the default. JPEG is first because Google's own
# REST example for this endpoint uses "image/jpeg", and a live run reported a
# 400 invalid_request when sending "image/png" -- even though the prose docs
# list both as accepted. Since the docs and the live API disagree and the
# disagreement can only be observed with a valid key, images.py starts with
# JPEG and automatically falls back to the other entry if the API rejects the
# mime type. Do not collapse this back to a single string.
IMAGE_MIME_TYPES = ("image/jpeg", "image/png")
IMAGE_MIME_TYPE = IMAGE_MIME_TYPES[0]
# Extension for build/images/NN.<ext>, derived so the filename doesn't lie
# about its contents. If the runtime mime fallback above kicks in, the bytes
# are still written to this path -- every downstream consumer (PIL, ffmpeg,
# Real-ESRGAN) identifies the format from the file's magic bytes, not its name.
IMAGE_FILE_EXT = ".jpg" if IMAGE_MIME_TYPE == "image/jpeg" else ".png"
# Paid-tier limit is far higher than the old free 10 req/min, but 6s costs
# nothing meaningful against a multi-hour render and keeps 429s rare.
IMAGE_MIN_INTERVAL_SECONDS = 6.0
IMAGE_MAX_RETRIES = 6
IMAGE_BACKOFF_BASE_SECONDS = 3.0
# NOTE: the old fixed IMAGE_SEED is gone -- this API exposes no seed parameter.
# Cross-scene style consistency now rests entirely on repeating an identical
# style suffix on every prompt in Visuals.txt, which was always the dominant
# factor of the two. Keep those suffixes byte-identical.

# --- Video ---
FPS = 30
CROSSFADE_SECONDS = 1.0
ZOOM_MAX = 1.25     # Was lowered from 1.5 back when sources were a soft
                    # 1024x576 and every extra bit of zoom magnified that
                    # softness into mush. Sources are now natively 3840x2160,
                    # so that constraint no longer applies -- 1.5 is safe again
                    # if more motion is wanted. Left at 1.25 because it's a
                    # LOOK decision, not a technical one: raise it only after
                    # eyeballing a sample, not by default.
ZOOM_STEP = 0.0006  # calmer baseline motion (reference video measured ~2.5 mean
                    # frame-diff vs our old 3.5 at step=0.0008; this is the trade)

# --- Cinematic finish (applied to the FINAL 1080p frame, after zoompan) ---
# The whole "looks like an AI slideshow vs. a real video" gap comes down to four
# things stacked here, each A/B-picked by eye on scenes 1 (fog) and 65 (corridor):
#
#   1. SHARPEN  -- applied to the final pixels (after zoompan, so we sharpen
#      what's actually shown, not a version the zoom will re-soften).
#      !! REVIEW THIS VALUE. amount=1.2 was tuned to rescue a soft, upscaled
#      1024x576 source. Sources are now natively 3840x2160 and genuinely sharp,
#      and the 4K->1080p downscale softens only slightly -- so some sharpening
#      still helps, but 1.2 may now be too much and can produce visible halos
#      on high-contrast edges. Judge it on a sample before a full run; 0.6-0.8
#      is the likelier sweet spot at this source resolution. Left unchanged
#      because it is a LOOK decision, not a technical one.
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
# 1920x1080 output. Sources now arrive natively at 3840x2160 (IMAGE_SIZE="4K"),
# so this is a no-op passthrough: no upscale, no downscale, no resampling at
# all before the zoom. Every pixel the viewer sees is real generated detail.
#
# It stays comfortably above the 1920x1080 target on purpose -- ZOOM_MAX crops
# into the frame, so at full zoom we still have 3840/1.25 = 3072 real pixels
# feeding a 1920-wide output. That headroom is why the zoom stays sharp instead
# of turning into mush at the end of a scene.
SCALE_PRE_ZOOMPAN_WIDTH = 3840

# Per-scene zoompan motion variants (selected by scene index % 3) so all 69
# clips aren't mechanically identical -- pure center zoom-in was flagged as
# visually monotonous vs. the reference, which varies its motion.
ZOOM_PAN_VARIANTS = [
    {"pan_x": 0, "pan_y": 0},      # variant 0: pure center zoom-in
    {"pan_x": 30, "pan_y": -10},   # variant 1: zoom-in + slow drift right/up
    {"pan_x": -30, "pan_y": 15},   # variant 2: zoom-in + slow drift left/down
]
