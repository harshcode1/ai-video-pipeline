"""doctor.py preflight: it must catch a missing ffmpeg and a missing image key
BEFORE a render wastes hours, and run the test suite through pytest."""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import config
import doctor
import ffmpeg_utils


@pytest.fixture(autouse=True)
def fresh_results():
    doctor.results.clear()
    yield
    doctor.results.clear()


def statuses(label_part):
    return [(s, label, detail) for s, label, detail in doctor.results if label_part in label]


# ------------------------------------------------------------------ ffmpeg

def test_missing_ffmpeg_is_a_fail_with_the_install_command(monkeypatch):
    def not_found(name):
        raise FileNotFoundError(f"{name} not found on PATH or in known winget install location. "
                                f"Install via: winget install --id Gyan.FFmpeg -e")

    monkeypatch.setattr(ffmpeg_utils, "_find", not_found)
    monkeypatch.setattr(ffmpeg_utils, "_cache", {})
    doctor.check_ffmpeg()
    (status, label, detail), = statuses("ffmpeg")
    assert status == doctor.FAIL
    assert "winget install" in detail


def test_present_ffmpeg_is_two_passes(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils, "_find", lambda name: f"C:/fake/{name}.exe")
    monkeypatch.setattr(ffmpeg_utils, "_cache", {})
    doctor.check_ffmpeg()
    assert [s for s, _, _ in doctor.results] == [doctor.PASS, doctor.PASS]


# ------------------------------------------------------------------ image API key

def test_missing_image_key_is_a_fail_that_names_the_variable(monkeypatch):
    monkeypatch.delenv(config.IMAGE_API_KEY_ENV, raising=False)
    doctor.check_image_api()
    (status, label, detail), = statuses("image API key")
    assert status == doctor.FAIL
    assert config.IMAGE_API_KEY_ENV in detail and "setx" in detail


def test_blank_image_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv(config.IMAGE_API_KEY_ENV, "   ")
    doctor.check_image_api()
    assert statuses("image API key")[0][0] == doctor.FAIL


def test_a_set_key_passes_and_still_warns_that_credit_is_needed(monkeypatch):
    monkeypatch.setenv(config.IMAGE_API_KEY_ENV, "abc123")
    doctor.check_image_api()
    assert statuses("image API key")[0][0] == doctor.PASS
    assert statuses("image billing")[0][0] == doctor.WARN


def test_a_working_resolution_that_does_not_match_the_provider_warns(monkeypatch):
    monkeypatch.setenv(config.IMAGE_API_KEY_ENV, "abc123")
    monkeypatch.setattr(config, "SCALE_PRE_ZOOMPAN_WIDTH", 1920)
    doctor.check_image_api()
    assert statuses("working resolution")[0][0] == doctor.WARN


# ------------------------------------------------------------------ packages and inputs

def test_a_missing_required_package_fails_and_a_missing_optional_one_only_warns(monkeypatch):
    real = importlib.import_module

    def fake(name, *a, **k):
        if name in ("edge_tts", "watchdog"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(doctor.importlib, "import_module", fake)
    doctor.check_deps()
    assert statuses("edge_tts")[0][0] == doctor.FAIL
    assert statuses("watchdog")[0][0] == doctor.WARN


def test_missing_input_files_are_a_warning_pointing_at_the_format_docs(project, monkeypatch):
    monkeypatch.setattr(doctor, "ROOT", project)
    doctor.check_inputs()
    found = statuses("input/")
    assert len(found) == 2 and all(s == doctor.WARN for s, _, _ in found)
    assert all("input/README.md" in d for _, _, d in found)


# ------------------------------------------------------------------ unit tests via pytest

def fake_run(returncode, stdout="", stderr=""):
    def run(cmd, **kwargs):
        run.cmd = cmd
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return run


def test_doctor_runs_the_fast_tests_through_pytest_and_reports_the_count(monkeypatch):
    run = fake_run(0, stdout="..........\n23 passed in 1.2s\n")
    monkeypatch.setattr(doctor.subprocess, "run", run)
    doctor.check_unit_tests(ffmpeg_ok=True)
    assert run.cmd[:3] == [sys.executable, "-m", "pytest"]
    (status, label, detail), = statuses("unit tests")
    assert status == doctor.PASS and "23 passed" in detail


def test_failing_tests_make_doctor_fail(monkeypatch):
    monkeypatch.setattr(doctor.subprocess, "run", fake_run(1, stdout="1 failed, 22 passed in 2s\n"))
    doctor.check_unit_tests(ffmpeg_ok=True)
    assert statuses("unit tests")[0][0] == doctor.FAIL


def test_pytest_not_installed_is_only_a_warning_with_the_fix(monkeypatch):
    monkeypatch.setattr(doctor.subprocess, "run",
                        fake_run(1, stderr="C:\\python.exe: No module named pytest"))
    doctor.check_unit_tests(ffmpeg_ok=True)
    (status, label, detail), = statuses("unit tests")
    assert status == doctor.WARN and "requirements-dev.txt" in detail


def test_the_preflight_test_selection_only_names_files_that_exist():
    tests_dir = Path(doctor.ROOT) / "tests"
    for name in doctor.PREFLIGHT_TESTS:
        assert (tests_dir / name).exists(), name
