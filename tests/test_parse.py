"""parse.py: Script.txt + Visuals.txt joined on [MM:SS]."""
import json

import pytest

import config
import parse


def write_inputs(script: str, visuals: str):
    config.SCRIPT_FILE.write_text(script, encoding="utf-8")
    config.VISUALS_FILE.write_text(visuals, encoding="utf-8")


def vis(*stamps):
    return "\n".join(f"[{s}] IMAGE: picture for {s}" for s in stamps) + "\n"


# ------------------------------------------------------------------ the happy path

def test_scenes_join_on_timestamp_in_visuals_order(project):
    write_inputs("Title: My Video\nThumbnail Text: WOW\n\n[00:00] First line.\n[00:05] Second line.\n",
                 vis("00:00", "00:05"))
    meta, scenes = parse.build_scenes()
    assert meta == {"title": "My Video", "thumbnail": "WOW"}
    assert [s["index"] for s in scenes] == [1, 2]
    assert [s["text"] for s in scenes] == ["First line.", "Second line."]
    assert scenes[1]["seconds"] == 5
    assert scenes[0]["image_prompt"] == "picture for 00:00"


def test_scenes_no_longer_carry_index_named_file_paths(project):
    write_inputs("[00:00] Hello.\n", vis("00:00"))
    _, scenes = parse.build_scenes()
    assert not {"audio_file", "image_file", "scene_file"} & set(scenes[0])


def test_hindi_text_and_extra_whitespace_are_preserved_and_normalized(project):
    write_inputs("[00:00]   यह   एक    परीक्षण है।  \n", vis("00:00"))
    _, scenes = parse.build_scenes()
    assert scenes[0]["text"] == "यह एक परीक्षण है।"


# ------------------------------------------------------------------ mismatched timestamps

def test_a_timestamp_missing_from_visuals_is_rejected_and_named(project):
    write_inputs("[00:00] A.\n[00:05] B.\n[00:09] C.\n", vis("00:00", "00:05"))
    with pytest.raises(AssertionError) as err:
        parse.build_scenes()
    msg = str(err.value)
    assert "DO NOT match" in msg
    assert "In Script but not Visuals: ['00:09']" in msg


def test_a_timestamp_missing_from_the_script_is_rejected_and_named(project):
    write_inputs("[00:00] A.\n", vis("00:00", "00:07"))
    with pytest.raises(AssertionError) as err:
        parse.build_scenes()
    assert "In Visuals but not Script: ['00:07']" in str(err.value)


def test_both_directions_are_reported_together(project):
    write_inputs("[00:00] A.\n[00:05] B.\n", vis("00:00", "00:09"))
    with pytest.raises(AssertionError) as err:
        parse.build_scenes()
    msg = str(err.value)
    assert "['00:05']" in msg and "['00:09']" in msg


def test_a_duplicate_visual_timestamp_is_rejected_with_its_line(project):
    write_inputs("[00:00] A.\n", vis("00:00", "00:00"))
    with pytest.raises(ValueError, match=r"Duplicate visual timestamp 00:00 at Visuals.txt line 2"):
        parse.build_scenes()


# ------------------------------------------------------------------ directives and pauses

def test_an_emotion_directive_is_stripped_from_the_text_and_recorded(project):
    write_inputs("[00:00] [whispers] Hello there.\n", vis("00:00"))
    _, scenes = parse.build_scenes()
    assert scenes[0]["text"] == "Hello there."
    assert scenes[0]["emotion"] == "whispers"
    assert scenes[0]["raw_directives"] == ["whispers"]


def test_a_pause_line_sharing_the_timestamp_merges_into_the_scene(project):
    write_inputs("[00:00] [pause]\n[00:00] Hello there.\n", vis("00:00"))
    _, scenes = parse.build_scenes()
    assert len(scenes) == 1
    assert scenes[0]["pause_ms"] == config.PAUSE_MS["pause"]
    assert scenes[0]["text"] == "Hello there."


def test_pauses_add_up_and_the_long_pause_is_longer(project):
    write_inputs("[00:00] [pause]\n[00:00] [long pause]\n[00:00] Hello.\n", vis("00:00"))
    _, scenes = parse.build_scenes()
    assert scenes[0]["pause_ms"] == config.PAUSE_MS["pause"] + config.PAUSE_MS["long pause"]
    assert config.PAUSE_MS["long pause"] > config.PAUSE_MS["pause"]


def test_the_first_emotion_wins_when_there_are_several(project):
    write_inputs("[00:00] [curious] [whispers] Hmm.\n", vis("00:00"))
    _, scenes = parse.build_scenes()
    assert scenes[0]["emotion"] == "curious"


def test_directives_are_case_insensitive(project):
    write_inputs("[00:00] [WHISPERS] Quiet.\n[00:05] [Long Pause] Loud.\n", vis("00:00", "00:05"))
    _, scenes = parse.build_scenes()
    assert scenes[0]["emotion"] == "whispers"
    assert scenes[1]["pause_ms"] == config.PAUSE_MS["long pause"]


def test_an_unknown_directive_warns_with_its_line_number(project, capsys):
    write_inputs("[00:00] Fine.\n[00:05] [shouts wildly] Boom.\n", vis("00:00", "00:05"))
    _, scenes = parse.build_scenes()
    err = capsys.readouterr().err
    assert "unknown directive tag 'shouts wildly'" in err and "line 2" in err
    assert scenes[1]["text"] == "Boom."                      # warned, not fatal


def test_known_delivery_only_directives_do_not_warn(project, capsys):
    write_inputs("[00:00] [sighs] Oh well.\n", vis("00:00"))
    parse.build_scenes()
    assert capsys.readouterr().err == ""


def test_a_scene_with_only_directives_and_no_words_is_rejected(project):
    write_inputs("[00:00] [pause]\n", vis("00:00"))
    with pytest.raises(AssertionError, match="no spoken text"):
        parse.build_scenes()


# ------------------------------------------------------------------ the command

def test_main_writes_scenes_json(project, capsys):
    write_inputs("Title: T\n[00:00] One.\n[00:04] [pause] Two.\n", vis("00:00", "00:04"))
    parse.main()
    data = json.loads(config.SCENES_JSON.read_text(encoding="utf-8"))
    assert [s["text"] for s in data["scenes"]] == ["One.", "Two."]
    assert data["meta"]["title"] == "T"
    assert "Parsed 2 scenes" in capsys.readouterr().out
