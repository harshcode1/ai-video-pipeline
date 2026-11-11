"""Measures sequential vs pooled wall-clock time for the two stages that run in a
pool: speech synthesis and per-scene clip rendering. DEV / MEASUREMENT ONLY.

    python bench/bench_parallel.py            # full run (a few minutes)
    python bench/bench_parallel.py --quick    # tiny smoke run, to check the script works

What is measured, exactly:
  * TTS (real)      the real edge-tts service (free, keyless) with the project's voice,
                    12 fixed Hindi lines, a fresh empty cache every run.
  * TTS (fake)      a fake provider that just sleeps 0.5 s per line. This is labelled
                    "pool only": it shows the pool's mechanics, NOT real TTS speed.
  * Clips (real)    real ffmpeg, production settings (30 fps, 2048x1152 JPEG source ->
                    1920x1080 zoompan + finish filters), synthetic noise images, 6 s of
                    narration per scene.
  * Images          deliberately NOT benchmarked: they stay sequential behind a 1 request
                    per 6 s limit, so a pool cannot make them faster.

Each configuration is run RUNS times, alternating the order so no configuration always
goes first (or last); the table reports the median, with all raw times in results.json.
Wall-clock is time.perf_counter() around the stage call only. Network latency to the TTS
service varies from run to run, which is why the real-TTS numbers are noisier.
"""
import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import assemble  # noqa: E402
import audio  # noqa: E402
import config  # noqa: E402
import ffmpeg_utils  # noqa: E402
import parallel  # noqa: E402
from tts import EdgeTTSProvider, TTSProvider  # noqa: E402

LINES = [
    "यह पहली पंक्ति है और हम एक छोटा सा परीक्षण कर रहे हैं।",
    "कोहरे में डूबा हुआ बंदरगाह सुबह की पहली रोशनी का इंतज़ार कर रहा था।",
    "जहाज़ धीरे-धीरे लहरों के बीच आगे बढ़ता रहा।",
    "किसी को नहीं पता था कि आगे क्या होने वाला है।",
    "रात गहरी थी और हवा में नमक की महक थी।",
    "दूर कहीं एक दीपक टिमटिमा रहा था।",
    "कप्तान ने नक्शे की ओर देखा और चुपचाप सिर हिलाया।",
    "समुद्र शांत था, लेकिन यह शांति अजीब थी।",
    "अचानक लहरें ऊँची उठने लगीं।",
    "सबने एक-दूसरे की ओर देखा, पर कोई कुछ नहीं बोला।",
    "यही वह क्षण था जब सब कुछ बदल गया।",
    "और फिर, चारों ओर सन्नाटा छा गया।",
]


def isolate(root: Path) -> None:
    """Point every config path at a fresh folder so runs never share a cache."""
    config.ROOT = root
    config.BUILD_DIR = root / "build"
    config.AUDIO_DIR = config.BUILD_DIR / "audio"
    config.IMAGES_DIR = config.BUILD_DIR / "images"
    config.IMAGES_UPSCALED_DIR = config.BUILD_DIR / "images_upscaled"
    config.SCENES_DIR = config.BUILD_DIR / "scenes"
    config.OUTPUT_DIR = root / "output"
    config.ensure_dirs()


class FakeLatencyTTS(TTSProvider):
    """Sleeps instead of synthesizing: measures the pool, not any real service."""

    def __init__(self, seconds: float):
        self.seconds = seconds

    def fingerprint(self) -> dict:
        return {"provider": "fake-latency", "seconds": self.seconds}

    def synthesize(self, text, emotion, out_path):
        time.sleep(self.seconds)
        out_path.write_bytes(b"\xff\xfb" + b"\x00" * 2000)        # not real audio; never decoded


def tts_scenes(lines):
    return [{"index": i, "timestamp": f"00:{i:02d}", "text": t, "emotion": None, "pause_ms": 0}
            for i, t in enumerate(lines, start=1)]


def timed(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def run_matrix(label, workers_list, runs, make_job):
    """make_job(workers) -> zero-arg callable doing the work in a fresh isolated folder."""
    raw = {w: [] for w in workers_list}
    for r in range(runs):
        order = workers_list if r % 2 == 0 else list(reversed(workers_list))
        for w in order:
            seconds = timed(make_job(w))
            raw[w].append(round(seconds, 2))
            print(f"  {label}: workers={w:<2} run {r + 1}/{runs}: {seconds:6.2f} s", flush=True)
    return raw


def bench_tts(provider_factory, lines, workers_list, runs, label):
    def make_job(workers):
        def job():
            with tempfile.TemporaryDirectory() as tmp:
                isolate(Path(tmp))
                audio.build_all_audio(tts_scenes(lines), provider_factory(), workers=workers)
        return job

    return run_matrix(label, workers_list, runs, make_job)


def make_sources(folder: Path, count: int):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        w, h = config.FAL_IMAGE_WIDTH, config.FAL_IMAGE_HEIGHT
        img = Image.merge("RGB", [Image.effect_noise((w, h), 40 + 10 * c + i) for c in range(3)])
        path = folder / f"src_{i:02d}.jpg"
        img.save(path, "JPEG", quality=90)
        paths.append(path)
    return paths


def bench_clips(sources, workers_list, runs, narration_seconds=6.0):
    def make_job(workers):
        def job():
            with tempfile.TemporaryDirectory() as tmp:
                isolate(Path(tmp))
                scenes = [{"index": i, "timestamp": f"00:{i:02d}", "measured_duration": narration_seconds,
                           "source_key": f"{i:016x}", "source_file": str(p)}
                          for i, p in enumerate(sources, start=1)]
                assemble.render_all_clips(scenes, workers=workers)
        return job

    return run_matrix("clips", workers_list, runs, make_job)


def machine_info() -> dict:
    def ps(expr):
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", expr], capture_output=True,
                                 text=True, timeout=30).stdout.strip()
            return out or None
        except Exception:
            return None

    ffmpeg_line = ffmpeg_utils.run([ffmpeg_utils.FFMPEG, "-version"]).stdout.splitlines()[0]
    return {
        "cpu": ps("(Get-CimInstance Win32_Processor).Name") or platform.processor(),
        "logical_cpus": os.cpu_count(),
        "ram_gb": ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)"),
        "os": platform.platform(),
        "python": platform.python_version(),
        "ffmpeg": ffmpeg_line,
        "default_clip_workers": parallel.default_clip_workers(),
    }


def summarize(raw: dict) -> list:
    rows = []
    base = statistics.median(raw[min(raw)])                       # workers=1 is the baseline
    for w in sorted(raw):
        med = statistics.median(raw[w])
        rows.append({"workers": w, "median_s": round(med, 2), "runs_s": raw[w],
                     "speedup_vs_1": round(base / med, 2)})
    return rows


def markdown(info, sections, runs) -> str:
    lines = ["# Parallel stages: measured results", "",
             f"Machine: {info['cpu']} ({info['logical_cpus']} logical CPUs, {info['ram_gb']} GB RAM), "
             f"{info['os']}, Python {info['python']}, {info['ffmpeg']}.", "",
             f"Each configuration was run {runs} times, alternating the order; the table shows the median "
             f"(all raw times are in results.json). Wall-clock via time.perf_counter() around the stage "
             f"call only. Speedup = median(1 worker) / median(N workers).", ""]
    for title, note, rows in sections:
        lines += [f"## {title}", "", note, "", "| workers | median (s) | speedup vs 1 | raw runs (s) |",
                  "|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['workers']} | {r['median_s']} | {r['speedup_vs_1']}x | "
                         f"{', '.join(str(x) for x in r['runs_s'])} |")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="tiny smoke run; does not write results")
    ap.add_argument("--skip-real-tts", action="store_true")
    args = ap.parse_args()

    runs = 1 if args.quick else 3
    lines = LINES[:4] if args.quick else LINES
    n_clips = 2 if args.quick else 8
    default_w = min(parallel.default_clip_workers(), n_clips)
    clip_workers = sorted({1, 2, 4, default_w}) if not args.quick else [1, 2]

    info = machine_info()
    print(json.dumps(info, indent=2, ensure_ascii=False))
    sections = []

    if not args.skip_real_tts:
        print("\n== TTS, REAL edge-tts ==")
        raw = bench_tts(EdgeTTSProvider, lines, [1, 4], runs, "tts-real")
        sections.append((f"Speech synthesis: real edge-tts ({len(lines)} lines)",
                         f"Real network calls to the free edge-tts service, voice {config.TTS_VOICE}, empty "
                         f"cache each run. Network latency varies between runs.", summarize(raw)))

    print("\n== TTS, FAKE latency (pool mechanics only) ==")
    raw = bench_tts(lambda: FakeLatencyTTS(0.5), lines, [1, 4], runs, "tts-fake")
    sections.append((f"Speech synthesis: fake 0.5 s latency ({len(lines)} lines), POOL MECHANICS ONLY",
                     "NOT real TTS: each fake line just sleeps 0.5 s. It shows what the pool does when the "
                     "work is pure waiting; it says nothing about the real service's speed.", summarize(raw)))

    print("\n== Clip rendering, real ffmpeg ==")
    with tempfile.TemporaryDirectory() as src_dir:
        sources = make_sources(Path(src_dir), n_clips)
        raw = bench_clips(sources, clip_workers, runs)
    sections.append((f"Clip rendering: real ffmpeg ({n_clips} scenes)",
                     f"Production settings ({config.FPS} fps, {config.FAL_IMAGE_WIDTH}x{config.FAL_IMAGE_HEIGHT} "
                     f"synthetic noise JPEG -> 1920x1080, zoompan + finish filters), 6 s of narration per scene "
                     f"(7 s clip each). Default worker count on this machine = {info['default_clip_workers']} "
                     f"(capped at the scene count).", summarize(raw)))

    text = markdown(info, sections, runs)
    print("\n" + text)
    if not args.quick:
        out_dir = Path(__file__).resolve().parent
        (out_dir / "results.md").write_text(text + "\n", encoding="utf-8")
        (out_dir / "results.json").write_text(json.dumps(
            {"machine": info, "runs": runs,
             "sections": [{"title": t, "note": n, "rows": r} for t, n, r in sections]},
            indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {out_dir / 'results.md'}")


if __name__ == "__main__":
    main()
