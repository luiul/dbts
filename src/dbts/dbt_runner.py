from __future__ import annotations

import os
import shutil
import subprocess
from typing import Iterable

from dbts.config import (
    ConfigError,
    Target,
    dbt_project_dir,
    profiles_dir,
    sandbox_user,
)


def run(subcommand: str, args: Iterable[str], target_name: str, target: Target) -> int:
    """Invoke `dbt <subcommand> --target <target_name> <args...>`.

    Sets:
      - ENV=<target_name>          (consumed by `generate_database_name` macro)
      - DBT_PROFILES_DIR=<dir>     (so dbt picks up the same profile we read)
      - DBTS_SANDBOX_USER=<user>   (only when target_name == 'sandbox')

    Returns dbt's exit code unchanged.
    """
    if shutil.which("dbt") is None:
        raise ConfigError(
            "dbt not found on PATH. Activate the venv where dbt-core is installed."
        )

    project = dbt_project_dir()
    env = os.environ.copy()
    env["ENV"] = target_name
    env["DBT_PROFILES_DIR"] = str(profiles_dir())
    if target_name == "sandbox":
        env["DBTS_SANDBOX_USER"] = sandbox_user(target)

    cmd = ["dbt", subcommand, "--target", target_name, *args]
    completed = subprocess.run(cmd, cwd=str(project), env=env, check=False)
    return completed.returncode
