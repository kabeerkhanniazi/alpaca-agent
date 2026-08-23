"""Thin wrapper around Alpaca's official CLI (`alpaca`).

This module replaces the `alpaca-py` SDK as the agent's transport. The hackathon
requires projects to use Alpaca's MCP server or its CLI tools; direct SDK calls
do not satisfy that, so every network call now shells out to the CLI and parses
its JSON.

Two design notes:

**Credentials never touch disk.** The CLI reads ``ALPACA_API_KEY`` and
``ALPACA_SECRET_KEY`` straight from the environment and defaults to paper
trading, which is what Alpaca recommends for "scripts, CI, and agents". No
profile file is created, so a bare cron environment needs nothing beyond the two
variables and a PATH that reaches the binary.

**Return shapes stay compatible.** Downstream modules were written against
`alpaca-py` objects and read them with ``getattr(obj, "field", default)``. The
CLI returns plain JSON, so :func:`wrap` converts parsed JSON into objects that
answer the same attribute names — including the handful where Alpaca's wire
format differs from the SDK's field names (``impliedVolatility`` vs
``implied_volatility``, ``bp`` vs ``bid_price``). Nothing downstream changed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45

# Attribute name -> the wire keys that can satisfy it. Alpaca's JSON uses
# camelCase and one-letter keys in the market-data payloads while the SDK
# exposed snake_case attributes; resolving on lookup keeps the mapping in one
# place instead of rewriting every payload on arrival.
# Fields the SDK deserialised into aware datetimes. The CLI emits them as ISO
# strings, and the dashboard does arithmetic on the clock ones ("closes in..."),
# so they are parsed on the way out to keep the shape the callers expect.
_DATETIME_KEYS = frozenset({
    "timestamp", "next_open", "next_close",
    "created_at", "updated_at", "submitted_at", "filled_at",
    "expired_at", "canceled_at", "failed_at", "replaced_at",
})

# Python's fromisoformat accepts 3 or 6 fractional digits; Alpaca sends 9.
_NANOS = re.compile(r"(\.\d{6})\d+")

_ALIASES: dict[str, tuple[str, ...]] = {
    "implied_volatility": ("impliedVolatility",),
    "latest_quote": ("latestQuote",),
    "latest_trade": ("latestTrade",),
    "bid_price": ("bp",),
    "ask_price": ("ap",),
    "bid_size": ("bs",),
    "ask_size": ("as",),
}


class CLIError(RuntimeError):
    """A CLI invocation failed.

    ``exit_code`` follows the CLI's convention: 1 for an API/usage error and 2
    for an authentication failure. ``permanent`` marks the errors where a retry
    can never help, so :func:`options_agent.broker.retrying` stops early.
    """

    def __init__(self, message: str, exit_code: int = 1, permanent: bool = False):
        super().__init__(message)
        self.exit_code = exit_code
        self.permanent = permanent


class Obj:
    """Attribute-access view over a parsed JSON object.

    Missing keys return ``None`` rather than raising, which matches how the
    calling code already treats absent fields — a contract with no Greeks is a
    normal event on the indicative feed, not an error.
    """

    __slots__ = ("_d",)

    def __init__(self, data: dict[str, Any]):
        object.__setattr__(self, "_d", data)

    def __getattr__(self, name: str) -> Any:
        d = object.__getattribute__(self, "_d")
        if name in d:
            return _coerce(name, d[name])
        for alias in _ALIASES.get(name, ()):
            if alias in d:
                return _coerce(name, d[alias])
        return None

    def __getitem__(self, key: str) -> Any:
        return _coerce(key, object.__getattribute__(self, "_d")[key])

    def __contains__(self, key: str) -> bool:
        return key in object.__getattribute__(self, "_d")

    def get(self, key: str, default: Any = None) -> Any:
        d = object.__getattribute__(self, "_d")
        return _coerce(key, d[key]) if key in d else default

    def to_dict(self) -> dict[str, Any]:
        return dict(object.__getattribute__(self, "_d"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Obj({object.__getattribute__(self, '_d')!r})"


def parse_timestamp(value: str) -> datetime | str:
    """Parse an Alpaca ISO-8601 timestamp, returning the input if it will not parse.

    Returning the raw string on failure keeps a surprising timestamp format from
    taking down a whole cycle over a field most callers only display.
    """
    try:
        return datetime.fromisoformat(_NANOS.sub(r"\1", value).replace("Z", "+00:00"))
    except ValueError:
        return value


def _coerce(key: str, value: Any) -> Any:
    """Wrap a value, parsing it to a datetime when the field name calls for it."""
    if key in _DATETIME_KEYS and isinstance(value, str) and value:
        return parse_timestamp(value)
    return wrap(value)


def wrap(value: Any) -> Any:
    """Recursively present dicts as :class:`Obj`, leaving scalars alone."""
    if isinstance(value, dict):
        return Obj(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value


_CLI_PATH: str | None = None


def _looks_executable(path: str) -> bool:
    """True if `path` is plausibly a real program rather than a stray file.

    A failed download can leave a short text file sitting where the binary
    should be — one such artifact (nine bytes reading "Not Found") was found
    shadowing the real CLI on PATH, and it surfaces as a bare
    ``Exec format error`` that says nothing about the actual cause. Checking
    the magic bytes turns that into a message naming the file.
    """
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        return False
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        return False
    # ELF, a shebang script, or a Mach-O binary (universal or 64-bit).
    return magic[:4] == b"\x7fELF" or magic[:2] == b"#!" or magic[:4] in (
        b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"
    )


def cli_path() -> str:
    """Locate a usable `alpaca` binary.

    ``go install`` drops it in ``~/go/bin``, which is not on PATH in a bare cron
    environment — checking there explicitly is what keeps the scheduled run
    working when the interactive shell would have found it anyway. Candidates
    that exist but are not real executables are skipped rather than returned,
    so a stale file cannot shadow a working install.
    """
    global _CLI_PATH
    if _CLI_PATH is not None:
        return _CLI_PATH

    candidates = [
        shutil.which("alpaca"),
        os.path.expanduser("~/go/bin/alpaca"),
        "/usr/local/bin/alpaca",
    ]
    skipped: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        if _looks_executable(candidate):
            _CLI_PATH = candidate
            return candidate
        if os.path.exists(candidate):
            skipped.append(candidate)

    hint = ""
    if skipped:
        hint = (
            f" Found {', '.join(skipped)} but it is not a working executable — "
            "remove it so it stops shadowing a real install."
        )
    raise CLIError(
        "The `alpaca` CLI is not installed or not on PATH. Install it with "
        "`go install github.com/alpacahq/cli/cmd/alpaca@latest`." + hint,
        exit_code=1,
        permanent=True,
    )


def run(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> Any:
    """Invoke the CLI and return its parsed JSON stdout.

    The CLI reports failures two ways: a non-zero exit code, and — for some
    argument errors — exit 0 with an ``error`` field in the JSON body. Both are
    treated as failures here so a malformed request cannot be mistaken for an
    empty result.
    """
    cmd = [cli_path(), *args, "--quiet"]
    env = dict(os.environ)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise CLIError(f"`alpaca {' '.join(args[:3])}` timed out after {timeout}s") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        detail = _extract_error(stdout) or _extract_error(stderr) or stderr or stdout
        raise CLIError(
            f"`alpaca {' '.join(args[:3])}` failed (exit {proc.returncode}): {detail}",
            exit_code=proc.returncode,
            # Exit 2 is an auth failure and a 4xx is a settled rejection; neither
            # improves by waiting. Rate limits and 5xx are already retried by the
            # CLI itself before it ever returns non-zero.
            permanent=proc.returncode == 2 or _is_client_error(detail),
        )

    if not stdout:
        return None

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CLIError(f"`alpaca {' '.join(args[:3])}` returned non-JSON output: {stdout[:200]}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise CLIError(
            f"`alpaca {' '.join(args[:3])}` reported: {payload['error']}",
            exit_code=int(payload.get("code") or 1),
            permanent=True,
        )

    return payload


def _extract_error(text: str) -> str | None:
    """Pull the message out of the CLI's structured JSON error, if present."""
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("message")
        return str(message) if message else None
    return None


def _is_client_error(detail: str) -> bool:
    """True when the error text names a settled 4xx (but not a rate limit)."""
    text = (detail or "").lower()
    if "429" in text or "too many requests" in text:
        return False
    return any(code in text for code in ("400", "401", "403", "404", "422")) or (
        "forbidden" in text or "unauthorized" in text or "not found" in text
    )


def flags(**kwargs: Any) -> list[str]:
    """Render keyword arguments as CLI flags, dropping the unset ones.

    ``strike_price_gte=745`` becomes ``--strike-price-gte 745``. Booleans render
    as bare flags when true and vanish when false.
    """
    out: list[str] = []
    for key, value in kwargs.items():
        if value is None:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                out.append(flag)
            continue
        out.extend([flag, str(value)])
    return out
