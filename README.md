# ai-video-pipeline

A reproducible, resumable pipeline that turns a **timestamped narration script**
plus a list of **image prompts** into a finished, loudness-normalized 1080p
narrated video — using local ffmpeg plus one paid image API.

Voiceover is free and keyless. **Image generation is not free**: it needs a
funded [fal.ai](https://fal.ai) account with the key in `FAL_KEY`, and costs
about **$0.019 per image — roughly $1.30 for a 69-scene video**.

The image provider is **pluggable** (`IMAGE_PROVIDER` in `src/config.py`: `fal`
by default, `gemini` as an optional fallback), so switching is a one-line change.
No provider offers a free tier that also gives native 16:9 at 1080p or above; see
*Technical Decisions* below for the short history.

```
input/Script.txt    ─┐
input/Visuals.txt   ─┴─▶ parse ─▶ TTS ─▶ timing ─▶ images ─▶ upscale ─▶ assemble ─▶ output/*.mp4
```

## How it works

| Stage | Module | What it does |
| --- | --- | --- |
| 0. Parse | `src/parse.py` | Joins `Script.txt` + `Visuals.txt` on `MM:SS` timestamps into `build/scenes.json`. Validates that both timestamp sets match exactly. No network. |
| 1. TTS | `src/tts.py`, `src/audio.py` | Per-line Hindi TTS via **edge-tts** (`hi-IN-SwaraNeural`), with per-line prosody from the script's `[directives]` and real inserted silence for `[pause]`. Lines are synthesized on a capped thread pool (`--tts-workers`, default 4); retries with exponential backoff. |
| 2. Timing | `src/timing.py` | Measures true audio duration with `ffprobe` — scene length follows the *actual* voiceover, not the nominal script timestamps. |
| 3. Images | `src/images.py` | One image per scene from the provider set in `IMAGE_PROVIDER` — by default **FLUX.2 Turbo on fal.ai** at 2048×1152 (true 16:9, above the 1080p output), throttled to 1 per 6s and cached on disk. Classifies API failures so deterministic ones (bad key, no credit, rejected request) fail immediately with the reason instead of burning six retries. Style consistency comes from a **fixed seed** plus an identical style suffix on every prompt. |
| 4. Upscale | `src/upscale.py` | **Auto-skips.** Local Real-ESRGAN 4× is kept for the case where sources are smaller than the working resolution, but 2048×1152 already meets it, so it returns the original untouched. |
| 5. Assemble | `src/assemble.py` | Per-scene `zoompan` motion clip (rendered several at a time, `--clip-workers`) → a cinematic finish filter chain → `xfade` crossfade chain → mux with two-pass EBU R128 loudness-normalized audio (`src/loudness.py`, target −14 LUFS). |

**Every stage is cached by content.** Each output is named by a short hash of
everything that affects it (the text, the prompt, the voice, the seed, fps and
filter settings, ...), e.g. `build/audio/3f9a1c...mp3`, never by scene number. A
stage is skipped when its exact file already exists, so a crash resumes where it
stopped, and editing the script redoes only what changed: change one line and
only that line's speech, its clip and the final assembly re-run; insert or
reorder scenes and nothing but the final assembly re-runs. Files are written to a
temp name and moved into place, so a crash can't leave a half-written file that
looks valid.

Builds made by older versions left index-named files (`build/audio/03.mp3`,
`build/images/03.jpg`). They are never used and never deleted; render.py prints a
note when it sees them, and they are safe to remove by hand.

### The parts that took the most tuning

Design decisions are documented inline where they're implemented, not
summarized away — the interesting ones:

- **`src/config.py`** — the "looks like an AI slideshow vs. a real video" gap
  closed by four stacked filters on the *final* frame (sharpen, contrast/sat
  S-curve, vignette, temporal film grain), each A/B-picked by eye, plus
  per-scene zoom/pan variants so 69 clips aren't mechanically identical. 
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

# Tune the pools for one run (defaults: 4 speech lines, about half the CPU cores for clips)
python src/render.py --all --tts-workers 2 --clip-workers 4

# Skip Real-ESRGAN explicitly (rarely needed: it skips itself for full-size pictures)
python src/render.py --all --no-upscale
```

Optional flags: `--label` names the merged intermediates, `--out` overrides the
output path. Output lands in `output/`; all intermediates in `build/`.

**Every stage is cached by content, so if a run crashes, just rerun the same
command** — it resumes rather than starting over, and a finished image is never
paid for twice. Don't delete `build/` to "clean up"; that's the cache.

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

```bash
pip install -r requirements-dev.txt     # adds pytest
python -m pytest                        # everything (needs ffmpeg for the media tests)
python src/doctor.py                    # preflight; also runs a fast pytest selection
```

No network and no credits are used: image and speech providers are fakes, and the
media tests use tiny synthetic clips with real ffmpeg. The tests cover:

- the cache: stable hashes, atomic writes, and the invalidation matrix (edit a
  line, edit a prompt, insert, reorder, delete, change fps/seed/voice/loudness,
  crash leftovers, legacy files), counting how often each stage really ran;
- `parse.py` (mismatched timestamps, directives, pauses), `timing.py`, `doctor.py`;
- the image-API retry vs fail-fast matrix for both providers, with the
  exponential backoff checked;
- the ffmpeg layer (argument lists only, `-nostdin`, real-duration xfade offsets,
  explicit 48 kHz loudnorm output) and the pools (worker cap, no duplicate
  requests, resume after a crash, fail-fast cancellation, the shared limiter).

The two original files still run on their own too (`python tests/test_images.py`).

## Technical Decisions

**Why direct ffmpeg (no MoviePy).** Every video step is one ffmpeg command: a `zoompan` clip per scene, an `xfade` chain, a mux. MoviePy holds frames in Python memory and was leaking on long renders; a subprocess per step is simple, uses ffmpeg's own multithreading, and a failure leaves a normal stderr message. All calls go through one helper, `ffmpeg_utils.run()`: argument lists only (never a shell), `-nostdin` for ffmpeg (ffprobe has no such option, so it just gets a closed stdin), and UTF-8 decoding that can't crash on Windows. The xfade offsets are computed from the real `ffprobe` duration of each rendered clip, not the nominal one, and that math is unit-tested.

**Why content-hash caching (the bug it fixed).** Outputs used to be named by scene number (`build/audio/03.mp3`, `build/images/03.jpg`), and a stage skipped any scene whose file existed. So editing the text of scene 3 kept the old voiceover, and inserting a scene at the start shifted everything by one: scene 4's narration played over scene 3's picture, with no error. Now every output is named by a short hash (SHA-256, 16 hex characters) of everything that affects it, plus a per-stage version string:

| Stage | Key covers |
|---|---|
| speech | text, resolved prosody (rate/volume/pitch), voice |
| scene audio | the speech key, the pause, how silence is made |
| image | the full prompt (including your style suffix), provider, model/endpoint, size, seed, guidance, format |
| upscaled image | the image key, upscaler settings |
| clip | the picture's key, the narration length (to the millisecond), the zoom/pan motion, fps, zoom and filter settings, working and output resolution |
| crossfaded video | the ordered clip keys, crossfade, fps |
| mixed voiceover | the ordered scene-audio keys, loudness targets |
| final file | the video key and the mix key |

Changing one thing re-runs only what depends on it; this is tested with fake providers, counting real stage executions: editing a line re-runs that line's speech, its clip and the final assembly; editing an image prompt re-runs that image, its clip and the video; inserting a scene reuses every other scene; reordering re-runs only the final assembly; changing fps re-runs only the clips and what follows. The zoom/pan motion is derived from the picture's hash, not its position, so moving a scene never changes its motion (an earlier `scene index % 3` rule made every later clip change whenever a scene was inserted). Every file is written to a temp name in the same folder and moved into place with `os.replace()`, so a crash can't leave a half-written file that looks valid, and leftover temp files are ignored. Old index-named files in `build/` are never used and never deleted; they're safe to remove by hand.

**Why a capped pool, a shared image limiter, and no image parallelism.** Speech lines (network-bound) and scene clips (one ffmpeg process each) are independent, so they run on a plain `ThreadPoolExecutor` with a worker cap (`TTS_WORKERS`, default 4; `CLIP_WORKERS`, default about half the CPU cores, because each ffmpeg is already multi-threaded). The first failure cancels work that hasn't started, lets running work finish, and re-raises the original error; the cache stays consistent because writes are atomic. Identical lines or clips are made once, and a finished item is never requested again, which matters for paid calls. Images stay sequential on purpose: the provider limit is one request per 6 seconds, so a pool could not make them faster and would only risk the limit. The limiter is still thread-safe and shared by every provider object (callers reserve the next free slot under a lock, then sleep outside it), so the limit can't be broken by accident later. Running things concurrently found a real Windows-only race: two threads creating the same silence clip made `os.replace` fail while another thread's ffmpeg was reading the file. It's fixed twice over: that creation is single-flight, and `atomic_write` accepts an identical, already-published content-addressed file.

**Measured results, and what was not measured.** Speech synthesis with the real edge-tts service: 12 fixed Hindi lines, an empty cache every run, 3 runs per setting in alternating order, wall-clock around the stage only (`bench/bench_parallel.py`; results in `bench/results.md`), on an Intel Core Ultra 7 155H laptop. **15.86 s (median, 1 worker) to 3.91 s (4 workers), about 4x.** Raw runs: 24.61, 15.47, 15.86 s and 3.79, 4.09, 3.91 s. Network latency varies between runs (the first sequential run was slower), so read it as "roughly 4x faster here", not a promise. A second run with a fake provider that just sleeps 0.5 s per line (labelled *pool only*: it measures the pool, not any service) went from 6.1 s to 1.58 s. **Clip rendering: no measured speedup yet.** The first attempt was invalid (other work was running on the machine) and its numbers were discarded; the benchmark must be rerun on an idle machine. Images are not benchmarked: they stay sequential behind the 1-request-per-6-seconds limit.

**Retry vs fail-fast.** A render is a long unattended job, so retrying a failure that can't succeed wastes minutes and hides the reason. Rate limits (429), server errors (5xx) and network errors are retried with exponential backoff (about 3 s, 6 s, 12 s, ... plus jitter, up to 6 attempts). A bad key (401/403), no credit (402, or a message about the balance, checked before the key so it isn't misreported as a bad key), and a rejected request (400/422, with the response body kept because it names the field) stop immediately. Both providers follow the same rules and the matrix is tested.

**Two-pass loudnorm.** The narration is normalized once, after all scenes are joined, to -14 LUFS (the YouTube target). Pass one measures the file; pass two applies exactly those measurements in linear mode, so the whole voiceover moves by one gain instead of being compressed scene by scene. The output sample rate is set explicitly (48 kHz) because loudnorm resamples internally. The end-to-end test measures the finished file's loudness with a different ffmpeg filter (ebur128) to confirm it lands within 1 LU of -14.

**Why the image provider is pluggable.** Two image providers stopped being free in the same month (2026): the keyless one moved to prepaid credits and capped images at 1024x576, and Google removed its free image tier. The default is now FLUX.2 Turbo on fal.ai (about $0.019 per 2048x1152 image, with a seed that keeps one visual style across scenes), and `gemini` remains a selectable fallback. Switching is one setting, `IMAGE_PROVIDER` in `src/config.py`. Always request explicit pixel sizes, never a named preset such as `landscape_16_9`, which on a 1-megapixel model means 1024x576, below the 1080p output.

**Known limitations.**
- Images are paid per request (about $1.30 for 69 scenes) and sequential, so a full video takes at least 7 minutes of image time alone.
- The cache key can't see changes on the provider's side (a model updated behind the same endpoint, or a changed neural voice); bump the stage version in `src/cache.py` or clear that stage's files to force a re-run.
- Old index-named files from earlier versions are left in `build/` and never reused or deleted.
- TTS depends on the unofficial Edge speech endpoint, which Microsoft has broken for old library versions before (edge-tts 7.2.8 returned HTTP 403; 7.3.1 works).
- Clips and the xfade chain are CPU-bound; the xfade chain is one ffmpeg process and does not parallelize.
- Image sources must be at least as wide as `SCALE_PRE_ZOOMPAN_WIDTH`, or the Real-ESRGAN fallback needs a Vulkan GPU and a separate binary (not needed with the default provider).

**Next steps (not done yet).**
- Rerun the clip-rendering benchmark on an idle machine and report the real number.
- An end-to-end test (synthetic inputs, real 1080p ffmpeg): check the output's size, length, audio and loudness, and measure any audio/video drift from frame rounding across scenes. Not run yet, so no drift claim is made.
- A `--plan` dry run that prints which stages would re-run and why, with the estimated image cost, without any API call.

## Repository layout

```
CLAUDE.md      auto-loaded context for Claude Code
.claude/       /make-video workflow command
src/           pipeline modules (config.py is the single source of truth)
src/doctor.py  environment preflight check
tests/         pytest suite (no network, no credits)
bench/         parallel-stage benchmark script + results
input/         Script.txt + Visuals.txt  (gitignored -- see input/README.md)
build/         all intermediates: audio, images, upscales, scene clips (gitignored)
output/        finished videos (gitignored)
tools/         Real-ESRGAN binary + models (gitignored -- download separately)
```

`build/`, `output/`, and `tools/` are excluded deliberately: the first two are
fully reproducible from `input/` + `src/`, and the third is a third-party
download. Keeping them out is what makes this repo a few hundred KB instead of
several GB.
