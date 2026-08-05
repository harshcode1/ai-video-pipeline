"""Parse Script.txt + Visuals.txt into build/scenes.json.

No network calls. Timestamp (MM:SS) is the join key. Script.txt encodes
[pause]/[long pause] as a standalone line sharing the SAME timestamp as the
content line that follows it -- both collapse into one scene here.
"""
import json
import re
import sys

import config

TIMESTAMP_RE = re.compile(r"^\[(\d{2}):(\d{2})\]\s*(.*)$")
TAG_RE = re.compile(r"\[([^\]]+)\]")
VISUAL_RE = re.compile(r"^\[(\d{2}):(\d{2})\]\s*IMAGE:\s*(.+)$")


def ts_to_seconds(mm: str, ss: str) -> int:
    return int(mm) * 60 + int(ss)


def parse_header(lines):
    title = None
    thumbnail = None
    for line in lines:
        if line.startswith("Title:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("Thumbnail Text:"):
            thumbnail = line.split(":", 1)[1].strip()
    return {"title": title, "thumbnail": thumbnail}


def parse_script(path):
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    header_lines = [l for l in raw_lines if not TIMESTAMP_RE.match(l.strip())]
    meta = parse_header(header_lines)

    scenes = {}  # timestamp_key -> scene dict, insertion order preserved
    order = []

    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        m = TIMESTAMP_RE.match(line)
        if not m:
            continue
        mm, ss, rest = m.groups()
        key = f"{mm}:{ss}"

        tags = TAG_RE.findall(rest)
        text = TAG_RE.sub("", rest).strip()
        text = re.sub(r"\s+", " ", text)

        if key not in scenes:
            scenes[key] = {
                "timestamp": key,
                "seconds": ts_to_seconds(mm, ss),
                "pause_ms": 0,
                "emotion": None,
                "raw_directives": [],
                "text": "",
                "source_lines": [],
            }
            order.append(key)

        scene = scenes[key]
        scene["source_lines"].append(lineno)

        for tag in tags:
            tag_norm = tag.strip().lower()
            scene["raw_directives"].append(tag_norm)
            if tag_norm in config.PAUSE_MS:
                scene["pause_ms"] += config.PAUSE_MS[tag_norm]
            elif tag_norm in config.EMOTION_PROSODY and scene["emotion"] is None:
                scene["emotion"] = tag_norm
            elif tag_norm not in config.KNOWN_DIRECTIVES:
                print(f"WARNING: unknown directive tag {tag!r} on script line {lineno}", file=sys.stderr)

        if text:
            scene["text"] = (scene["text"] + " " + text).strip() if scene["text"] else text

    ordered_scenes = [scenes[k] for k in order]
    return meta, ordered_scenes


def parse_visuals(path):
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    visuals = {}
    order = []
    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        m = VISUAL_RE.match(line)
        if not m:
            continue
        mm, ss, prompt = m.groups()
        key = f"{mm}:{ss}"
        if key in visuals:
            raise ValueError(f"Duplicate visual timestamp {key} at Visuals.txt line {lineno}")
        visuals[key] = prompt.strip()
        order.append(key)
    return visuals, order


def build_scenes():
    meta, script_scenes = parse_script(config.SCRIPT_FILE)
    visuals, visual_order = parse_visuals(config.VISUALS_FILE)

    script_keys = {s["timestamp"] for s in script_scenes}
    visual_keys = set(visuals)

    if script_keys != visual_keys:
        missing_in_visuals = sorted(script_keys - visual_keys)
        missing_in_script = sorted(visual_keys - script_keys)
        msg = ["Timestamp sets DO NOT match between Script.txt and Visuals.txt."]
        if missing_in_visuals:
            msg.append(f"  In Script but not Visuals: {missing_in_visuals}")
        if missing_in_script:
            msg.append(f"  In Visuals but not Script: {missing_in_script}")
        raise AssertionError("\n".join(msg))

    script_by_key = {s["timestamp"]: s for s in script_scenes}

    scenes = []
    for i, key in enumerate(visual_order, start=1):
        s = script_by_key[key]
        if not s["text"]:
            raise AssertionError(f"Scene {i} ({key}) has no spoken text after stripping directives.")
        scenes.append({
            "index": i,
            "timestamp": key,
            "seconds": s["seconds"],
            "text": s["text"],
            "emotion": s["emotion"],
            "pause_ms": s["pause_ms"],
            "raw_directives": s["raw_directives"],
            "image_prompt": visuals[key],
            "audio_file": f"build/audio/{i:02d}.mp3",
            "image_file": f"build/images/{i:02d}.png",
            "scene_file": f"build/scenes/{i:02d}.mp4",
        })

    return meta, scenes


def _force_utf8_stdout():
    """Windows consoles default to a legacy codepage (cp1252), and this script
    echoes the narration back -- so any non-Latin-1 script (Devanagari, CJK,
    Cyrillic, even a stray curly quote) raises UnicodeEncodeError and takes the
    whole command down. Reconfigure to UTF-8, and degrade to '?' rather than
    crashing on anything the terminal font still can't render."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass  # already fine, or redirected somewhere that doesn't support it


def main():
    _force_utf8_stdout()
    meta, scenes = build_scenes()

    config.SCENES_JSON.write_text(
        json.dumps({"meta": meta, "scenes": scenes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Title: {meta['title']}")
    print(f"Thumbnail: {meta['thumbnail']}")
    print(f"Parsed {len(scenes)} scenes -> {config.SCENES_JSON}")
    print()
    print(f"{'#':>3} {'ts':>6} {'pause':>6} {'emotion':<14} {'text':<60} {'image_prompt':<50}")
    print("-" * 145)
    for s in scenes:
        text_preview = (s["text"][:57] + "...") if len(s["text"]) > 60 else s["text"]
        img_preview = (s["image_prompt"][:47] + "...") if len(s["image_prompt"]) > 50 else s["image_prompt"]
        emo = s["emotion"] or "-"
        pause = f"{s['pause_ms']}ms" if s["pause_ms"] else "-"
        print(f"{s['index']:>3} {s['timestamp']:>6} {pause:>6} {emo:<14} {text_preview:<60} {img_preview:<50}")

    merged = [s for s in scenes if len(s["raw_directives"]) or s["pause_ms"]]
    dup_merges = [s for s in scenes if s["pause_ms"] > 0]
    print()
    print(f"Scenes with a pause merged in (dup-timestamp lines collapsed): {len(dup_merges)}")
    for s in dup_merges:
        print(f"  #{s['index']} ({s['timestamp']}): pause_ms={s['pause_ms']}, directives={s['raw_directives']}")


if __name__ == "__main__":
    main()
