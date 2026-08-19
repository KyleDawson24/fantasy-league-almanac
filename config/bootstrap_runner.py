"""Safe handoff from MLB-145 guided setup to the existing public runner.

This module deliberately owns no extraction, dbt, or Google behavior. It
starts ``tools/create_public_almanac.py`` in a fresh Python process so that
the established entrypoint loads the just-written release ``.env`` itself.
The subprocess command contains no credentials and the runner is injectable
for a CLI today and another local UI later.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping, Optional, Sequence

from config.bootstrap import (
    BootstrapErrorCode,
    BootstrapValidationError,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RUNNER_PATH = REPO_ROOT / "tools" / "create_public_almanac.py"
_LOCAL_ESPN_KEYS = ("LEAGUE_ID", "ESPN_S2", "SWID")


@dataclass(frozen=True)
class AlmanacRunResult:
    """Credential-free completion metadata for any setup shell."""

    completed: bool


def run_public_almanac(
    *,
    process_runner: Callable[..., object] = subprocess.run,
    python_executable: str = sys.executable,
    repo_root: Path = REPO_ROOT,
    runner_path: Path = PUBLIC_RUNNER_PATH,
    environment: Optional[Mapping[str, str]] = None,
) -> AlmanacRunResult:
    """Run the established public orchestration without forwarding secrets."""

    repo_root = Path(repo_root)
    runner_path = Path(runner_path)
    if runner_path != repo_root / "tools" / "create_public_almanac.py":
        raise BootstrapValidationError(
            BootstrapErrorCode.RUN_FAILED,
            "The public almanac runner is not in the expected release folder. "
            "Restore tools/create_public_almanac.py and try again.",
        )
    if not runner_path.is_file():
        raise BootstrapValidationError(
            BootstrapErrorCode.RUN_FAILED,
            "The public almanac runner is missing. Restore the release folder "
            "and try again; the saved setup was not removed.",
        )

    command: Sequence[str] = (str(python_executable), str(runner_path))
    child_environment = dict(os.environ if environment is None else environment)
    for key in _LOCAL_ESPN_KEYS:
        child_environment.pop(key, None)
    try:
        process_runner(
            command,
            cwd=repo_root,
            check=True,
            env=child_environment,
        )
    except FileNotFoundError:
        raise BootstrapValidationError(
            BootstrapErrorCode.RUN_FAILED,
            "Almanac creation could not find the release's Python runtime. "
            "Run setup again with the installed Python 3.13 environment; "
            "the saved setup is still intact.",
        ) from None
    except subprocess.CalledProcessError:
        raise BootstrapValidationError(
            BootstrapErrorCode.RUN_FAILED,
            "Almanac creation stopped in the existing public runner. Review "
            "its last on-screen message, fix that issue, then rerun "
            "tools/create_public_almanac.py; the saved setup is still intact.",
        ) from None
    except OSError:
        raise BootstrapValidationError(
            BootstrapErrorCode.RUN_FAILED,
            "Almanac creation could not start the existing public runner. "
            "Check local permissions and the release folder, then try again; "
            "the saved setup is still intact.",
        ) from None
    return AlmanacRunResult(completed=True)
