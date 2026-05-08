from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SANDBOX_DB_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*_SANDBOX_[A-Z0-9_]+$")

ENV_VAR_PATTERN = re.compile(
    r"""\{\{\s*env_var\(\s*['"](?P<name>[^'"]+)['"]\s*(?:,\s*['"](?P<default>[^'"]*)['"]\s*)?\)\s*\}\}"""
)


class ConfigError(Exception):
    pass


def _render_env_vars(value: str) -> str:
    """Substitute `{{ env_var('NAME', 'default') }}` against os.environ.

    dbt projects often template the `profile:` field in dbt_project.yml. We only
    support the env_var lookup since that's all that ever appears there.
    """

    def repl(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        if default is not None:
            return default
        raise ConfigError(f"environment variable '{name}' is not set and dbt_project.yml provides no default")

    return ENV_VAR_PATTERN.sub(repl, value)


@dataclass(frozen=True)
class Target:
    name: str
    type: str
    account: str
    user: str
    role: str
    authenticator: str
    warehouse: str
    database: str
    schema: str


def profiles_dir() -> Path:
    """Resolve $DBT_PROFILES_DIR or default to ~/.dbt."""
    env = os.environ.get("DBT_PROFILES_DIR")
    path = Path(env).expanduser() if env else Path.home() / ".dbt"
    if not path.is_dir():
        raise ConfigError(f"profiles directory not found: {path}")
    return path


def profiles_path() -> Path:
    path = profiles_dir() / "profiles.yml"
    if not path.is_file():
        raise ConfigError(f"profiles.yml not found at {path}")
    return path


def read_profile(profile: str, target: str) -> Target:
    """Load a single target from ~/.dbt/profiles.yml."""
    raw = yaml.safe_load(profiles_path().read_text())
    if not isinstance(raw, dict) or profile not in raw:
        raise ConfigError(f"profile '{profile}' not found in {profiles_path()}")
    profile_block = raw[profile]
    outputs = profile_block.get("outputs") or {}
    if target not in outputs:
        available = ", ".join(sorted(outputs)) or "(none)"
        raise ConfigError(f"target '{target}' not found under profile '{profile}'. Available targets: {available}")
    cfg = outputs[target]
    try:
        return Target(
            name=target,
            type=cfg["type"],
            account=cfg["account"],
            user=cfg["user"],
            role=cfg["role"],
            authenticator=cfg.get("authenticator", "externalbrowser"),
            warehouse=cfg["warehouse"],
            database=cfg["database"],
            schema=cfg["schema"],
        )
    except KeyError as e:
        raise ConfigError(f"profile '{profile}', target '{target}' is missing required key: {e.args[0]}") from e


def sandbox_user(target: Target) -> str:
    """Extract the user segment from a sandbox target's database name.

    Asserts the database matches `<PREFIX>_SANDBOX_<USER>` so a typo in the
    profile doesn't end up cloning over a teammate's clone.
    """
    db_upper = target.database.upper()
    if not SANDBOX_DB_PATTERN.match(db_upper):
        raise ConfigError(
            f"sandbox target's database '{target.database}' does not match "
            f"the expected pattern <PREFIX>_SANDBOX_<USER>. Update ~/.dbt/profiles.yml."
        )
    # Take everything after the last '_SANDBOX_'
    return db_upper.rsplit("_SANDBOX_", 1)[1]


def dbt_project_dir(start: Path | None = None) -> Path:
    """Walk up from cwd (or `start`) to find the directory containing dbt_project.yml."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "dbt_project.yml").is_file():
            return candidate
    raise ConfigError(f"could not find dbt_project.yml walking up from {current}. Run dbts from inside a dbt project.")


def default_profile_name() -> str:
    """Resolve the dbt profile name.

    Order of precedence:
      1. $DBTS_PROFILE
      2. The `profile:` field in dbt_project.yml at the project root
    """
    env = os.environ.get("DBTS_PROFILE")
    if env:
        return env
    project = dbt_project_dir()
    raw = yaml.safe_load((project / "dbt_project.yml").read_text())
    if not isinstance(raw, dict) or "profile" not in raw:
        raise ConfigError(
            "could not determine dbt profile name. Set $DBTS_PROFILE or add a `profile:` field to dbt_project.yml."
        )
    return _render_env_vars(str(raw["profile"]))
