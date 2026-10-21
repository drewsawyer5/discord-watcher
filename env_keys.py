"""env_keys.py - load only the named keys from the shared Drew_code/.env (life-org #387).

`load_dotenv` on Drew_code/.env put every secret in the shared file (OpenAI, Discord, SLATE, ...) into each script's
environment, where every child process inherits it and any env dump prints it. Each script now names the keys it
uses; only those reach os.environ:

    import env_keys
    env_keys.load("DISCORD_BOT_TOKEN", "GENERAL_CHANNEL_ID")

The file is ../.env beside this repo (Drew_code/.env on A6), or SHARED_ENV_FILE when set (the Linux unit).
Same rules as load_dotenv's default: a variable already set in the environment wins, a missing file loads nothing,
and `${VAR}` in a value expands from the environment (not from other lines of the file). Never logs a value.
"""

from __future__ import annotations

import io
import os
from collections.abc import MutableMapping
from pathlib import Path

SHARED_ENV = Path(os.environ.get("SHARED_ENV_FILE") or Path(__file__).resolve().parent.parent / ".env")


def read(names: list[str] | tuple[str, ...], path: Path | str = SHARED_ENV) -> dict[str, str]:
    """{name: value} for the named keys present in the file (a later line wins, like dotenv)."""
    wanted = set(names)
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    found: dict[str, str] = {}
    try:
        from dotenv.parser import parse_stream
    except ImportError:  # pragma: no cover - python-dotenv missing: the simple parser below
        parse_stream = None
    if parse_stream is not None:
        for b in parse_stream(io.StringIO(text)):
            if b.key in wanted and b.value is not None:
                found[b.key] = b.value
    else:  # pragma: no cover
        for line in text.splitlines():
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and key.strip() in wanted:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                else:
                    value = value.split(" #", 1)[0].strip()
                found[key.strip()] = value
    return found


def load(*names: str, path: Path | str = SHARED_ENV, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Put the named keys into the environment (never over a value already set). Returns the names it set."""
    env = os.environ if environ is None else environ
    values = read(names, path)
    try:
        from dotenv.main import resolve_variables

        values = dict(resolve_variables(values.items(), override=False))
    except Exception:  # pragma: no cover - no interpolation without python-dotenv
        pass
    set_now = []
    for name in names:
        if name in values and name not in env and values[name] is not None:
            env[name] = values[name]
            set_now.append(name)
    return set_now
