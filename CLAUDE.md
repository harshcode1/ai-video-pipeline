# CLAUDE.md — context for Claude Code

This repo is a **script-to-video pipeline**. Given a narration script and a list
of image prompts, it produces a finished, loudness-normalized 1080p narrated
video using free services plus local ffmpeg. TTS is still keyless; images need
one free Google AI Studio key in `GEMINI_API_KEY` (Pollinations went paid in
Aug 2026 — see `src/config.py` for the full post-mortem).

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
   not garbage. Every stage checks for its own output and skips if present, so a
   crashed run resumes for free by rerunning the same command. Deleting `build/`
   throws away hours of GPU upscaling and rate-limited image downloads.
2. **To resume after any crash, rerun the exact same command.** Do not add
   cleanup steps, do not "start fresh."
3. **Never hand-edit anything in `build/` or `output/`.** All of it is generated.
   Change the inputs or `src/config.py` and re-render.
4. **Never lower `IMAGE_MIN_INTERVAL_SECONDS` and never parallelize image
   fetches.** 6s between requests keeps us under the free tier's ~10 req/min.
   Exceeding it gets you rate-limited and makes the run slower, not faster.
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

`render.py` runs these in order; each is idempotent and disk-cached.

| # | Module | Does | Cache location |
|---|---|---|---|
| 0 | `parse.py` | Joins Script + Visuals on `MM:SS` → scene list. No network. | `build/scenes.json` |
| 1 | `tts.py`, `audio.py`, `silence.py` | Per-line edge-tts voiceover, prosody from `[directives]`, real inserted silence for `[pause]`. | `build/audio/NN.mp3` |
| 2 | `timing.py` | `ffprobe` measures true audio duration. **Scene length follows the actual voiceover, never the script's own timestamps.** | in-memory |
| 3 | `images.py` | One Gemini image per scene, native 16:9 at 4K, 6s throttle. Needs `GEMINI_API_KEY`. | `build/images/NN.png` |
| 4 | `upscale.py` | Real-ESRGAN 4×. **Auto-skips now** — 3840px sources need no upscale. | `build/images_upscaled/NN.png` |
| 5 | `assemble.py`, `loudness.py` | Per-scene zoompan clip → finish filters → xfade chain → mux with −14 LUFS audio. | `build/scenes/NN.mp4` → `output/` |

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
                                          # needed now -- it auto-skips at 4K)
python tests/test_assemble.py             # unit tests (plain asserts, NOT pytest)
python src/forensics.py                   # QA metrics: LUFS, F0 variation, video motion
```

Useful flags: `--label NAME` names the merged intermediates, `--out PATH`
overrides the output location.

## Installing things

- ffmpeg: `winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements`.
  **No shell restart needed afterwards** — `ffmpeg_utils.py` scans the winget
  install path directly, even though winget's own message says otherwise.
- Core packages: `pip install -r requirements.txt` (small, fast, pure wheels).
- `requirements-forensics.txt` is separate and optional — large binary wheels
  used only by `src/forensics.py`. Don't install it as part of normal setup; a
  failed opencv build must never block someone from making a video.
- Real-ESRGAN: **no longer needed** — images arrive at 3840×2160 and
  `upscale.py` skips itself. Only relevant if sources ever shrink again. If you
  do need it: release tag **`v0.2.5.0`**, asset
  `realesrgan-ncnn-vulkan-20220424-windows.zip`. **Do not use
  `releases/latest`** — the newer v0.3.0 tag publishes no Windows binary.
- Image API key: free at https://aistudio.google.com/apikey. Set it with
  `setx GEMINI_API_KEY "..."` (persistent) — a new shell then picks it up.

## Settings worth changing

All in `src/config.py`:

- `TTS_VOICE` — default `hi-IN-SwaraNeural` (Hindi). List real options with
  `.venv\Scripts\edge-tts.exe --list-voices` (the bare `edge-tts` command is not
  on PATH — it only exists inside the venv).
  **Always verify a voice exists before using it.**
- `EMOTION_PROSODY` / `PAUSE_MS` — what each `[directive]` in the script does.
- `IMAGE_MODEL` / `IMAGE_SIZE` / `IMAGE_ASPECT_RATIO` — `gemini-3.1-flash-image`
  at 16:9 / 4K. Uppercase `K` is mandatory. `gemini-3.1-flash-lite-image` is
  cheaper but caps at 1K — don't use it, 1K is below the output resolution.
- **There is no image seed any more** (the API exposes none). Cross-scene style
  consistency now rests *entirely* on repeating a byte-identical style suffix on
  every prompt in `Visuals.txt`. Guard that harder than before.
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
| `No image API key found` | `GEMINI_API_KEY` isn't set. Free key at https://aistudio.google.com/apikey, then `setx GEMINI_API_KEY "..."`. `doctor.py` checks this before a render wastes the TTS stage. |
| Image API returns 401/403 | Bad or revoked key. Fails fast on purpose rather than retrying 6×. |
| Image fetch fails repeatedly | Backoff is already built in. If it's a 429 you're over the free tier's rate — wait, do not remove the throttle. |
| Anything suggesting Pollinations | **Retired Aug 2026 — it went paid and only ever returned 1024×576.** It still serves *cached* old prompts, so a spot-check can look like it works while every new prompt 402s. Do not wire it back in. |
| `suspiciously small audio output` | edge-tts hiccup; retries handle it automatically. |
| Unit tests won't even import | They need ffmpeg installed, because `assemble.py` imports `ffmpeg_utils` at module level. |

## How long things take

For a ~69-scene / ~18-minute video:

- Images: 69 × 6s enforced throttle ≈ **7 min minimum** (was 17 min at 15s)
- Upscaling: **gone** — was ~53 min, now auto-skipped at 4K sources
- TTS, scene clips, the 69-input xfade chain, and mux: **the bulk of the time
  now** — the xfade chain is one heavy ffmpeg process

Budget **1–2 hours** for a full run and background it. The switch to native 4K
sources cut roughly an hour off what this used to take. A 3-scene checkpoint is
only a few minutes, which is exactly why you render one first.
