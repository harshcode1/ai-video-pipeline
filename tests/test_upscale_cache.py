"""Upscaled images are keyed by (source image key + upscaler settings). The clip
stage is told which file to use; it never looks one up by scene number, so a
stale upscaled file can never win over the current picture."""
import pytest

import config
import upscale
from conftest import make_jpeg


def prepared_scene(project, width_px=256, key="srckey0000000001"):
    raw = config.IMAGES_DIR / f"{key}.jpg"
    make_jpeg(raw, size=(width_px, int(width_px * 9 / 16)))
    return {"index": 1, "timestamp": "00:01", "image_key": key,
            "image_file": raw.relative_to(config.ROOT).as_posix()}


def test_a_source_that_already_meets_the_working_width_is_used_as_is(project, monkeypatch):
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 200)       # fixture image is 256 wide
    monkeypatch.setattr(upscale, "upscale", lambda *a, **k: pytest.fail("upscaler must not run"))
    s = prepared_scene(project, 256)
    path = upscale.get_upscaled_image(s)
    assert path == config.ROOT / s["image_file"]
    assert s["source_file"] == s["image_file"]
    assert s["source_key"] == s["image_key"]
    assert list(config.IMAGES_UPSCALED_DIR.iterdir()) == []


def test_a_small_source_is_upscaled_into_a_hash_named_file(project, monkeypatch):
    calls = []

    def fake(in_path, out_path):
        calls.append((in_path, out_path))
        out_path.write_bytes(b"UPSCALED PNG" * 100)

    monkeypatch.setattr(upscale, "upscale", fake)
    s = prepared_scene(project, 256)
    path = upscale.get_upscaled_image(s)
    assert len(calls) == 1
    assert path.parent == config.IMAGES_UPSCALED_DIR
    assert path.name == f"{upscale.upscale_key(s['image_key'])}.png"
    assert s["source_file"] == path.relative_to(config.ROOT).as_posix()
    assert s["source_key"] == path.stem
    assert ".tmp" in str(calls[0][1])                                # written via a temp path


def test_an_existing_upscaled_file_is_reused(project, monkeypatch):
    calls = []
    monkeypatch.setattr(upscale, "upscale", lambda i, o: (calls.append(1), o.write_bytes(b"U" * 600000)))
    monkeypatch.setattr(upscale, "_is_valid_output", lambda p: p.exists() and p.stat().st_size > 0)
    s = prepared_scene(project, 256)
    upscale.get_upscaled_image(s)
    upscale.get_upscaled_image(dict(s))
    assert len(calls) == 1


def test_a_different_source_image_gets_a_different_upscaled_file(project, monkeypatch):
    monkeypatch.setattr(upscale, "upscale", lambda i, o: o.write_bytes(b"U" * 600000))
    monkeypatch.setattr(upscale, "_is_valid_output", lambda p: p.exists() and p.stat().st_size > 0)
    a = upscale.get_upscaled_image(prepared_scene(project, 256, key="aaaa000000000001"))
    b = upscale.get_upscaled_image(prepared_scene(project, 256, key="bbbb000000000002"))
    assert a != b


def test_upscaler_settings_are_part_of_the_key(monkeypatch):
    before = upscale.upscale_key("srckey")
    monkeypatch.setattr(upscale, "UPSCALER_SETTINGS", {"model": "other-model", "scale": 4})
    assert upscale.upscale_key("srckey") != before


def test_a_stale_index_named_upscaled_file_is_ignored_and_left_alone(project, monkeypatch):
    stale = config.IMAGES_UPSCALED_DIR / "01.png"
    stale.write_bytes(b"UPSCALE OF AN OLD PICTURE" * 100)
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 200)
    monkeypatch.setattr(upscale, "upscale", lambda *a, **k: pytest.fail("not needed"))
    s = prepared_scene(project, 256)
    path = upscale.get_upscaled_image(s)
    assert path != stale
    assert s["source_file"] == s["image_file"]            # the CURRENT picture, not 01.png
    assert stale.exists()


def test_upscale_failure_leaves_no_file_behind(project, monkeypatch):
    def boom(i, o):
        o.write_bytes(b"half")
        raise RuntimeError("gpu lost")

    monkeypatch.setattr(upscale, "upscale", boom)
    s = prepared_scene(project, 256)
    with pytest.raises(RuntimeError):
        upscale.get_upscaled_image(s)
    assert list(config.IMAGES_UPSCALED_DIR.iterdir()) == []
