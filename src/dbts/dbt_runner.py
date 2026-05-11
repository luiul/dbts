from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from dbts.config import (
    ConfigError,
    Target,
    dbt_project_dir,
    profiles_dir,
    sandbox_user,
)


def ensure_dbt_on_path() -> None:
    if shutil.which("dbt") is None:
        raise ConfigError("dbt not found on PATH. Activate the venv where dbt-core is installed.")


def dbt_env(target_name: str, target: Target) -> dict[str, str]:
    """Build the env vars dbt should run with.

    - ENV=<target_name>          (consumed by `generate_database_name` macro)
    - DBT_PROFILES_DIR=<dir>     (so dbt picks up the same profile we read)
    - DBTS_SANDBOX_USER=<user>   (only when target_name == 'sandbox')
    """
    env = os.environ.copy()
    env["ENV"] = target_name
    env["DBT_PROFILES_DIR"] = str(profiles_dir())
    if target_name == "sandbox":
        env["DBTS_SANDBOX_USER"] = sandbox_user(target)
    return env


def project_root() -> Path:
    return dbt_project_dir()


def run(subcommand: str, args: Iterable[str], target_name: str, target: Target) -> int:
    """Invoke `dbt <subcommand> --target <target_name> <args...>`. Returns dbt's exit code."""
    ensure_dbt_on_path()
    cmd = ["dbt", subcommand, "--target", target_name, *args]
    completed = subprocess.run(cmd, cwd=str(project_root()), env=dbt_env(target_name, target), check=False)
    return completed.returncode
