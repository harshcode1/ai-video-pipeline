---
description: Make a complete narrated video - asks a few questions, then does everything
argument-hint: "[optional topic or script]"
---

Take the user from an idea to a finished, upload-ready video.

## Who you're talking to

**He is a working YouTuber who makes his living on the platform. He is not a
programmer.** Calibrate both halves of that:

- **On anything creative — do not explain, ask.** He knows hooks, retention,
  pacing, titles, thumbnails and CTR better than you do. Use his vocabulary
  freely. Never define a term he uses professionally. Never lecture him about
  what makes a good video.
- **On anything technical — plain English, and just handle it.** No tracebacks,
  no file paths he didn't ask for, no jargon. He should never see the words
  "LUFS", "ffmpeg", "venv", or "stderr" unless he brings them up.
- Creative calls are his. Technical calls are yours. Don't hand him a decision
  that's really yours to make (which voice ID, which tile size, whether to
  upscale) and don't quietly make one that's really his (the hook, the angle,
  the title).

Worth stating **once**, early, because it's his professional context: output is
1080p 16:9, and the audio is normalized to YouTube's own loudness target, so it
won't get turned down on upload.

If `$ARGUMENTS` is non-empty, treat it as his answer to Question 1.

---

## Step 0 — Make sure the machine is ready

Run `.venv\Scripts\python.exe src\doctor.py` (or `python src/doctor.py` if
there's no `.venv` yet).

- **If it passes:** say nothing about it. Move on.
- **If it fails:** don't send him to another command. Fix it inline by following
  `.claude/commands/setup.md`, asking permission once in plain English first.
  Something like: "Quick thing — a couple of tools aren't installed yet. Want me
  to set them up? Takes a minute or two."
- **If the image API key is missing, that's a hard stop** — no key, no pictures.
  `doctor.py` names the variable for the active provider (`FAL_KEY` by default).
  Walk him through Step 3 of `setup.md` (~5 minutes, needs his own account with
  credit on it). It's the only step you genuinely cannot do for him. Never print
  the key back to him and never write it into a file.
- **Images cost real money now** — about 2 cents each, so ~$1.30 for a 69-scene
  video. Tell him the estimated cost for *his* scene count before starting a
  full run, once, in one sentence. He's spending his own money; he gets to
  decide. Don't re-raise it on every render.
- **If images fail with a balance/quota error**, the account is out of credit.
  Say that directly — it is not a broken key, not a rate limit, and not
  something waiting will fix.
- Ignore the upscaler entirely. Images already exceed the 1080p output and it
  auto-skips, so `--no-upscale` is no longer something to reason about.

## Step 1 — Offer the shortcut first

Before the questions, give him the option to skip them:

> I can either walk you through a few choices, or you just tell me the topic and
> I'll make all the calls myself. Which do you want?

If he picks the fast path, ask only for the topic and choose everything else
yourself using the defaults below. Tell him what you picked when you present the
sample in Step 4 — don't make him find out by watching.

## Step 2 — The four questions

Ask them in **one** round, not one at a time. On every question, explicitly
offer "or I can pick" so he never has to have an opinion he doesn't have.

**1. What's the video about?**
Does he want to paste his own script, or give you the topic and angle and have
you draft it? If you're drafting, ask what angle or hook he wants — that's his
expertise, not yours. Default: you draft, he edits.

**2. Language, and how should the voice feel?**
Ask for the language and the delivery he wants — calm and slow, dramatic,
high-energy, conspiratorial, whatever he says. **You** then map that to a real
voice: run `.venv\Scripts\edge-tts.exe --list-voices` (bare `edge-tts` is not on
PATH — it only exists inside the venv) and pick from it. Offer him 2–3 real options
if he wants to choose the voice himself. **Never invent a voice name** — only use
one that appears in that list. Default: Hindi, `hi-IN-SwaraNeural`.

**3. How long?**
His call — he knows his own retention curve. Convert to scenes at ~4 per minute
(~15s of narration each). Tell him the honest render time before he commits: a
10-minute video is roughly 40–60 minutes of rendering, ~18 minutes is 1–2 hours.

**4. What should it look like?**
Ask for the visual vibe. Default preset: dark symbolic expressionist
illustration, psychological noir, muted earth tones with one amber accent, matte
oil painting texture, no photorealism, no faces in focus.

**Also draft 2–3 title options and a thumbnail text option and let him pick.**
Titles and thumbnail text are pure CTR — that's his professional judgment, so
offer choices rather than deciding. These go in the `Title:` and
`Thumbnail Text:` header lines of `Script.txt`.

## Step 3 — Build the inputs

Read `input/README.md` for the exact format, then write `input/Script.txt` and
`input/Visuals.txt`. What actually matters:

- **Timestamps must match exactly between the two files.** This is the join key;
  a mismatch aborts everything. Space them ~15s apart, accumulating realistically.
- One narration line per scene in `Script.txt`, under the `Title:` /
  `Thumbnail Text:` header.
- Use `[directives]` sparingly — `[thoughtful]`, `[dramatic tone]`, `[curious]`,
  `[softly]`, `[whispers]`, `[sighs]`, `[pause]`, `[long pause]`. A handful per
  video, not on every line, or the effect flattens out. `[pause]` goes on its
  **own line sharing the same timestamp** as the line it precedes.
- Every scene needs real words left after directives are stripped.
- One `IMAGE:` prompt per scene in `Visuals.txt`, and **append a byte-identical
  style suffix to every single prompt.** Together with the fixed `IMAGE_SEED` in
  `config.py`, this is what keeps 60+ images looking like one film instead of a
  slideshow. Copy-paste the suffix; never retype or reword it per scene. End
  each with `no text, 16:9`.
- Prompts should be symbolic and concrete — an object, a space, a light source —
  not literal illustrations of the sentence. Never put words inside an image.

If he wrote the script himself, keep his words. Reformat and add timestamps, but
don't rewrite his lines.

Then run `parse.py` and confirm the scene count is what you intended with no
warnings. Fix anything it flags before moving on. Don't show him this step.

## Step 4 — Sample first, then stop

Set `TTS_VOICE` in `src/config.py` if he chose a non-default voice. Then:

```
python src/render.py --indices 1,2,3 --label sample
```

A few minutes, not hours. Then **stop and hand it to him**:

> Here's the first 45 seconds so you can check it before I render the whole
> thing: <path>
>
> Listen for the voice and pacing, and see if the look is right. Anything you
> want changed?

If he asked you to make the calls yourself, list what you chose here — voice,
length, style, title.

**Do not start the full render until he approves.** This is the entire point:
catching a wrong voice at minute 5 instead of hour 2.

If he wants changes: voice and delivery live in `src/config.py`, look lives in
`Visuals.txt`, words live in `Script.txt`. Re-render the sample. The cache is
by content, so only what changed is redone: an edited image prompt re-fetches just
that picture, an edited line re-synthesizes just that line, and a changed look
setting re-renders just the affected clips. There is nothing to delete by hand —
and **never delete `build/`**, which holds paid images and costs hours to rebuild.

## Step 5 — Full render

```
python src/render.py --all
```

- **Run it in the background with a multi-hour timeout.** A default timeout will
  kill it partway through.
- Tell him it's running and roughly how long, so he can walk away.
- If it crashes, **rerun the identical command** — every stage is cached and it
  resumes where it stopped. Check `CLAUDE.md`'s failure playbook before trying
  anything cleverer. Don't relay the error text to him; just say you hit a snag
  and are picking it back up.

## Step 6 — Hand it over

Lead with the file path — he waited hours for it. Then:

- Length and file size
- The title and thumbnail text you used, so he can copy them straight into the
  upload form
- One line inviting notes

Keep it short. If he wants numbers on it, `python src/forensics.py` reports
loudness, how expressive the delivery measured, and how much the frame actually
moves — translate those into plain terms rather than pasting the table.
