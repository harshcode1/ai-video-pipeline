"""Two ffmpeg-layer facts that are easy to get silently wrong:
  * xfade offsets must use the REAL durations of the rendered clips, not nominal ones;
  * the second loudnorm pass must set the output sample rate explicitly."""
import subprocess

import pytest

import assemble
import config
import ffmpeg_utils
import loudness
import timing
from conftest import make_tone, measure_lufs, probe_audio, probe_video, requires_ffmpeg


def make_clip(path, seconds, rate=10):
    result = ffmpeg_utils.run([ffmpeg_utils.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                               "-i", f"testsrc=size=160x90:rate={rate}:duration={seconds}",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    assert result.returncode == 0, result.stderr


# ------------------------------------------------------------------ xfade uses real durations

def test_xfade_offsets_come_from_probed_clip_durations_not_nominal_ones(tmp_path, monkeypatch):
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"]
    for c in clips:
        c.write_bytes(b"x")
    real = {clips[0]: 7.3, clips[1]: 5.1, clips[2]: 6.0}
    monkeypatch.setattr(timing, "probe_duration", lambda p: real[p])
    monkeypatch.setattr(config, "BUILD_DIR", tmp_path)

    seen = {}
    original = assemble.build_xfade_filter

    def spy(durations, fade=config.CROSSFADE_SECONDS):
        seen["durations"] = list(durations)
        return original(durations, fade)

    monkeypatch.setattr(assemble, "build_xfade_filter", spy)
    ran = {}
    monkeypatch.setattr(ffmpeg_utils, "run", lambda cmd, **k: ran.setdefault("cmd", cmd) and
                        subprocess.CompletedProcess(cmd, 0, "", ""))

    assemble.xfade_chain(clips, tmp_path / "out.mp4")

    assert seen["durations"] == [7.3, 5.1, 6.0]                    # exactly what ffprobe reported
    fade = config.CROSSFADE_SECONDS
    # the filter script the chain wrote used those durations: first join at 7.3 - fade
    assert assemble.compute_offsets(seen["durations"], fade)[0] == pytest.approx(7.3 - fade)


@requires_ffmpeg
def test_chain_length_matches_real_clip_lengths_even_when_they_differ_from_nominal(tmp_path):
    fade = config.CROSSFADE_SECONDS
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_clip(a, 3.3)
    make_clip(b, 2.1)
    real = [timing.probe_duration(a), timing.probe_duration(b)]
    out = tmp_path / "out.mp4"
    assemble.xfade_chain([a, b], out)
    # two clips crossfaded by `fade` last (d1 + d2 - fade); within one frame at 10 fps
    assert probe_video(out)["duration"] == pytest.approx(sum(real) - fade, abs=0.11)


# ------------------------------------------------------------------ loudnorm second pass

def test_second_pass_sets_the_output_sample_rate_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(loudness, "_measure", lambda p: {
        "input_i": "-30.0", "input_tp": "-12.0", "input_lra": "3.0",
        "input_thresh": "-40.0", "target_offset": "0.5"})
    recorded = []
    monkeypatch.setattr(ffmpeg_utils, "run", lambda cmd, **k: recorded.append(list(cmd)) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    loudness.normalize(tmp_path / "in.mp3", tmp_path / "out.wav")
    cmd = recorded[0]
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert "linear=true" in cmd[cmd.index("-af") + 1]
    assert "measured_I=-30.0" in cmd[cmd.index("-af") + 1]


@requires_ffmpeg
def test_normalized_voiceover_is_48k_and_about_minus_14_lufs(tmp_path):
    quiet = tmp_path / "quiet.mp3"
    make_tone(quiet, seconds=6.0, amplitude=0.05)                  # far below -14 LUFS
    assert measure_lufs(quiet) < -25
    out = tmp_path / "norm.wav"
    loudness.normalize(quiet, out)
    assert probe_audio(out)["sample_rate"] == 48000
    assert measure_lufs(out) == pytest.approx(-14.0, abs=1.0)
