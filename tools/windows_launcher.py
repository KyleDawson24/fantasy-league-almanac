"""Standard-library bootstrap behind ``START_ALMANAC.cmd``.

This file deliberately imports no third-party package.  A fresh release can
therefore use it to finish an interrupted dependency install before the guided
setup imports requests, dbt, Google libraries, or any other shipped package.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON = (3, 13)
STAMP_NAME = ".fantasy-league-almanac-requirements.sha256"


class LauncherError(RuntimeError):
    """A local preparation failure with stranger-safe recovery text."""


ProcessRunner = Callable[..., subprocess.CompletedProcess]


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    runner: ProcessRunner = subprocess.run,
) -> subprocess.CompletedProcess:
    """Run an argument vector without shell parsing or path interpolation."""

    return runner(tuple(str(part) for part in command), cwd=cwd, check=False)


def _requirements_digest(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise LauncherError(
            "requirements.txt could not be read. Extract the complete release "
            "ZIP into one folder, then start again."
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _read_stamp(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return value or None


def _write_stamp(path: Path, digest: str) -> None:
    """Publish the install marker only after pip and ``pip check`` succeed."""

    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(digest + "\n", encoding="ascii")
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise LauncherError(
            "Packages installed, but their local completion marker could not "
            "be saved. Check that the extracted folder is writable, then run "
            "START_ALMANAC again."
        ) from exc


def _pip_works(
    python_executable: Path,
    *,
    project_root: Path,
    runner: ProcessRunner,
) -> bool:
    result = _run(
        (python_executable, "-m", "pip", "--version"),
        cwd=project_root,
        runner=runner,
    )
    return result.returncode == 0


def _pip_check(
    python_executable: Path,
    *,
    project_root: Path,
    runner: ProcessRunner,
) -> bool:
    result = _run(
        (python_executable, "-m", "pip", "check"),
        cwd=project_root,
        runner=runner,
    )
    return result.returncode == 0


def ensure_dependencies(
    project_root: Path = REPO_ROOT,
    *,
    python_executable: Path | str = Path(sys.executable),
    runner: ProcessRunner = subprocess.run,
) -> bool:
    """Install the pinned environment once, safely resuming on a later run.

    Returns ``True`` when this call installed or repaired packages and ``False``
    when the requirements hash and ``pip check`` already matched.
    """

    project_root = Path(project_root).resolve()
    python_executable = Path(python_executable).resolve()
    requirements = project_root / "requirements.txt"
    venv_root = project_root / ".venv"
    stamp = venv_root / STAMP_NAME
    digest = _requirements_digest(requirements)

    if not _pip_works(
        python_executable, project_root=project_root, runner=runner
    ):
        print("The package installer is incomplete; repairing it now...")
        repair = _run(
            (python_executable, "-m", "ensurepip", "--upgrade"),
            cwd=project_root,
            runner=runner,
        )
        if repair.returncode != 0 or not _pip_works(
            python_executable, project_root=project_root, runner=runner
        ):
            raise LauncherError(
                "The private environment has no working pip installer. Close "
                "this window and run START_ALMANAC again; the launcher will "
                "repair the project environment before asking for credentials."
            )

    if _read_stamp(stamp) == digest and _pip_check(
        python_executable, project_root=project_root, runner=runner
    ):
        print("Required packages are already ready.")
        return False

    print("Installing the pinned packages. The first run can take several minutes.")
    print("If the connection drops, run START_ALMANAC again; installation resumes safely.")
    installed = _run(
        (
            python_executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            requirements,
        ),
        cwd=project_root,
        runner=runner,
    )
    if installed.returncode != 0:
        raise LauncherError(
            "Package installation did not finish. Check the internet connection "
            "and run START_ALMANAC again. A partial install is safe to resume."
        )
    if not _pip_check(
        python_executable, project_root=project_root, runner=runner
    ):
        raise LauncherError(
            "The packages downloaded, but their compatibility check failed. "
            "Run START_ALMANAC once more. If it repeats, use Report a bug and "
            "share only the non-secret error text shown above."
        )

    venv_root.mkdir(parents=True, exist_ok=True)
    _write_stamp(stamp, digest)
    print("Required packages are ready.")
    return True


def run_guided_setup(
    project_root: Path = REPO_ROOT,
    *,
    python_executable: Path | str = Path(sys.executable),
    rotate_credentials: bool = False,
    runner: ProcessRunner = subprocess.run,
) -> int:
    setup = Path(project_root) / "tools" / "setup_league.py"
    if not setup.is_file():
        raise LauncherError(
            "The guided setup file is missing. Extract the complete release ZIP "
            "into one folder, then start again."
        )
    command: list[Path | str] = [Path(python_executable), setup]
    if rotate_credentials:
        command.append("--rotate-credentials")
    print("\n[3/3] Starting guided ESPN setup...\n")
    return _run(command, cwd=Path(project_root), runner=runner).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the release environment and start guided setup."
    )
    parser.add_argument(
        "--rotate-credentials",
        action="store_true",
        help="Validate and explicitly replace expired shared ESPN cookies.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    actual = sys.version_info[:2]
    if actual != SUPPORTED_PYTHON:
        rendered = ".".join(str(part) for part in sys.version_info[:3])
        print(
            f"Launcher stopped: Python 3.13.x is required; this private "
            f"environment is using Python {rendered}.",
            file=sys.stderr,
        )
        print(
            "Run START_ALMANAC again so it can repair the project environment.",
            file=sys.stderr,
        )
        return 2
    try:
        ensure_dependencies()
        return run_guided_setup(rotate_credentials=args.rotate_credentials)
    except KeyboardInterrupt:
        print(
            "\nInterrupted safely. Run START_ALMANAC again when ready; completed "
            "local work can be reused.",
            file=sys.stderr,
        )
        return 130
    except LauncherError as exc:
        print(f"Launcher stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
