"""config.py must be import-safe: importing it may not touch the disk, and the
project root can be redirected (AVP_ROOT) so tests and one-off runs never write
into the real build/ folder."""
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _run_python(code: str, root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "AVP_ROOT": str(root), "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def test_importing_config_creates_no_directories(tmp_path):
    result = _run_python("import config", tmp_path)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == [], "import config must not create build/ or output/"


def test_avp_root_redirects_every_path(tmp_path):
    result = _run_python(
        "import config; print(config.ROOT); print(config.BUILD_DIR); print(config.OUTPUT_DIR)", tmp_path)
    assert result.returncode == 0, result.stderr
    root, build, out = result.stdout.split()
    assert Path(root) == tmp_path
    assert Path(build) == tmp_path / "build"
    assert Path(out) == tmp_path / "output"


def test_ensure_dirs_creates_the_working_folders(tmp_path):
    result = _run_python("import config; config.ensure_dirs()", tmp_path)
    assert result.returncode == 0, result.stderr
    for sub in ("build/audio", "build/images", "build/images_upscaled", "build/scenes", "output"):
        assert (tmp_path / sub).is_dir(), sub
