# ai-video-pipeline

A reproducible, resumable pipeline that turns a **timestamped narration script**
plus a list of **image prompts** into a finished, loudness-normalized 1080p
narrated video — using only free, keyless services and local ffmpeg.

No paid APIs, no accounts, no API keys anywhere in this repo.

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
| 3. Images | `src/images.py` | One image per scene from **Pollinations** (`flux`), with a fixed seed across every request for style consistency, rate-limited to the anonymous tier and cached on disk. |
| 4. Upscale | `src/upscale.py` | Local, offline 4× super-resolution via **Real-ESRGAN** (ncnn-vulkan — no PyTorch/CUDA, any Vulkan GPU). Pollinations' free tier silently caps output at 1024×576, so this reconstructs real detail instead of bicubic-blurring it. |
| 5. Assemble | `src/assemble.py` | Per-scene `zoompan` motion clip → a cinematic finish filter chain → `xfade` crossfade chain → mux with two-pass EBU R128 loudness-normalized audio (`src/loudness.py`, target −14 LUFS). |

**Every stage is idempotent and cached.** Reruns after a crash only redo what's
missing, so a partial 69-scene render never starts over from zero.

### The parts that took the most tuning

Design decisions are documented inline where they're implemented, not
summarized away — the interesting ones:

- **`src/config.py`** — the "looks like an AI slideshow vs. a real video" gap
  closed by four stacked filters on the *final* frame (sharpen, contrast/sat
  S-curve, vignette, temporal film grain), each A/B-picked by eye, plus
  per-scene zoom/pan variants so 69 clips aren't mechanically identical.
- **`src/upscale.py`** — `vkQueueSubmit failed -4` (`VK_ERROR_DEVICE_LOST`)
  storms on a shared-memory Intel Arc iGPU traced to Real-ESRGAN's automatic
  tile sizing, with measurements. Retries step the **tile size down** rather
  than sleeping longer, and outputs are grid-validated so partially-dead GPU
  buffers get caught instead of averaging past a whole-image check.

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

Then add the Real-ESRGAN binary, which is a large third-party download and is
therefore **not committed**:

1. Download
   [`realesrgan-ncnn-vulkan-20220424-windows.zip`](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip)
   (~45 MB). Note the tag is **v0.2.5.0** — the newer v0.3.0 release publishes no
   Windows binary, so don't go looking at "latest".
2. Extract it to `tools/realesrgan-ncnn-vulkan/`, so that
   `tools/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan.exe` and its `models/`
   directory both exist.
3. Verify your GPU can actually drive it: `python src/doctor.py --test-upscale`.

Don't have a suitable GPU? Skip this entirely and render with `--no-upscale`.

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
   missing, including the video engine and the optional sharpening tool
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
| Whether to use the sharpening tool | Claude — it detects your hardware |
| What to install, and resuming after a crash | Claude |

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

A full ~69-scene run takes **1.5–3 hours**, dominated by the 15s-per-image
Pollinations throttle (~17 min) and Real-ESRGAN upscaling (~46s per image).

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
python tests/test_assemble.py
```

Covers `compute_offsets()` — the xfade offset math, where a silent bug would
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
