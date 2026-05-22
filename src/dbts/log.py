from __future__ import annotations

import logging

from rich.logging import RichHandler


def configure(verbose: bool = False, quiet: bool = False) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
                show_level=False,
                markup=True,
            )
        ],
        force=True,
    )
    # The Snowflake connector probes boto3 during platform detection. When the
    # user has a stale AWS SSO session, botocore logs a WARNING with exc_info,
    # which RichHandler renders as a full traceback panel — even though the
    # probe is harmless and Snowflake auth proceeds via externalbrowser.
    for noisy in ("botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
