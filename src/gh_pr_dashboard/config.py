"""Configuration: a TOML file with an array of owners, or plain env vars.

Config file (path from PRD_CONFIG, default /config/config.toml when it exists):

    refresh_seconds = 1800
    exclude_repos = ["gx1400/old-thing"]
    # token for owners that don't name their own (default: env GITHUB_TOKEN)
    default_token_env = "GITHUB_TOKEN"

    [[owners]]
    name = "gx1400"                 # uses GITHUB_TOKEN_GX1400 if set, else the default

    [[owners]]
    name = "APRS-NET"
    token_env = "APRS_TOKEN"        # read the token from this env var

    [[owners]]
    name = "WA0RC"
    token_file = "/run/secrets/wa0rc"   # or from a file (re-read every poll)

Without a config file the env vars are used: OWNERS (comma-separated),
GITHUB_TOKEN, GITHUB_TOKEN_<OWNER>, EXCLUDE_REPOS, REFRESH_SECONDS, PORT.

Tokens are never stored in the config itself: a config file is typically a
ConfigMap or committed example, and tokens belong in a Secret.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = "/config/config.toml"
MIN_REFRESH = 60


class ConfigError(ValueError):
    pass


def owner_env_key(owner: str) -> str:
    """GITHUB_TOKEN_<OWNER>: upper-cased, every non-alphanumeric becomes '_'."""
    return "GITHUB_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", owner.upper())


@dataclass(frozen=True)
class TokenSource:
    """Where an owner's token comes from. Resolved on every poll, so a rotated
    file-mounted Secret takes effect without a restart."""

    env: str | None = None
    file: str | None = None

    def describe(self) -> str:
        return f"file {self.file}" if self.file else f"env {self.env}"

    def read(self, environ: Mapping[str, str]) -> str:
        if self.file:
            try:
                return Path(self.file).read_text().strip()
            except OSError as e:
                raise ConfigError(f"cannot read token file {self.file}: {e.strerror}") from None
        value = environ.get(self.env or "", "").strip()
        if not value:
            raise ConfigError(f"env {self.env} is empty or unset")
        return value


@dataclass(frozen=True)
class Owner:
    name: str
    token: TokenSource


@dataclass(frozen=True)
class Config:
    owners: tuple[Owner, ...]
    exclude_repos: frozenset[str] = field(default_factory=frozenset)
    refresh_seconds: int = 1800
    port: int = 8080
    source: str = "env"


def _token_source(entry: Mapping, environ: Mapping[str, str], default: TokenSource) -> TokenSource:
    if "token" in entry:
        raise ConfigError(
            f"owner {entry.get('name')!r}: put tokens in an env var or file "
            "(token_env / token_file), not in the config"
        )
    if entry.get("token_env") and entry.get("token_file"):
        raise ConfigError(f"owner {entry.get('name')!r}: set token_env or token_file, not both")
    if entry.get("token_file"):
        return TokenSource(file=str(entry["token_file"]))
    if entry.get("token_env"):
        return TokenSource(env=str(entry["token_env"]))
    per_owner = owner_env_key(str(entry["name"]))
    if environ.get(per_owner):
        return TokenSource(env=per_owner)
    return default


def _refresh(value) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"refresh_seconds must be an integer, got {value!r}") from None
    return max(MIN_REFRESH, seconds)


def _from_file(path: Path, environ: Mapping[str, str]) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    if data.get("default_token_file"):
        default = TokenSource(file=str(data["default_token_file"]))
    else:
        default = TokenSource(env=str(data.get("default_token_env", "GITHUB_TOKEN")))
    entries = data.get("owners")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: needs at least one [[owners]] entry")
    owners, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ConfigError(f"{path}: every [[owners]] entry needs a name")
        name = str(entry["name"])
        if name.lower() in seen:
            raise ConfigError(f"{path}: owner {name!r} is listed twice")
        seen.add(name.lower())
        owners.append(Owner(name, _token_source(entry, environ, default)))
    return Config(
        owners=tuple(owners),
        exclude_repos=frozenset(r.lower() for r in data.get("exclude_repos", [])),
        refresh_seconds=_refresh(data.get("refresh_seconds", 1800)),
        port=int(data.get("port", environ.get("PORT", 8080))),
        source=str(path),
    )


def _from_env(environ: Mapping[str, str]) -> Config:
    names = [o.strip() for o in environ.get("OWNERS", "").split(",") if o.strip()]
    if not names:
        raise ConfigError("no owners: set OWNERS or provide a config file (PRD_CONFIG)")
    default = TokenSource(env="GITHUB_TOKEN")
    owners = tuple(Owner(n, _token_source({"name": n}, environ, default)) for n in names)
    exclude = environ.get("EXCLUDE_REPOS", "")
    return Config(
        owners=owners,
        exclude_repos=frozenset(r.strip().lower() for r in exclude.split(",") if r.strip()),
        refresh_seconds=_refresh(environ.get("REFRESH_SECONDS", 1800)),
        port=int(environ.get("PORT", 8080)),
    )


def load(environ: Mapping[str, str] | None = None) -> Config:
    environ = os.environ if environ is None else environ
    explicit = environ.get("PRD_CONFIG")
    path = Path(explicit or DEFAULT_CONFIG)
    if explicit and not path.is_file():
        raise ConfigError(f"PRD_CONFIG={explicit} does not exist")
    if path.is_file():
        return _from_file(path, environ)
    return _from_env(environ)
