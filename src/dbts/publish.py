from __future__ import annotations

import logging

from rich.console import Console

from dbts.clone import _confirm, _sql_ident, require_exists
from dbts.config import Target, default_profile_name, read_profile, sandbox_user
from dbts.snowflake import connect, log_target_info, run_sql

log = logging.getLogger("dbts.publish")
console = Console()

_GRANT_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("USAGE on database", "GRANT USAGE ON DATABASE {db} TO ROLE PUBLIC"),
    ("USAGE on all schemas", "GRANT USAGE ON ALL SCHEMAS IN DATABASE {db} TO ROLE PUBLIC"),
    ("USAGE on future schemas", "GRANT USAGE ON FUTURE SCHEMAS IN DATABASE {db} TO ROLE PUBLIC"),
    ("SELECT on all tables", "GRANT SELECT ON ALL TABLES IN DATABASE {db} TO ROLE PUBLIC"),
    ("SELECT on future tables", "GRANT SELECT ON FUTURE TABLES IN DATABASE {db} TO ROLE PUBLIC"),
    ("SELECT on all views", "GRANT SELECT ON ALL VIEWS IN DATABASE {db} TO ROLE PUBLIC"),
    ("SELECT on future views", "GRANT SELECT ON FUTURE VIEWS IN DATABASE {db} TO ROLE PUBLIC"),
)

# Tear down in reverse: object-level grants first, then schema usage, then DB usage.
_REVOKE_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("SELECT on all tables", "REVOKE SELECT ON ALL TABLES IN DATABASE {db} FROM ROLE PUBLIC"),
    ("SELECT on future tables", "REVOKE SELECT ON FUTURE TABLES IN DATABASE {db} FROM ROLE PUBLIC"),
    ("SELECT on all views", "REVOKE SELECT ON ALL VIEWS IN DATABASE {db} FROM ROLE PUBLIC"),
    ("SELECT on future views", "REVOKE SELECT ON FUTURE VIEWS IN DATABASE {db} FROM ROLE PUBLIC"),
    ("USAGE on all schemas", "REVOKE USAGE ON ALL SCHEMAS IN DATABASE {db} FROM ROLE PUBLIC"),
    ("USAGE on future schemas", "REVOKE USAGE ON FUTURE SCHEMAS IN DATABASE {db} FROM ROLE PUBLIC"),
    ("USAGE on database", "REVOKE USAGE ON DATABASE {db} FROM ROLE PUBLIC"),
)


def _resolve_sandbox() -> tuple[Target, str]:
    target = read_profile(default_profile_name(), "sandbox")
    sandbox_user(target)
    return target, target.database.upper()


def _apply_grants(
    target: Target,
    db_id: str,
    templates: tuple[tuple[str, str], ...],
    verb: str,
) -> None:
    total = len(templates)
    log_target_info(target)
    with console.status("[dim]Authenticating to Snowflake…[/dim]") as spinner:
        conn = connect(target)
        try:
            for i, (label, tmpl) in enumerate(templates, start=1):
                spinner.update(f"[dim]{verb} {label} ({i}/{total})…[/dim]")
                ddl = tmpl.format(db=db_id)
                log.debug("[dim]%s[/dim]", ddl)
                run_sql(conn, ddl)
        finally:
            conn.close()


def publish(yes: bool = False) -> int:
    require_exists()
    target, db = _resolve_sandbox()

    if not yes and not _confirm(f"Grant USAGE+SELECT on {db} (incl. future objects) to ROLE PUBLIC?"):
        log.warning("[yellow]Aborted.[/yellow]")
        return 1

    _apply_grants(target, _sql_ident(db), _GRANT_TEMPLATES, verb="Granting")
    log.info("[green]Published[/green] %s to ROLE PUBLIC", db)
    return 0


def unpublish(yes: bool = False) -> int:
    require_exists()
    target, db = _resolve_sandbox()

    if not yes and not _confirm(f"Revoke ROLE PUBLIC access to {db}?"):
        log.warning("[yellow]Aborted.[/yellow]")
        return 1

    _apply_grants(target, _sql_ident(db), _REVOKE_TEMPLATES, verb="Revoking")
    log.info("[green]Unpublished[/green] %s from ROLE PUBLIC", db)
    return 0
