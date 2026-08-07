# `input/` — pipeline inputs

The pipeline reads exactly two files from this directory. They are gitignored
(script content is per-video and personal), so create them yourself.

`MM:SS` timestamps are the **join key** between the two files — the timestamp
sets must match exactly, or `parse.py` aborts and tells you which side is
missing which key. Scene order comes from `Visuals.txt`.

## `Script.txt`

An optional header, then one timestamped narration line per scene:

```
Title: Your video title here
Thumbnail Text: Short thumbnail hook

[00:00] [thoughtful] First line of narration.
[00:12] Second line of narration.
[00:28] [dramatic tone] A line delivered slower and lower.
[00:44] [pause]
[00:44] A line preceded by 400ms of silence.
```

`[bracketed]` tags are **directives** — stripped from the spoken text and
mapped to prosody or silence in `src/config.py`:

| Directive | Effect |
| --- | --- |
| `whispers`, `softly` | slower, much quieter |
| `dramatic tone` | slower, lower pitch |
| `curious` | higher pitch |
| `thoughtful` | slightly slower |
| `sighs`, `exhales` | delivery color only, default prosody |
| `pause` | insert 400 ms of silence |
| `long pause` | insert 900 ms of silence |

A pause is written as a **standalone line sharing the same timestamp** as the
content line that follows it; both collapse into one scene. Unknown directives
warn on stderr rather than failing. Every scene must have some spoken text left
after directives are stripped.

## `Visuals.txt`

One `IMAGE:` prompt per scene, same timestamps:

```
[00:00] IMAGE: A dark room, an empty lit throne, a still shadow in the fog. <style suffix>

[00:12] IMAGE: A golden crown floating in an empty brutalist hall. <style suffix>
```

Repeat a **byte-identical** style suffix on every prompt (medium, lighting,
palette, `no text`, `16:9`, …). Together with the fixed `IMAGE_SEED` in
`src/config.py`, that suffix is what keeps all scenes looking like one film
instead of N unrelated pictures. The two reinforce each other, but only the
suffix survives a change of image provider — so keep guarding it. Copy-paste it
rather than retyping. Duplicate timestamps are an error.
