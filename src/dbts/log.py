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
