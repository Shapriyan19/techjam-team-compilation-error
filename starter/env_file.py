"""Minimal `.env` loader for local credentials and overrides.

Every runtime knob in this repo is read from the process environment, which is
awkward for the one value that must never be committed: the LLM API key. This
module loads a `.env` file into `os.environ` at package import so a local run
picks up `NVIDIA_API_KEY` (and any `TECHJAM_*` override) without exporting it by
hand each shell.

Deliberately dependency-free and deliberately conservative:

* a variable already present in the real environment always wins, so
  `NVIDIA_API_KEY=... python -m evaluator.local_evaluator` and CI secrets still
  override the file;
* a missing or unreadable file is not an error - the default offline runtime
  needs no credential at all;
* nothing is parsed beyond `KEY=value` lines, so the file stays a data file and
  never runs shell code.
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILENAME = ".env"
# The repository root: starter/env_file.py -> starter/ -> repo root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_env_path(path: str | os.PathLike[str] | None = None) -> Path:
    """`TECHJAM_ENV_FILE`, else `.env` in the working directory, else repo root."""
    if path is not None:
        return Path(path)
    configured = os.getenv("TECHJAM_ENV_FILE")
    if configured:
        return Path(configured)
    local = Path.cwd() / DEFAULT_ENV_FILENAME
    return local if local.is_file() else PROJECT_ROOT / DEFAULT_ENV_FILENAME


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `KEY=value` lines. Supports `export ` prefixes, `#` comments, quotes."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, separator, value = line.partition("=")
        if not separator:
            continue
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            # An unquoted trailing comment is not part of the value.
            marker = value.find(" #")
            if marker != -1:
                value = value[:marker].rstrip()
        values[name] = value
    return values


def load_env_file(
    path: str | os.PathLike[str] | None = None,
    override: bool = False,
) -> dict[str, str]:
    """Load the file into `os.environ`; return the variables actually applied."""
    env_path = resolve_env_path(path)
    try:
        text = env_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    applied: dict[str, str] = {}
    for name, value in parse_env_file(text).items():
        if not override and name in os.environ:
            continue
        os.environ[name] = value
        applied[name] = value
    return applied
