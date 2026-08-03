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

## Step 3 — Offer the sharpness upgrade (their choice)

This one is genuinely their call, so ask — with the tradeoff in plain English,
no jargon:

> One optional extra: a tool that makes the images noticeably sharper. It's a
> 45 MB download and works best if your PC has a decent graphics card. Without
> it your videos still come out fine, just a little softer. Want it?

If **yes**, download and extract it:

```powershell
Invoke-WebRequest -Uri "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip" -OutFile "$env:TEMP\resrgan.zip"
Expand-Archive -Path "$env:TEMP\resrgan.zip" -DestinationPath "tools\realesrgan-ncnn-vulkan" -Force
Remove-Item "$env:TEMP\resrgan.zip"
```

The tag `v0.2.5.0` is pinned deliberately — **do not use `releases/latest`**,
because the newer v0.3.0 tag ships no Windows binary at all.

Afterwards confirm `tools/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan.exe`
and `tools/realesrgan-ncnn-vulkan/models/realesrgan-x4plus.bin` both exist. If
the zip nested them one folder deeper, move them up so those exact paths work.

Then verify their graphics card can actually use it:
`.venv\Scripts\python.exe src\doctor.py --test-upscale` (takes ~45s). If it
fails, that's fine — say so plainly and note you'll make videos without it.

If **no**, just note that renders will use `--no-upscale`.

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
