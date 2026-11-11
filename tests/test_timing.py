"""timing.py: real measured durations drive scene length (never the script's
timestamps), and audio concatenation adds up."""
import pytest

import timing
from conftest import make_tone, requires_ffmpeg

pytestmark = requires_ffmpeg


def test_probe_returns_the_true_length_of_a_known_file(tmp_path):
    tone = tmp_path / "two.mp3"
    make_tone(tone, seconds=2.0)
    assert timing.probe_duration(tone) == pytest.approx(2.0, abs=0.06)


def test_probe_distinguishes_different_lengths(tmp_path):
    short, long_ = tmp_path / "s.mp3", tmp_path / "l.mp3"
    make_tone(short, seconds=1.0)
    make_tone(long_, seconds=3.5)
    assert timing.probe_duration(long_) - timing.probe_duration(short) == pytest.approx(2.5, abs=0.1)


def test_probe_of_a_missing_or_broken_file_raises_a_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        timing.probe_duration(tmp_path / "nope.mp3")
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"this is not audio")
    with pytest.raises(RuntimeError):
        timing.probe_duration(broken)


def test_concatenated_audio_is_the_sum_of_its_parts(tmp_path):
    parts = []
    for i, sec in enumerate((1.0, 2.0, 1.5)):
        p = tmp_path / f"p{i}.mp3"
        make_tone(p, seconds=sec, freq=300 + 100 * i)
        parts.append(p)
    out = tmp_path / "all.mp3"
    timing.concat_audio_files(parts, out)
    assert timing.probe_duration(out) == pytest.approx(4.5, abs=0.15)


def test_concat_failure_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match="concat failed"):
        timing.concat_audio_files([tmp_path / "missing.mp3", tmp_path / "also-missing.mp3"], tmp_path / "o.mp3")


def test_scripted_duration_is_the_gap_to_the_next_timestamp_and_none_for_the_last():
    scenes = [{"seconds": 0}, {"seconds": 5}, {"seconds": 12}]
    assert timing.scripted_duration(scenes, 1) == 5
    assert timing.scripted_duration(scenes, 2) == 7
    assert timing.scripted_duration(scenes, 3) is None


def test_the_timing_table_reports_measured_not_scripted_time(capsys):
    all_scenes = [{"index": 1, "timestamp": "00:00", "seconds": 0},
                  {"index": 2, "timestamp": "00:05", "seconds": 5}]
    rendered = [dict(all_scenes[0], measured_duration=7.25), dict(all_scenes[1], measured_duration=3.0)]
    timing.print_timing_table(rendered, all_scenes)
    out = capsys.readouterr().out
    assert "7.25s" in out and "+2.25s" in out          # measured 7.25 vs scripted 5.00
    assert "n/a" in out                                # last scene has no scripted length
