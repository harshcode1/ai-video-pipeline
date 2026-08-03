---
description: Interview the user, then generate and render a complete narrated video end to end
argument-hint: "[optional topic or script]"
---

You are going to take this user from an idea to a finished narrated video. They
may know nothing about this codebase — do not make them learn it. Ask a few
questions, then do all the work.

If `$ARGUMENTS` is non-empty, treat it as their answer to Question 1 and don't
ask it again.

---

## Step 1 — Preflight (do this before asking anything)

Run `python src/doctor.py`.

- If it exits **non-zero**, it prints exactly what's blocking. Fix what you can
  (e.g. `pip install -r requirements.txt`), tell the user plainly what they must
  do themselves (installing ffmpeg needs their machine), and stop. Do not
  proceed to a render that will fail.
- Note whether a working **upscaler** was found. If not, every render command
  below needs `--no-upscale`. Do not ask the user about this — it's a hardware
  fact you just measured.

Do not mention doctor's warnings about missing `input/*.txt` — you're about to
create those.

## Step 2 — Ask exactly these four questions

Ask them together in one round, not one at a time. Offer the defaults shown.

1. **What's the video about?** They can paste a full script, or just give a
   topic/idea and you'll write the narration yourself. Make clear both are fine.
2. **What language and voice?** Default is Hindi (`hi-IN-SwaraNeural`). Run
   `edge-tts --list-voices` to offer 2–3 real options for whatever language they
   name. **Never invent a voice name** — only use one that appears in that list.
3. **Roughly how long should it be?** Default ~10 minutes. Convert to a scene
   count at **~4 scenes per minute** (~15s of narration each), and warn them
   that longer means hours of render time.
4. **What visual style/vibe?** Default preset: dark symbolic expressionist
   illustration, psychological noir, muted earth tones with one amber accent,
   matte oil painting texture, no photorealism. Or they describe their own.

## Step 3 — Write the two input files

Create `input/Script.txt` and `input/Visuals.txt`. Read `input/README.md` for the
exact format first. The rules that actually matter:

- **Timestamps must match exactly between the two files.** This is the join key.
  A mismatch aborts the whole run. Space them ~15s apart, accumulating
  realistically (`[00:00]`, `[00:15]`, `[00:31]`, …).
- `Script.txt` gets a `Title:` and `Thumbnail Text:` header, then one timestamped
  narration line per scene.
- Use `[directives]` sparingly — `[thoughtful]`, `[dramatic tone]`, `[curious]`,
  `[softly]`, `[whispers]`, `[sighs]`, `[pause]`, `[long pause]`. A few per
  video, not on every line, or the effect flattens out. A `[pause]` goes on its
  **own line sharing the same timestamp** as the line it precedes.
- Every scene must have real spoken words left after directives are stripped.
- `Visuals.txt` gets one `IMAGE:` prompt per scene. **Append the identical style
  suffix to every single prompt** — this plus the fixed seed is the only reason
  the images look like one coherent film. End each with `no text, 16:9`.
- Prompts should be symbolic and concrete (an object, a space, a light source),
  not literal illustrations of the sentence. Never put words in the image.

Then run `python src/parse.py` and confirm it reports the scene count you
intended with no warnings. Fix anything it complains about before continuing.

## Step 4 — Set the voice

If they chose a non-default voice, update `TTS_VOICE` in `src/config.py`. That
file is the single source of truth — don't patch it anywhere else.

## Step 5 — Checkpoint render, then STOP and show them

```
python src/render.py --indices 1,2,3 --label checkpoint
```

This takes a few minutes, not hours. Then **stop and report**: give them the
output path and tell them to watch it and check three things — voice/pacing,
visual style, and whether the images match the narration.

**Wait for their approval before Step 6.** This is the whole point: catching a
wrong voice at minute 5 instead of hour 2. If they want changes, adjust
(`config.py` for voice/prosody, `Visuals.txt` for style) and re-checkpoint.

Note: changing the style means the already-generated images are stale — delete
only the specific files in `build/images/` and `build/images_upscaled/` that need
regenerating. **Never delete all of `build/`.**

## Step 6 — Full render

```
python src/render.py --all
```

- **Run this in the background with a multi-hour timeout.** It is genuinely
  1.5–3 hours. A default timeout will kill it partway.
- Tell them upfront it's running and roughly how long it'll take.
- If it crashes, **rerun the identical command** — every stage is cached and it
  picks up where it stopped. Read `CLAUDE.md`'s failure playbook before trying
  anything cleverer.

## Step 7 — Report

When it finishes, tell them:

- The **full path** to the finished file in `output/`
- Its **duration and file size** (use `ffprobe`)
- The scene count, voice, and style you used
- That they should watch it and say what to change

Optionally run `python src/forensics.py` and share the loudness and motion
numbers — it measures whether the audio actually sounds expressive and the video
actually reads as moving, so "sounds flat" becomes a number instead of a guess.

Keep the final message short. They waited hours; lead with the file path.
