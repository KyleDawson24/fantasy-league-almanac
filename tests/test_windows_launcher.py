"""Windows release bootstrapper contracts; no installs or live services."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import tools.windows_launcher as launcher


class FakeRunner:
    def __init__(self, returncodes=()):
        self.returncodes = list(returncodes)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        code = self.returncodes.pop(0) if self.returncodes else 0
        return subprocess.CompletedProcess(command, code)


def _release(tmp_path: Path):
    root = tmp_path / "OneDrive - Example User" / "Fantasy League Almanac"
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "requirements.txt").write_text("synthetic==1.0\n", encoding="utf-8")
    (root / "tools" / "setup_league.py").write_text(
        "# synthetic setup\n", encoding="utf-8"
    )
    python = root / ".venv" / "Scripts" / "python.exe"
    python.write_bytes(b"synthetic executable placeholder")
    return root, python


def _digest(root: Path) -> str:
    return hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest()


def test_matching_stamp_and_pip_check_skip_install_in_space_path(tmp_path):
    root, python = _release(tmp_path)
    (root / ".venv" / launcher.STAMP_NAME).write_text(
        _digest(root) + "\n", encoding="ascii"
    )
    runner = FakeRunner([0, 0])

    assert launcher.ensure_dependencies(root, python_executable=python, runner=runner) is False
    commands = [call[0] for call in runner.calls]
    assert commands == [
        (str(python.resolve()), "-m", "pip", "--version"),
        (str(python.resolve()), "-m", "pip", "check"),
    ]
    assert all(call[1]["cwd"] == root.resolve() for call in runner.calls)


def test_first_run_installs_checks_then_atomically_marks_complete(tmp_path):
    root, python = _release(tmp_path)
    runner = FakeRunner([0, 0, 0])

    assert launcher.ensure_dependencies(root, python_executable=python, runner=runner) is True
    commands = [call[0] for call in runner.calls]
    assert commands[1] == (
        str(python.resolve()),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str((root / "requirements.txt").resolve()),
    )
    assert commands[2][-3:] == ("-m", "pip", "check")
    stamp = root / ".venv" / launcher.STAMP_NAME
    assert stamp.read_text(encoding="ascii").strip() == _digest(root)
    assert not stamp.with_name(stamp.name + ".tmp").exists()


def test_changed_requirements_reinstall_even_when_old_stamp_exists(tmp_path):
    root, python = _release(tmp_path)
    (root / ".venv" / launcher.STAMP_NAME).write_text(
        "0" * 64 + "\n", encoding="ascii"
    )
    runner = FakeRunner([0, 0, 0])

    assert launcher.ensure_dependencies(root, python_executable=python, runner=runner) is True
    assert any("install" in call[0] for call in runner.calls)


def test_interrupted_install_leaves_no_success_stamp_and_is_actionable(tmp_path):
    root, python = _release(tmp_path)
    runner = FakeRunner([0, 9])

    with pytest.raises(launcher.LauncherError, match="safe to resume"):
        launcher.ensure_dependencies(root, python_executable=python, runner=runner)

    assert not (root / ".venv" / launcher.STAMP_NAME).exists()


def test_missing_pip_is_repaired_before_install(tmp_path):
    root, python = _release(tmp_path)
    runner = FakeRunner([1, 0, 0, 0, 0])

    assert launcher.ensure_dependencies(root, python_executable=python, runner=runner) is True
    commands = [call[0] for call in runner.calls]
    assert commands[1][-3:] == ("-m", "ensurepip", "--upgrade")
    assert commands[2][-3:] == ("-m", "pip", "--version")


def test_setup_handoff_uses_argument_vector_and_optional_rotation(tmp_path):
    root, python = _release(tmp_path)
    runner = FakeRunner([0])

    result = launcher.run_guided_setup(
        root,
        python_executable=python,
        rotate_credentials=True,
        runner=runner,
    )

    assert result == 0
    assert runner.calls[0][0] == (
        str(python),
        str(root / "tools" / "setup_league.py"),
        "--rotate-credentials",
    )
    assert runner.calls[0][1]["cwd"] == root


def test_missing_setup_file_is_named_without_running_anything(tmp_path):
    root, python = _release(tmp_path)
    (root / "tools" / "setup_league.py").unlink()
    runner = FakeRunner()

    with pytest.raises(launcher.LauncherError, match="complete release ZIP"):
        launcher.run_guided_setup(root, python_executable=python, runner=runner)

    assert runner.calls == []


def test_cmd_front_door_is_path_safe_and_never_requests_admin():
    text = (launcher.REPO_ROOT / "START_ALMANAC.cmd").read_text(encoding="utf-8")
    lowered = text.lower()

    assert 'pushd "%~dp0"' in lowered
    assert '"%fla_venv_python%" "%fla_root%tools\\windows_launcher.py" %*' in lowered
    assert "py -3.13 -m venv" not in lowered  # selected executable is used, not assumed
    assert "administrator" in lowered and "nothing is fixed" in lowered
    assert "python 3.13" in lowered
    assert "add python.exe to path" in lowered


def test_launcher_sources_contain_no_credential_input_or_echo_flags():
    cmd = (launcher.REPO_ROOT / "START_ALMANAC.cmd").read_text(encoding="utf-8").lower()
    python = (launcher.REPO_ROOT / "tools" / "windows_launcher.py").read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in ("espn_s2=", "swid=", "league_id="):
        assert forbidden not in cmd + python


@pytest.mark.skipif(os.name != "nt", reason="the public launcher is Windows-only")
def test_real_cmd_front_door_runs_from_a_disposable_path_with_spaces(tmp_path):
    """Exercise cmd quoting, venv creation, the stdlib helper, and pause.

    The release is synthetic, requirements are empty, and setup never asks for
    credentials or calls a service.  ``python.cmd`` only makes the already
    running test interpreter discoverable under the name the public launcher
    probes; the launcher itself still creates and uses its own disposable venv.
    """

    root = tmp_path / "OneDrive - Example User" / "Fantasy Almanac Candidate"
    tools = root / "tools"
    shims = tmp_path / "command shims"
    tools.mkdir(parents=True)
    shims.mkdir()
    shutil.copy2(launcher.REPO_ROOT / "START_ALMANAC.cmd", root)
    shutil.copy2(launcher.REPO_ROOT / "ROTATE_ESPN_CREDENTIALS.cmd", root)
    shutil.copy2(launcher.REPO_ROOT / "tools" / "windows_launcher.py", tools)
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (tools / "setup_league.py").write_text(
        "import sys\nprint('SYNTHETIC GUIDED SETUP REACHED', *sys.argv[1:])\n",
        encoding="utf-8",
    )
    (shims / "python.cmd").write_text(
        f'@echo off\r\n"{sys.executable}" %*\r\n', encoding="utf-8"
    )
    environment = dict(os.environ)
    environment["PATH"] = str(shims) + os.pathsep + environment.get("PATH", "")

    result = subprocess.run(
        ("cmd.exe", "/d", "/c", str(root / "START_ALMANAC.cmd")),
        cwd=tmp_path,
        env=environment,
        input="\n",
        text=True,
        capture_output=True,
        timeout=120,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "SYNTHETIC GUIDED SETUP REACHED" in combined
    assert "Finished. You may close this window." in combined
    assert (root / ".venv" / launcher.STAMP_NAME).is_file()

    rotation = subprocess.run(
        ("cmd.exe", "/d", "/c", str(root / "ROTATE_ESPN_CREDENTIALS.cmd")),
        cwd=tmp_path,
        env=environment,
        input="\n",
        text=True,
        capture_output=True,
        timeout=120,
    )
    rotation_output = rotation.stdout + rotation.stderr
    assert rotation.returncode == 0, rotation_output
    assert "SYNTHETIC GUIDED SETUP REACHED --rotate-credentials" in rotation_output
