---
description: One-time setup - installs everything needed to make videos on this machine
---

Get this machine ready to make videos. **Assume the user is not a programmer.**

## How to talk to them

- Plain English only. Never show a traceback, a stack trace, or raw tool output.
- Don't name tools you're installing unless it helps them. "Installing the video
  engine" beats "installing Gyan.FFmpeg via winget."
- Say what you're doing, then do it. No lectures.
- If something fails, tell them what it means for *them* and what you'll try
  next — not the error text.

## Step 1 — See what's missing

Run `python src/doctor.py` (if Python itself is missing, that command won't run —
that's your answer for step 2).

**Translate the result into plain English.** Do not paste the output. Example of
the right tone:

> Checked your machine. Two things are missing: the video engine and a few
> Python add-ons. Both are quick installs — want me to set them up now?

## Step 2 — Ask permission once, then install

Ask **once** for permission to install what's missing, listing it in plain terms.
It's their computer. Then install without further check-ins.

On Windows, use `winget`:

| Missing | Command |
|---|---|
| Python | `winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements` |
| ffmpeg ("the video engine") | `winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements` |

Always pass `--accept-package-agreements --accept-source-agreements` so nothing
hangs waiting on a prompt they can't see.

**After installing ffmpeg, do not tell them to restart anything.**
`src/ffmpeg_utils.py` also scans the winget install location directly, so it is
found immediately even though winget says the PATH needs a shell restart.

Then the Python packages:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use `.venv\Scripts\python.exe` for **every** command from here on — never bare
`python`, or packages won't be found. On macOS/Linux the path is
`.venv/bin/python`.

Skip `requirements-forensics.txt` unless they ask for quality metrics. Those are
large and only power an optional diagnostic.

## Step 3 — The image key (required, free, ~2 minutes)

This is the one step only they can do — it needs their Google login. Without it
the pipeline cannot make pictures at all, so don't treat it as optional.

Explain it in one plain sentence and give them the exact steps:

> Last thing, and I can't do this one for you because it needs your Google
> login. The picture service needs a free key — it takes about two minutes:
>
> 1. Go to https://aistudio.google.com/apikey
> 2. Sign in with any Google account
> 3. Click **Create API key**
> 4. Copy it and paste it here
>
> It's free, there's no credit card, and the free allowance is far more than
> you'll ever use for videos.

When they paste it, store it permanently for them:

```powershell
setx GEMINI_API_KEY "the-key-they-pasted"
$env:GEMINI_API_KEY = "the-key-they-pasted"
```

Set **both** — `setx` persists it for future sessions but does not affect the
current one, so the second line is what makes it work right now.

**Treat the key as a secret.** Never echo it back, never write it into a file in
the repo, never put it in a commit.

Then confirm it works by generating one real test image before declaring
success. If the key is rejected, say plainly that the key didn't take and walk
them through creating a fresh one.

**Do not install Real-ESRGAN.** The old "sharpness upgrade" step is obsolete —
pictures now arrive at full 4K, so that tool would add nothing but ~50 minutes
per video. If they already have it installed, it simply gets skipped.

## Step 4 — Confirm and hand off

Run `.venv\Scripts\python.exe src\doctor.py` once more. When it reports no
failures, finish with something like:

> You're all set. Whenever you want to make a video, just type:
>
>     /make-video
>
> I'll ask you a few simple questions and handle the rest.

If anything still fails and it needs them (rare — usually a machine-level
install), explain in one plain sentence what they need to do.
