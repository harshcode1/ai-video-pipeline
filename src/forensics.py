"""One-off diagnostic tool: quantifies WHY the checkpoint render might sound
'stale'/flat and look static, using real signal-processing rather than
guesswork. Not part of the production pipeline."""
import sys
import subprocess
from pathlib import Path

import numpy as np
import librosa
import pyloudnorm as pyln
import parselmouth
import cv2

ROOT = Path(__file__).resolve().parent.parent


def audio_report(path: Path, label: str):
    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration = len(y) / sr

    # Loudness (EBU R128) -- YouTube targets ~-14 LUFS integrated
    meter = pyln.Meter(sr)
    lufs = meter.integrated_loudness(y)

    # RMS envelope -- dynamic range / expressiveness proxy
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    rms_db_voiced = rms_db[rms_db > -60]  # drop silence frames
    dyn_range_db = float(rms_db_voiced.max() - rms_db_voiced.min()) if len(rms_db_voiced) else 0.0

    # Pitch (F0) contour via Praat -- flat F0 = monotone/robotic perception
    snd = parselmouth.Sound(str(path))
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=75, pitch_ceiling=400)
    f0 = pitch.selected_array["frequency"]
    f0_voiced = f0[f0 > 0]

    if len(f0_voiced) > 0:
        f0_mean = float(np.mean(f0_voiced))
        f0_std = float(np.std(f0_voiced))
        f0_range = float(np.max(f0_voiced) - np.min(f0_voiced))
        # semitone std is a more perceptually-meaningful monotonicity metric
        f0_semitone_std = float(np.std(12 * np.log2(f0_voiced / f0_mean)))
    else:
        f0_mean = f0_std = f0_range = f0_semitone_std = 0.0

    # Speech rate proxy: voiced-frame ratio (higher = less pausing/breath)
    voiced_ratio = float(np.count_nonzero(f0 > 0) / len(f0)) if len(f0) else 0.0

    print(f"--- {label} ({path.name}) ---")
    print(f"  duration        : {duration:.2f}s")
    print(f"  integrated LUFS : {lufs:.1f}  (YouTube target ~-14 LUFS)")
    print(f"  RMS dyn. range  : {dyn_range_db:.1f} dB  (natural speech usually 15-25dB)")
    print(f"  F0 mean         : {f0_mean:.1f} Hz")
    print(f"  F0 std          : {f0_std:.1f} Hz")
    print(f"  F0 range        : {f0_range:.1f} Hz")
    print(f"  F0 std (semitones): {f0_semitone_std:.2f}  (expressive speech usually >2.5-3.0 st)")
    print(f"  voiced ratio    : {voiced_ratio:.2f}")
    print()
    return {
        "label": label, "duration": duration, "lufs": lufs, "dyn_range_db": dyn_range_db,
        "f0_mean": f0_mean, "f0_std": f0_std, "f0_range": f0_range,
        "f0_semitone_std": f0_semitone_std, "voiced_ratio": voiced_ratio,
    }


def video_motion_report(path: Path, label: str, sample_fps: float = 5.0):
    cap = cv2.VideoCapture(str(path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(src_fps / sample_fps))

    prev_gray = None
    diffs = []
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (320, 180))
            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                diffs.append(float(np.mean(diff)))
            prev_gray = gray
        frame_idx += 1
    cap.release()

    diffs = np.array(diffs)
    print(f"--- {label} video motion ({path.name}) ---")
    print(f"  sampled frames       : {len(diffs)+1} at ~{sample_fps}fps")
    print(f"  mean frame-diff      : {diffs.mean():.3f}  (0-255 scale; <1.0 reads as visually static)")
    print(f"  max frame-diff       : {diffs.max():.3f}  (crossfade spikes expected here)")
    print(f"  frames below 0.5 diff: {int(np.sum(diffs < 0.5))}/{len(diffs)} ({100*np.mean(diffs<0.5):.0f}%)")
    print()
    return {"label": label, "mean_diff": float(diffs.mean()), "max_diff": float(diffs.max())}


def main():
    print("=" * 70)
    print("AUDIO FORENSICS")
    print("=" * 70)
    scene_files = {
        "01 (default -5%)": ROOT / "build/audio/01.mp3",
        "02 (default -5%)": ROOT / "build/audio/02.mp3",
        "34 (whispers)": ROOT / "build/audio/34.mp3",
        "65 (dramatic tone)": ROOT / "build/audio/65.mp3",
    }
    results = []
    for label, path in scene_files.items():
        if path.exists():
            results.append(audio_report(path, label))

    ab_files = {
        "AB-A madhur+dramatic(-15%,-20Hz)": ROOT / "build/ab_test/A_madhur_dramatic.mp3",
        "AB-B madhur FLAT (no prosody)": ROOT / "build/ab_test/B_madhur_flat.mp3",
        "AB-C madhur rate-only(-15%)": ROOT / "build/ab_test/C_madhur_rate_only.mp3",
        "AB-D swara+dramatic": ROOT / "build/ab_test/D_swara_dramatic.mp3",
        "AB-E swara FLAT": ROOT / "build/ab_test/E_swara_flat.mp3",
    }
    for label, path in ab_files.items():
        if path.exists():
            results.append(audio_report(path, label))

    merged = ROOT / "build/_merged_audio_checkpoint1.mp3"
    if merged.exists():
        results.append(audio_report(merged, "MERGED checkpoint audio"))

    print("=" * 70)
    print("VIDEO MOTION FORENSICS")
    print("=" * 70)
    checkpoint_video = ROOT / "output/checkpoint1.mp4"
    if checkpoint_video.exists():
        video_motion_report(checkpoint_video, "checkpoint1")

    print("=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)
    print(f"{'label':<38} {'LUFS':>7} {'dynDB':>7} {'F0std(Hz)':>10} {'F0std(st)':>10}")
    for r in results:
        print(f"{r['label']:<38} {r['lufs']:>7.1f} {r['dyn_range_db']:>7.1f} {r['f0_std']:>10.1f} {r['f0_semitone_std']:>10.2f}")


if __name__ == "__main__":
    main()
