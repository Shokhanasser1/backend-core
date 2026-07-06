"""Alembic wrapper: injects dynamically discovered version_locations.

Why a wrapper: Alembic reads version_locations from the config BEFORE env.py
runs, so the dynamic list cannot be built inside env.py; a static list in
alembic.ini would cause merge conflicts in client projects whenever a feature
is added or removed (ADR-0008).

Usage (same arguments as the alembic CLI):
    python -m migrations.cli upgrade heads
    python -m migrations.cli downgrade shared@base
    python -m migrations.cli revision -m "add x" --head shared@head \
        --version-path shared/migrations
"""

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic.config import CommandLine, Config

from migrations.discovery import discover_version_locations

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def build_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    locations = discover_version_locations(PROJECT_ROOT)
    config.set_main_option("version_locations", os.pathsep.join(str(path) for path in locations))
    return config


def main(argv: Sequence[str] | None = None) -> None:
    command_line = CommandLine(prog="python -m migrations.cli")
    options = command_line.parser.parse_args(list(argv) if argv is not None else None)
    if not hasattr(options, "cmd"):
        command_line.parser.error("too few arguments")
    command_line.run_cmd(build_config(), options)


if __name__ == "__main__":
    main(sys.argv[1:])
