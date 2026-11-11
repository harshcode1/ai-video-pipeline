"""Content-hash cache primitives.

Every cached output is named by a short hash of EVERYTHING that affects it
(text, prompt, voice, seed, fps, filters, ...), never by its position in the
script. Editing, inserting or reordering scenes therefore can't make a stage
reuse a file that belongs to different content: a changed input is simply a
different file name.

Writes are crash-safe: output goes to a temp file in the same folder and is
moved into place with os.replace(), so a killed run can never leave a
half-written file under the real name.
"""
import hashlib
import json
import os
import uuid
from pathlib import Path

# Bump a stage's version when its LOGIC changes (not just its settings), so
# files produced by older code are no longer picked up.
STAGE_VERSIONS = {
    "tts": "1",       # speech synthesis
    "audio": "1",     # speech + inserted silence
    "image": "1",     # generated image
    "upscale": "1",   # upscaled image
    "clip": "1",      # per-scene zoompan clip
    "video": "1",     # xfade-chained video
    "mix": "1",       # concatenated + loudness-normalized audio
    "final": "1",     # muxed output
}

HASH_LENGTH = 16  # hex characters (64 bits): plenty for one project, short enough to read


def short_hash(*parts) -> str:
    """Hash of the canonical JSON form of `parts`: dict key order doesn't matter,
    list order and argument boundaries do."""
    payload = json.dumps(list(parts), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def is_valid(path) -> bool:
    """A cached output counts only if it exists and is non-empty. Because writes
    are atomic, this can't be a half-written file."""
    path = Path(path)
    return path.is_file() and path.stat().st_size > 0


def temp_path_for(final_path) -> Path:
    """A unique temp name in the same folder, keeping the suffix (ffmpeg picks
    the container from it). Never equal to the real name, so a leftover from a
    crash can't be mistaken for the output."""
    final_path = Path(final_path)
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return final_path.with_name(f"{final_path.stem}.{token}.tmp{final_path.suffix}")


def atomic_write(final_path, writer) -> Path:
    """Call writer(tmp_path) to produce the file, then move it to final_path.

    On any failure the temp file is removed and final_path is left exactly as it
    was. An empty or missing result is refused rather than published.
    """
    final_path = Path(final_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path_for(final_path)
    try:
        writer(tmp)
        if not tmp.exists():
            raise RuntimeError(f"writer produced no output for {final_path.name}")
        if tmp.stat().st_size == 0:
            raise RuntimeError(f"writer produced an empty file for {final_path.name}")
        os.replace(tmp, final_path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return final_path
