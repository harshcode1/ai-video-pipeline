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
# Provider is PLUGGABLE. Two providers have now died under us in the same month
# (Pollinations went paid, Google removed its free image tier), so switching is
# a one-line change here rather than a rewrite. Valid values: "fal", "gemini".
#
#   fal     -- DEFAULT. Black Forest Labs FLUX.2 Turbo hosted on fal.ai.
#              ~$0.019 per 2048x1152 image => ~$1.30 for a 69-scene video.
#              Supports a SEED, which is why it wins on more than price: a fixed
#              seed holds one visual style across all 69 scenes. Google exposes
#              no seed at all, so style consistency there rests entirely on
#              repeating identical prompt wording, which is a weaker lever.
#   gemini  -- Kept working as a fallback and as a quality benchmark. ~5-8x the
#              cost ($6.97/video at 2K, $10.42 at 4K) and no seed.
#
# NOTHING IS FREE ANY MORE. Researched exhaustively Aug 2026: no provider offers
# a recurring free tier that also delivers native 16:9 at 1080p+. The free
# options either cap below 1080p (Cloudflare flux-schnell, SiliconFlow,
# ModelScope) or allow roughly one usable image per day (Cloudflare
# lucid-origin). Don't go looking again without new evidence.
IMAGE_PROVIDER = "fal"
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
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
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
IMAGE_FILE_EXT = ".jpg"

# --- fal.ai / FLUX.2 Turbo (the default provider) ---
FAL_API_KEY_ENV = "FAL_KEY"
FAL_ENDPOINT = "https://fal.run/fal-ai/flux-2/turbo"
# VERIFIED against fal's OpenAPI schema, not assumed: image_size accepts either
# a preset string or an explicit {width, height}, and for this model both must
# be between 512 and 2048. ALWAYS send explicit pixels. The named presets are
# the exact trap that produced the old blurry output -- "landscape_16_9" on a
# 1-megapixel model means 1024x576, which is BELOW the 1920x1080 we render to.
FAL_IMAGE_WIDTH = 2048
FAL_IMAGE_HEIGHT = 1152   # 2048x1152 is true 16:9 and the largest 16:9 allowed
FAL_OUTPUT_FORMAT = "jpeg"        # enum: jpeg | png | webp
FAL_SYNC_MODE = True              # return the image inline instead of a CDN URL
FAL_GUIDANCE_SCALE = 2.5          # schema default; higher = follows prompt harder
FAL_ENABLE_SAFETY_CHECKER = True

# One fixed seed for every scene in the video. This is the style-consistency
# lever we LOST when Pollinations died and Google turned out to expose no seed:
# same seed + different prompt keeps the look coherent across all 69 scenes.
# Change it to reroll the whole video's aesthetic; keep it fixed within a video.
# Still keep the identical style suffix on every prompt in Visuals.txt -- the
# two reinforce each other, and only the suffix survives a provider change.
IMAGE_SEED = 424242

# Paid rate limits are far higher than the old free 10 req/min, but 6s costs
# nothing meaningful against a multi-hour render and keeps 429s rare. It also
# bounds the damage if a run goes wrong: images cost real money now.
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
ZOOM_MAX = 1.25     # At a 2048px source this crops to 2048/1.25 = 1638px
                    # feeding a 1920px frame, i.e. a ~17% upscale at PEAK zoom
                    # only, ramping in gradually. That is mild and was judged an
                    # acceptable trade for an 8x cost reduction -- for scale,
                    # the old Pollinations images were an 87% upscale for the
                    # whole scene, which is what "blurry" actually looked like.
                    # Drop to 1.07 for mathematically zero upscaling (but
                    # almost no motion), or raise it if you prefer the movement
                    # to the sharpness. This is a LOOK decision -- judge it on a
                    # 3-scene sample rather than changing it blind.
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
# 1920x1080 output.
#
# !! THIS MUST TRACK THE PROVIDER'S OUTPUT WIDTH. It is a no-op passthrough only
# when it equals the generated width -- set it higher and assemble.py upscales
# every source before zooming (spending time to add blur), set it lower and it
# throws away detail you paid for. fal/FLUX.2 Turbo caps at 2048, so 2048 it is.
# If you switch IMAGE_PROVIDER to gemini at 4K, raise this to 3840 to match.
SCALE_PRE_ZOOMPAN_WIDTH = 2048

# Per-scene zoompan motion variants (selected by scene index % 3) so all 69
# clips aren't mechanically identical -- pure center zoom-in was flagged as
# visually monotonous vs. the reference, which varies its motion.
ZOOM_PAN_VARIANTS = [
    {"pan_x": 0, "pan_y": 0},      # variant 0: pure center zoom-in
    {"pan_x": 30, "pan_y": -10},   # variant 1: zoom-in + slow drift right/up
    {"pan_x": -30, "pan_y": 15},   # variant 2: zoom-in + slow drift left/down
]
