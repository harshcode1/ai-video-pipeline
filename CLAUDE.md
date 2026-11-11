# CLAUDE.md — context for Claude Code

This repo is a **script-to-video pipeline**. Given a narration script and a list
of image prompts, it produces a finished, loudness-normalized 1080p narrated
video using local ffmpeg. TTS is still free and keyless. **Images are not free
any more** — they need a funded fal.ai account with the key in `FAL_KEY`, and
cost about $0.019 each (**~$1.30 for a 69-scene video**).

Image generation is paid everywhere now: there is **no free provider that also
does native 16:9 at 1080p+**, researched exhaustively, so don't go looking again
without new evidence. The provider is **pluggable** (`IMAGE_PROVIDER` in
`src/config.py`: `fal` | `gemini`), so a provider disappearing is a one-line change.

**If the user wants to make a video, run the `/make-video` command** — it handles
the whole interview-and-render flow. **If their machine isn't set up yet, run
`/setup`.** Everything below is the background context for working in this repo
generally.

## Who uses this

Assume the operator is a **working YouTuber, not a programmer**, unless you learn
otherwise. That cuts two ways:

- **Creative topics — ask, don't explain.** Hooks, retention, pacing, titles,
  thumbnails, CTR: that's their profession. Use their vocabulary and let them
  make the call.
- **Technical topics — plain English, and just handle it.** No tracebacks, no
  jargon, no file paths they didn't ask for. Translate tool output; never paste
  it. Words like "LUFS", "ffmpeg", "venv" shouldn't reach them unprompted.

Technical decisions (voice IDs, upscaler tile sizes, whether to upscale, resume
strategy) are yours. Creative decisions (angle, script, title, style, length) are
theirs.

---

## Golden rules

1. **Never delete `build/` to "fix" a problem.** `build/` is the resume cache,
   not garbage. Every output is named by a hash of everything that affects it
   (`src/cache.py`) and a stage skips when its exact file exists, so a crashed run
   resumes for free by rerunning the same command, and editing the script re-runs
   only what changed. Deleting `build/` throws away paid images and hours of work.
   (Index-named files like `03.mp3` are from older versions: never used, never
   deleted, safe to remove by hand.)
2. **To resume after any crash, rerun the exact same command.** Do not add
   cleanup steps, do not "start fresh."
3. **Never hand-edit anything in `build/` or `output/`.** All of it is generated.
   Change the inputs or `src/config.py` and re-render.
4. **Never lower `IMAGE_MIN_INTERVAL_SECONDS` and never parallelize image
   fetches.** 6s between requests keeps 429s rare and costs nothing meaningful
   against a multi-hour render. Exceeding the rate gets you throttled and makes
   the run slower, not faster. Images also cost real money now — a parallel
   fetch storm that fails halfway still bills for what it generated. (Speech and
   clips DO run on capped pools -- `--tts-workers`, `--clip-workers` -- but images
   never do, and the image limiter is shared and thread-safe.)
5. **`src/config.py` is the single source of truth** for paths, voice, prosody,
   and the visual finish. Change settings there, not scattered through modules.
6. **Timestamps are the join key.** Every `[MM:SS]` in `input/Script.txt` must
   have an exactly matching `[MM:SS]` in `input/Visuals.txt`, or `parse.py`
   aborts. It will tell you which side is missing which key.
7. **Renders take hours, not minutes.** Always run them in the background with a
   long timeout. A default 2-minute tool timeout will kill a real render.
8. **Never commit `build/`, `output/`, `tools/`, or `input/*.txt`.** They are
   gitignored deliberately — generated media, a third-party binary, and personal
   script content respectively.

## Pipeline stages

`render.py` runs these in order; each is disk-cached by a hash of its inputs, so
an edit re-runs only what depends on it.

| # | Module | Does | Cache location |
|---|---|---|---|
| 0 | `parse.py` | Joins Script + Visuals on `MM:SS` → scene list. No network. | `build/scenes.json` |
| 1 | `tts.py`, `audio.py`, `silence.py` | Per-line edge-tts voiceover, prosody from `[directives]`, real inserted silence for `[pause]`. | `build/audio/<hash>.mp3` |
| 2 | `timing.py` | `ffprobe` measures true audio duration. **Scene length follows the actual voiceover, never the script's own timestamps.** | in-memory |
| 3 | `images.py` | One image per scene from the provider in `IMAGE_PROVIDER`. Default fal/FLUX.2 Turbo at 2048×1152, fixed seed, 6s throttle. Needs `FAL_KEY` **+ credit**. | `build/images/<hash>.jpg` |
| 4 | `upscale.py` | Real-ESRGAN 4×. **Auto-skips now** — 2048px sources already meet the working resolution. | `build/images_upscaled/<hash>.png` |
| 5 | `assemble.py`, `loudness.py` | Per-scene zoompan clip → finish filters → xfade chain → mux with −14 LUFS audio. | `build/scenes/<hash>.mp4`, `build/final/` → `output/` |

## Commands

Always use the venv interpreter — `.venv\Scripts\python.exe` on Windows,
`.venv/bin/python` elsewhere — not bare `python`, or the packages won't be found.

```bash
python src/doctor.py                      # preflight: ffmpeg, deps, upscaler, inputs
python src/doctor.py --test-upscale       # + one real ~45s GPU upscale test
python src/parse.py                       # validate inputs, print the scene table (fast, no network)
python src/render.py --indices 1,2,3      # checkpoint render -- ALWAYS do this first
python src/render.py --all                # full run (hours)
python src/render.py --all --no-upscale   # full run without Real-ESRGAN (rarely
                                          # needed -- it already auto-skips)
python -m pytest                          # full test suite (no network, no credits; media tests need ffmpeg)
python src/render.py --all --tts-workers 2 --clip-workers 4   # override the pool sizes for one run
python src/forensics.py                   # QA metrics: LUFS, F0 variation, video motion
```

Useful flags: `--label NAME` names the merged intermediates, `--out PATH`
overrides the output location.

## Installing things

- ffmpeg: `winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements`.
  **No shell restart needed afterwards** — `ffmpeg_utils.py` scans the winget
  install path directly, even though winget's own message says otherwise.
- Core packages: `pip install -r requirements.txt` (small, fast, pure wheels).
  Tests: `pip install -r requirements-dev.txt` (adds pytest).
- `requirements-forensics.txt` is separate and optional — large binary wheels
  used only by `src/forensics.py`. Don't install it as part of normal setup; a
  failed opencv build must never block someone from making a video.
- Real-ESRGAN: **no longer needed** — images already arrive at or above the
  working resolution and `upscale.py` skips itself. Only relevant if sources
  ever shrink again. If you
  do need it: release tag **`v0.2.5.0`**, asset
  `realesrgan-ncnn-vulkan-20220424-windows.zip`. **Do not use
  `releases/latest`** — the newer v0.3.0 tag publishes no Windows binary.
- Image API key: https://fal.ai/dashboard/keys. Set it with
  `setx FAL_KEY "..."` (persistent) — a new shell then picks it up.
  **A key by itself is not enough**: no provider has a free tier since Aug 2026,
  so the account needs credit or every request fails. ~$1.30 per video, so a
  small top-up lasts a long time.
  (If `IMAGE_PROVIDER = "gemini"` instead: key from
  https://aistudio.google.com/apikey in `GEMINI_API_KEY`, and its Google Cloud
  project needs billing enabled. Google's $300 new-account credit covers ~28
  videos there.)

## Settings worth changing

All in `src/config.py`:

- `TTS_VOICE` — default `hi-IN-SwaraNeural` (Hindi). List real options with
  `.venv\Scripts\edge-tts.exe --list-voices` (the bare `edge-tts` command is not
  on PATH — it only exists inside the venv).
  **Always verify a voice exists before using it.**
- `EMOTION_PROSODY` / `PAUSE_MS` — what each `[directive]` in the script does.
- `IMAGE_PROVIDER` — `fal` (default) or `gemini`. Everything else about the
  provider follows from this; `doctor.py` and `render.py` read
  `config.describe_image_settings()` rather than knowing the matrix themselves.
- `FAL_IMAGE_WIDTH` / `FAL_IMAGE_HEIGHT` — 2048×1152, true 16:9 and the largest
  FLUX.2 Turbo allows (**verified against fal's OpenAPI schema: 512–2048**).
  **Must stay equal to `SCALE_PRE_ZOOMPAN_WIDTH`** or every frame gets
  resampled; `doctor.py` warns if they drift apart.
- **Always send explicit pixels, never a named preset.** `landscape_16_9` on a
  1-megapixel model means 1024×576 — *below* the 1080p output. That preset trap
  is exactly what once made generated images blurry.
- `IMAGE_SEED` — back, and it matters. One fixed seed across all 69 scenes is
  what holds a single visual style together. Gemini exposes no seed at all, so
  on that provider consistency falls back to the prompt suffix alone.
- `IMAGE_MIME_TYPES` — Gemini only. A tuple deliberately: Google's docs say both
  `image/jpeg` and `image/png` are accepted, a live run got a 400 for png, so
  `images.py` tries one and falls back. Don't collapse it to a string.
- `TTS_WORKERS` (default 4) and `CLIP_WORKERS` (default: about half the CPU
  cores) -- how many speech lines / scene clips run at once; the matching
  `--tts-workers` / `--clip-workers` flags override them for one run.
- `STAGE_VERSIONS` in `src/cache.py` -- bump a stage's version when its LOGIC
  changes so older cached files are no longer picked up. Settings are already part
  of the keys; the cache can't see changes on a provider's side (a model updated
  behind the same endpoint), so force those by bumping the version.
- `FINISH_FILTER` — the sharpen + contrast + vignette + grain chain that closes
  the "looks like an AI slideshow" gap. Read the comments before touching it;
  each value was A/B-picked by eye.

## Failure playbook

| Symptom | Cause & fix |
|---|---|
| `ffmpeg not found on PATH` | Not installed. `winget install --id Gyan.FFmpeg -e`. `ffmpeg_utils.py` also scans the winget path, so a shell restart may be all that's needed. |
| `vkQueueSubmit failed -4` storms | GPU wedged (often a laptop lid close mid-render). `upscale.py` already retries by stepping the **tile size down**, which is the actual fix — more sleeping won't help. If it fails at tile=32, reset the driver (Win+Ctrl+Shift+B) or reboot, then rerun. All completed work is cached. |
| `Timestamp sets DO NOT match` | A `[MM:SS]` exists in one input file but not the other. The error lists exactly which. |
| `Scene N has no spoken text` | That line is only `[directives]` with no words left after stripping. Add narration text. |
| `No image API key found` | The active provider's key isn't set — `FAL_KEY` by default (https://fal.ai/dashboard/keys). `doctor.py` names the right variable and checks it before a render wastes the TTS stage. |
| `no remaining balance` / `quota ... is zero` (`ImageQuotaError`) | **The most likely failure now.** The key is fine, the account has no credit. Top up at https://fal.ai/dashboard/billing (or enable billing on the Google project if on `gemini`). Waiting can never fix it, which is why it fails fast instead of backing off 6×. |
| `HTTP 422` from fal | Schema validation — the message names the field. Almost always `image_size`: FLUX.2 Turbo requires 512–2048 on **both** axes. Deterministic, so it does not retry. |
| `rejected the key ... as invalid` | Bad or revoked key. Note this arrives as **HTTP 400**, not 401/403 — Google returns `INVALID_ARGUMENT`/`API_KEY_INVALID` for a bad key. Classified by `reason`, not status code, so don't "fix" it by only checking 401/403. |
| `rejected the request (HTTP 400, will not retry)` | Malformed request — the message quotes the field and its allowed values. Deterministic, so it fails immediately instead of burning 6 retries and discarding the explanation. |
| Image API rejects `mime_type` | Handled automatically: `images.py` retries once with the other entry in `config.IMAGE_MIME_TYPES`. The docs list both jpeg and png as valid but the live API has disagreed, so don't collapse that tuple to one value. |
| Image fetch fails repeatedly | Backoff is already built in. A genuine 429 rate-limit is retried; a zero-quota 429 is not (see above). Do not remove the throttle. |
| `suspiciously small audio output` | edge-tts hiccup; retries handle it automatically. |
| TTS fails with `403 Invalid response status` | Microsoft rotated what its speech endpoint accepts and the pinned `edge-tts` is too old. `pip install -U edge-tts` (7.3.1 works; 7.2.8 did not) and update the pin in `requirements.txt`. |
| Tests won't run | `pip install -r requirements-dev.txt`, then `python -m pytest`. Pure-logic tests load without ffmpeg; the media tests skip themselves if it's missing. |

## How long things take

For a ~69-scene / ~18-minute video:

- Images: 69 × 6s enforced throttle ≈ **7 min minimum**, and ~$1.30
- Upscaling: **gone** — was ~53 min, now auto-skipped (sources already meet the
  working resolution)
- TTS, scene clips, the 69-input xfade chain, and mux: **the bulk of the time
  now** — the xfade chain is one heavy ffmpeg process. Speech and clips run on
  pools; with real edge-tts, 12 lines took 15.9 s sequentially vs 3.9 s with 4
  workers on one laptop (`bench/results.md`). Clip-rendering speedup is not
  measured yet.

Budget **1–2 hours** for a full run and background it. Dropping Real-ESRGAN
cut roughly an hour off what this used to take. A 3-scene checkpoint is
only a few minutes, which is exactly why you render one first.
