# ai-video-pipeline

A reproducible, resumable pipeline that turns a **timestamped narration script**
plus a list of **image prompts** into a finished, loudness-normalized 1080p
narrated video — using local ffmpeg plus one paid image API.

Voiceover is free and keyless. **Image generation is not free**: it needs a
funded [fal.ai](https://fal.ai) account with the key in `FAL_KEY`, and costs
about **$0.019 per image — roughly $1.30 for a 69-scene video**.

The image provider is **pluggable** (`IMAGE_PROVIDER` in `src/config.py`).
Two providers died under this pipeline in a single month, so switching is a
one-line change rather than a rewrite.

> **Aug 2026 — free image generation ended, twice.**
>
> First, Pollinations (the previous keyless provider) moved to prepaid credits:
> new prompts fail with `402 PAYMENT_REQUIRED` at a zero balance, and `flux` was
> dropped from the anonymous model list. It had also silently capped every image
> at 1024×576 regardless of the size requested — the real cause of the soft,
> blurry output, since a 1080p frame needs ~3.5× that many pixels.
>
> The switch to Google Gemini fixed the quality problem for good: images
> generated natively at **16:9 4K (3840×2160)**, so nothing was invented and
> nothing resampled. Real-ESRGAN became unnecessary, cutting ~50 minutes off a
> full render.
>
> But Google's free image tier disappeared in the same month. Its pricing page
> now lists **"Free Tier: not available" for every image model** —
> `gemini-3.1-flash-image`, `-flash-lite-image`, `gemini-3-pro-image`,
> `gemini-2.5-flash-image` and Imagen 4 alike. A valid key on an unbilled
> project isn't broken; it simply has a quota of exactly zero, and no amount of
> retrying changes that.
>
> **So the default moved to FLUX.2 Turbo on fal.ai**, which wins on two counts:
> it's ~8× cheaper ($1.30 vs $10.42 per video), and it exposes a **seed**.
> That seed matters beyond price — one fixed seed holds a single visual style
> across all 69 scenes, which is the consistency lever that was lost when
> Pollinations died and Google turned out to offer no equivalent.
>
> A full survey (Aug 2026) found **no provider offering a recurring free tier
> that also delivers native 16:9 at 1080p or above.** The free options either
> cap below 1080p (Cloudflare flux-schnell, SiliconFlow, ModelScope) or allow
> roughly one usable image per day (Cloudflare lucid-origin). That's a settled
> answer, not a gap in the search.
>
> Full post-mortem in `src/config.py`.

```
input/Script.txt    ─┐
input/Visuals.txt   ─┴─▶ parse ─▶ TTS ─▶ timing ─▶ images ─▶ upscale ─▶ assemble ─▶ output/*.mp4
```

## How it works

| Stage | Module | What it does |
| --- | --- | --- |
| 0. Parse | `src/parse.py` | Joins `Script.txt` + `Visuals.txt` on `MM:SS` timestamps into `build/scenes.json`. Validates that both timestamp sets match exactly. No network. |
| 1. TTS | `src/tts.py`, `src/audio.py` | Per-line Hindi TTS via **edge-tts** (`hi-IN-SwaraNeural`), with per-line prosody from the script's `[directives]` and real inserted silence for `[pause]`. Retries with exponential backoff. |
| 2. Timing | `src/timing.py` | Measures true audio duration with `ffprobe` — scene length follows the *actual* voiceover, not the nominal script timestamps. |
| 3. Images | `src/images.py` | One image per scene from the provider set in `IMAGE_PROVIDER` — by default **FLUX.2 Turbo on fal.ai** at 2048×1152 (true 16:9, above the 1080p output), throttled to 1 per 6s and cached on disk. Classifies API failures so deterministic ones (bad key, no credit, rejected request) fail immediately with the reason instead of burning six retries. Style consistency comes from a **fixed seed** plus an identical style suffix on every prompt. |
| 4. Upscale | `src/upscale.py` | **Auto-skips.** Local Real-ESRGAN 4× is kept for the case where sources are smaller than the working resolution, but 2048×1152 already meets it, so it returns the original untouched. |
| 5. Assemble | `src/assemble.py` | Per-scene `zoompan` motion clip → a cinematic finish filter chain → `xfade` crossfade chain → mux with two-pass EBU R128 loudness-normalized audio (`src/loudness.py`, target −14 LUFS). |

**Every stage is idempotent and cached.** Reruns after a crash only redo what's
missing, so a partial 69-scene render never starts over from zero.

### The parts that took the most tuning

Design decisions are documented inline where they're implemented, not
summarized away — the interesting ones:

- **`src/config.py`** — the "looks like an AI slideshow vs. a real video" gap
  closed by four stacked filters on the *final* frame (sharpen, contrast/sat
  S-curve, vignette, temporal film grain), each A/B-picked by eye, plus
  per-scene zoom/pan variants so 69 clips aren't mechanically identical. Also
  carries the post-mortem on why the image provider changed.
- **`src/images.py`** — failures are *classified* rather than blanket-retried.
  A render is a multi-hour unattended job, so retrying a deterministic failure
  six times both wastes minutes and buries the reason: bad key, no credit and
  rejected-request all stop immediately with the response body intact, while
  rate limits and 5xx still back off. Also over-abstracted on purpose — two
  providers died in one month, so the swap cost matters more here than anywhere
  else. Gemini's undocumented response shape is handled by *walking* the JSON
  for the first plausible base64 blob rather than hard-coding a guessed path.
- **`src/upscale.py`** — `vkQueueSubmit failed -4` (`VK_ERROR_DEVICE_LOST`)
  storms on a shared-memory Intel Arc iGPU traced to Real-ESRGAN's automatic
  tile sizing, with measurements. Retries step the **tile size down** rather
  than sleeping longer, and outputs are grid-validated so partially-dead GPU
  buffers get caught instead of averaging past a whole-image check. Mostly
  historical now that upscaling auto-skips.

## Setup

Requires **Python 3.13** and **ffmpeg/ffprobe** on `PATH`.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt
```

`requirements.txt` is deliberately small and pure-python. The optional QA extras
(numpy, librosa, opencv, praat) are in `requirements-forensics.txt` and are only
needed for `src/forensics.py` — a render never imports them, so a failed opencv
build can't block you from making a video.

ffmpeg on Windows: `winget install --id Gyan.FFmpeg -e`. No shell restart needed
— `ffmpeg_utils.py` scans the winget install path directly.

Then add an image API key — required, the pipeline can't generate pictures
without it:

1. Create one at [fal.ai/dashboard/keys](https://fal.ai/dashboard/keys).
2. **Add credit to the account.** Not optional: no provider has a free tier any
   more, so an unfunded account fails on every image. A 69-scene video costs
   about **$1.30**, so a small top-up lasts many videos.
3. Set the key persistently:

   ```powershell
   setx FAL_KEY "your-key-here"      # future shells
   $env:FAL_KEY = "your-key-here"    # and the current one
   ```

   On Unix: `export FAL_KEY=your-key-here`.

`python src/doctor.py` verifies the key is visible before you start a long
render — but it cannot check your balance without spending a real request, so
if images fail with a "no remaining balance" error, top-up is the fix.

<details><summary>Using Google Gemini instead</summary>

Set `IMAGE_PROVIDER = "gemini"` in `src/config.py`, put a key from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) in
`GEMINI_API_KEY`, and enable billing on that key's Google Cloud project. Also
raise `SCALE_PRE_ZOOMPAN_WIDTH` to 3840 to match 4K output. Costs $6.97–$10.42
per video and offers no seed, so it's kept as a fallback and quality benchmark
rather than the default.

</details>

**Real-ESRGAN is no longer required.** Sources already meet the working
resolution and `upscale.py` skips itself. The binary and its
[v0.2.5.0 download](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip)
remain supported for the case where sources are ever smaller than the working
resolution — extract to `tools/realesrgan-ncnn-vulkan/` if you need it. (Don't
use "latest": the v0.3.0 tag ships no Windows binary.)

Finally, create `input/Script.txt` and `input/Visuals.txt` — see
[`input/README.md`](input/README.md) for the exact format.

## Quick start (no coding required)

If you have [Claude Code](https://claude.com/claude-code), you don't need to read
any of the rest of this. Open this folder in Claude Code and type:

```
/make-video
```

That's the only thing you ever need to type. It will:

1. Check whether your computer has what it needs — and offer to install anything
   missing, including the video engine and the image API key
2. Ask you a few questions (or just take your topic and decide the rest itself)
3. Write the narration script and the image prompts
4. Render a **45-second sample** and stop, so you can check the voice and look
   before committing to a long render
5. Render the whole thing (1–3 hours, runs in the background)
6. Hand you the finished file, plus the title and thumbnail text to upload with

If you'd rather get the install out of the way first, type `/setup` instead.

`CLAUDE.md` is loaded automatically, so Claude already knows how this pipeline
works, what the rate limits are, and how to resume if something crashes midway.

### Who decides what

You keep every creative call. Claude handles every technical one.

| Decision | Who decides |
| --- | --- |
| Topic, angle, and hook | **You** — or say "you pick" and Claude drafts it |
| The narration itself | **You** (paste your own), or Claude drafts and you edit |
| Title and thumbnail text | **You** — Claude offers 2–3 options to choose from |
| Language and how the voice should feel | **You** ("calm", "dramatic", …) |
| Video length | **You** — Claude tells you the render time first |
| Visual style / vibe | **You**, or take the built-in noir preset |
| Sign-off on the 45-second sample | **You** |
| Which actual TTS voice matches "dramatic" | Claude (verified against the real voice list) |
| Scene count, timestamps, pacing directives | Claude |
| Individual image prompt wording | Claude |
| What to install, and resuming after a crash | Claude |
| Creating the image API key **and enabling billing on it** | **You** — it needs your Google login and card (~5 min, one time) |

Everything is asked **up front**, in one round, and every question comes with an
"or you pick for me" option. If you only want to answer one thing, answer the
topic — Claude can decide the rest.

## Usage

Everything below is the manual path.

```bash
# Check your environment first: ffmpeg, deps, upscaler, inputs
python src/doctor.py

# Parse and inspect the scene table -- no network calls, fast sanity check
python src/parse.py

# Render a few scenes as a checkpoint before committing to a full run
python src/render.py --indices 1,2,5,34,65

# Full run
python src/render.py --all

# Full run without Real-ESRGAN (softer output, but no GPU or binary needed)
python src/render.py --all --no-upscale
```

Optional flags: `--label` names the merged intermediates, `--out` overrides the
output path. Output lands in `output/`; all intermediates in `build/`.

**Every stage is cached, so if a run crashes, just rerun the same command** — it
resumes rather than starting over. Don't delete `build/` to "clean up"; that's
the resume cache, and throwing it away costs hours of re-downloading and
re-upscaling.

A full ~69-scene run takes **1–2 hours**. It used to be 1.5–3: dropping the
15s-per-image throttle to 6s saved ~10 min, and eliminating Real-ESRGAN
(~46s per image) saved ~50 min more.

Two extra tools:

```bash
python src/watch.py       # auto-rerun the pipeline when input/ changes
python src/forensics.py   # QA: LUFS, pitch/F0 variation, video motion metrics
```

`forensics.py` is a diagnostic, not part of a render — it quantifies whether the
audio actually sounds expressive (F0 std in semitones, RMS dynamic range) and
whether the video actually reads as moving (mean inter-frame difference), so
"sounds flat" / "looks static" can be measured rather than guessed at.

## Tests

Plain asserts, no pytest dependency:

```bash
python tests/test_assemble.py   # xfade offset math (needs ffmpeg installed)
python tests/test_images.py     # image-API error classification (no network, no ffmpeg)
```

`test_images.py` covers the failure classifier, and every case in it is a bug
that actually shipped: a bad key arrives as HTTP **400** (not 401/403) and used
to be retried six times under the wrong label; a rejected `mime_type` had its
explanation discarded by `raise_for_status()`; and a zero quota was treated as a
transient rate-limit and slept through. Each one costs minutes of a multi-hour
render and names the wrong cause, so the classifier is tested against real
recorded response bodies rather than trusted.

`test_assemble.py` covers `compute_offsets()` — the xfade offset math, where a silent bug would
cascade through every join in the render.

## Repository layout

```
CLAUDE.md      auto-loaded context for Claude Code
.claude/       /make-video workflow command
src/           pipeline modules (config.py is the single source of truth)
src/doctor.py  environment preflight check
tests/         unit tests
input/         Script.txt + Visuals.txt  (gitignored -- see input/README.md)
build/         all intermediates: audio, images, upscales, scene clips (gitignored)
output/        finished videos (gitignored)
tools/         Real-ESRGAN binary + models (gitignored -- download separately)
```

`build/`, `output/`, and `tools/` are excluded deliberately: the first two are
fully reproducible from `input/` + `src/`, and the third is a third-party
download. Keeping them out is what makes this repo a few hundred KB instead of
several GB.
