"""cache.py: content hashes and crash-safe writes."""
import pytest

import cache


# ---------------------------------------------------------------- short_hash

def test_golden_value_pins_the_algorithm():
    # sha256 of the canonical JSON of the arguments, first 16 hex characters.
    # If this changes, EVERY cached file in every user's build/ goes stale.
    assert cache.short_hash({"a": 1, "b": [1, 2]}, "हिंदी") == "39238d97bcad826b"


def test_same_inputs_same_hash_and_dict_order_does_not_matter():
    assert cache.short_hash({"x": 1, "y": 2}) == cache.short_hash({"y": 2, "x": 1})


def test_hash_is_16_lowercase_hex_characters():
    h = cache.short_hash("anything")
    assert len(h) == 16 and all(c in "0123456789abcdef" for c in h)


@pytest.mark.parametrize("a,b", [
    ("hello", "hello "),
    (1, 1.0000001),
    ({"seed": 1}, {"seed": 2}),
    (["a", "b"], ["b", "a"]),          # order of a list matters
])
def test_any_difference_changes_the_hash(a, b):
    assert cache.short_hash(a) != cache.short_hash(b)


def test_argument_boundaries_matter():
    assert cache.short_hash("ab", "c") != cache.short_hash("a", "bc")


def test_every_stage_has_a_version_string():
    for stage in ("tts", "audio", "image", "upscale", "clip", "video", "mix", "final"):
        assert isinstance(cache.STAGE_VERSIONS[stage], str) and cache.STAGE_VERSIONS[stage]


def test_changing_a_stage_version_changes_keys_built_with_it(monkeypatch):
    before = cache.short_hash(cache.STAGE_VERSIONS["clip"], "same inputs")
    monkeypatch.setitem(cache.STAGE_VERSIONS, "clip", "999")
    assert cache.short_hash(cache.STAGE_VERSIONS["clip"], "same inputs") != before


# ---------------------------------------------------------------- atomic_write

def test_atomic_write_publishes_the_file_and_leaves_no_temp(tmp_path):
    final = tmp_path / "out" / "abc.mp3"            # parent folder does not exist yet

    def writer(tmp):
        tmp.write_bytes(b"data")

    assert cache.atomic_write(final, writer) == final
    assert final.read_bytes() == b"data"
    assert [p.name for p in final.parent.iterdir()] == ["abc.mp3"]


def test_writer_receives_a_temp_path_in_the_same_folder_with_the_same_suffix(tmp_path):
    final = tmp_path / "abc.mp4"
    seen = {}

    def writer(tmp):
        seen["tmp"] = tmp
        tmp.write_bytes(b"x")

    cache.atomic_write(final, writer)
    assert seen["tmp"].parent == final.parent
    assert seen["tmp"].suffix == ".mp4"            # ffmpeg picks the container from this
    assert seen["tmp"] != final


def test_failed_write_leaves_no_final_file_and_cleans_the_temp(tmp_path):
    final = tmp_path / "abc.png"

    def writer(tmp):
        tmp.write_bytes(b"half")
        raise RuntimeError("crash mid-write")

    with pytest.raises(RuntimeError, match="crash mid-write"):
        cache.atomic_write(final, writer)
    assert list(tmp_path.iterdir()) == []


def test_failed_write_does_not_damage_an_existing_good_file(tmp_path):
    final = tmp_path / "abc.png"
    final.write_bytes(b"good")

    def writer(tmp):
        tmp.write_bytes(b"half")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        cache.atomic_write(final, writer)
    assert final.read_bytes() == b"good"


def test_empty_output_is_refused_not_published(tmp_path):
    final = tmp_path / "abc.mp3"
    with pytest.raises(RuntimeError, match="empty"):
        cache.atomic_write(final, lambda tmp: tmp.write_bytes(b""))
    assert not final.exists()


def test_writer_that_never_creates_the_file_is_refused(tmp_path):
    final = tmp_path / "abc.mp3"
    with pytest.raises(RuntimeError, match="no output"):
        cache.atomic_write(final, lambda tmp: None)
    assert not final.exists()


# ---------------------------------------------------------------- is_valid / leftovers

def test_is_valid_needs_a_non_empty_file(tmp_path):
    assert not cache.is_valid(tmp_path / "missing.mp3")
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    assert not cache.is_valid(empty)
    full = tmp_path / "full.mp3"
    full.write_bytes(b"x")
    assert cache.is_valid(full)


def test_a_leftover_temp_file_from_a_crash_is_never_mistaken_for_the_output(tmp_path):
    final = tmp_path / "abc.mp3"
    # what a killed process leaves behind: a temp-named, half-written file
    leftover = cache.temp_path_for(final)
    leftover.write_bytes(b"half-written garbage")
    assert not cache.is_valid(final)
    # and a later successful write is unaffected by it
    cache.atomic_write(final, lambda tmp: tmp.write_bytes(b"real"))
    assert final.read_bytes() == b"real"
    assert leftover.exists()                       # we do not touch other processes' temps


def test_two_temp_paths_for_the_same_target_never_collide(tmp_path):
    final = tmp_path / "abc.mp3"
    assert cache.temp_path_for(final) != cache.temp_path_for(final)


# ---------------------------------------------------------------- concurrent publishers

def test_if_replace_is_refused_but_an_identical_valid_file_already_exists_the_temp_is_discarded(
        tmp_path, monkeypatch):
    """Content-addressed names mean 'same name' = 'same content'. On Windows a file
    another thread is reading can't be replaced; that must not be an error."""
    final = tmp_path / "abc.mp3"
    final.write_bytes(b"published by another thread")

    def refuse(src, dst):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(cache.os, "replace", refuse)
    result = cache.atomic_write(final, lambda tmp: tmp.write_bytes(b"my copy"))
    assert result == final
    assert final.read_bytes() == b"published by another thread"
    assert [p.name for p in tmp_path.iterdir()] == ["abc.mp3"]       # temp cleaned up


def test_a_refused_replace_with_no_existing_file_is_still_an_error(tmp_path, monkeypatch):
    def refuse(src, dst):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(cache.os, "replace", refuse)
    with pytest.raises(PermissionError):
        cache.atomic_write(tmp_path / "abc.mp3", lambda tmp: tmp.write_bytes(b"x"))
    assert list(tmp_path.iterdir()) == []
