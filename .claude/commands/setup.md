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

## Step 3 — The image key (required, costs money, ~5 minutes)

This is the one step only they can do — it needs their own account and card.
Without it the pipeline cannot make pictures at all, so don't treat it as
optional.

**Lead with the cost. Do not bury it or discover it later.** As of Aug 2026 no
image provider has a free tier. An account without credit is not broken; every
request just fails. Someone who was told "it's free" and then hits that will
reasonably think the tool is broken.

> Last thing, and I can't do this one for you — it needs your own account.
>
> Straight answer on cost first: making the pictures isn't free any more. Every
> free service that did this shut its doors in August. It now works out to about
> **$1.30 for a full video** — a bit under 2 cents a picture. Put $5 on and
> that's a few videos' worth.
>
> 1. Go to https://fal.ai and sign up
> 2. Add a small amount of credit (billing page)
> 3. Go to https://fal.ai/dashboard/keys and create an API key
> 4. Copy it and paste it here

If they'd rather not enable billing, say so plainly rather than pretending
there's a workaround: the script and voiceover stages still run for free, but
no images means no video. Don't invent a free provider — the last two both went
paid, and a spot-check of a cached result can look like it still works.

When they paste it, store it permanently for them:

```powershell
setx FAL_KEY "the-key-they-pasted"
$env:FAL_KEY = "the-key-they-pasted"
```

Set **both** — `setx` persists it for future sessions but does not affect the
current one, so the second line is what makes it work right now.

**Treat the key as a secret.** Never echo it back, never write it into a file in
the repo, never put it in a commit.

Then confirm it works by generating **one real test image** before declaring
success — a key that looks fine and a key that can actually generate are two
different things, and only a real request tells them apart.

Read the failure back in plain English:

- **"no remaining balance" / `ImageQuotaError`** — the key is fine, the account
  has no credit. By far the most common one. Send them back to step 2 above.
- **"rejected the key"** — the key itself didn't take (often a paste error, or
  `setx` set it but this window predates it). On Google this arrives as an HTTP
  400 rather than a 401, so don't describe it as a permissions problem.
- **"rejected the request"** — a settings problem in `config.py`, not anything
  they did. Fix it yourself; don't make them read the message.

**Do not install Real-ESRGAN.** The old "sharpness upgrade" step is obsolete —
pictures now arrive above the 1080p output resolution, so that tool would add
nothing but ~50 minutes per video. If they already have it, it simply gets
skipped.

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
