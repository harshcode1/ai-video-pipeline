# Parallel stages: measured results

Machine: Intel(R) Core(TM) Ultra 7 155H (22 logical CPUs, 15.6 GB RAM), Windows-11-10.0.26300-SP0, Python 3.13.7, ffmpeg version 8.1.2-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers.

Each configuration was run 3 times, alternating the order; the table shows the median (raw times are in the tables below). Wall-clock via time.perf_counter() around the stage call only. Speedup = median(1 worker) / median(N workers).

## Speech synthesis: real edge-tts (12 lines)

Real network calls to the free edge-tts service, voice hi-IN-SwaraNeural, empty cache each run. Network latency varies between runs.

| workers | median (s) | speedup vs 1 | raw runs (s) |
|---|---|---|---|
| 1 | 15.86 | 1.0x | 24.61, 15.47, 15.86 |
| 4 | 3.91 | 4.06x | 3.79, 4.09, 3.91 |

## Speech synthesis: fake 0.5 s latency (12 lines), POOL MECHANICS ONLY

NOT real TTS: each fake line just sleeps 0.5 s. It shows what the pool does when the work is pure waiting; it says nothing about the real service's speed.

| workers | median (s) | speedup vs 1 | raw runs (s) |
|---|---|---|---|
| 1 | 6.1 | 1.0x | 6.11, 6.08, 6.1 |
| 4 | 1.58 | 3.86x | 1.58, 1.58, 1.56 |

## Clip rendering: NO VALID RESULT YET

The first clip-rendering run was invalid and its numbers were removed: other test suites were running on the same machine while it measured (one run even took 4938 s because the machine was starved), so no speedup for clip rendering, or lack of one, can be claimed from it. It needs a rerun on an otherwise idle machine (`python bench/bench_parallel.py`, which also writes `results.json`).
