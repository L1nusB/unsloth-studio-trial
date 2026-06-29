"""Load pod-tool configuration from .env / env / runpodctl config, with precedence."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import overload

from runpod_pod.errors import ConfigError

_DEFAULT_SSH_KEY = "~/.runpod/ssh/runpodctl-ssh-key"
# This repo's clone target on the pod (override with RUNPOD_POD_REPO_DIR). Only matters
# for sync/pull and doctor's repo check; SSH `run`/the `-L` tunnel don't depend on it.
_DEFAULT_REPO_DIR = "/workspace/unsloth-studio-trial"
_DEFAULT_USER = "root"
_DEFAULT_RUNPOD_TOML = Path("~/.runpod/config.toml")


@dataclass(frozen=True, slots=True)
class PodConfig:
    pod_id: str
    ssh_key: Path
    repo_dir: str
    user: str
    api_key: str | None
    ssh_host: str | None
    ssh_port: int | None


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` .env file (stdlib; no python-dotenv)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip().strip('"').strip("'")
    return out


@overload
def _resolve(
    name: str, env_file: dict[str, str], overrides: dict[str, str], default: str
) -> str: ...
@overload
def _resolve(
    name: str, env_file: dict[str, str], overrides: dict[str, str], default: None = None
) -> str | None: ...
def _resolve(
    name: str,
    env_file: dict[str, str],
    overrides: dict[str, str],
    default: str | None = None,
) -> str | None:
    """Precedence: CLI override > os.environ > .env file > default."""
    if overrides.get(name):
        return overrides[name]
    if os.environ.get(name):
        return os.environ[name]
    if env_file.get(name):
        return env_file[name]
    return default


def _api_key_from_toml(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    # runpodctl writes the key as ``apikey`` (lowercase); some docs/specs say ``apiKey``.
    # Accept either so the REST fallback actually gets a key from the real config.toml.
    for name in ("apikey", "apiKey"):
        value = data.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def load_config(
    *,
    project_root: Path,
    overrides: dict[str, str] | None = None,
    runpod_config_toml: Path | None = None,
) -> PodConfig:
    """Build a PodConfig from .env / env / runpodctl config.toml."""
    overrides = overrides or {}
    env_file = parse_env_file(project_root / ".env")

    pod_id = _resolve("RUNPOD_POD_ID", env_file, overrides)
    if not pod_id:
        raise ConfigError(
            "RUNPOD_POD_ID is not set — add it to .env or pass --pod <id>."
        )

    ssh_key = Path(
        _resolve("RUNPOD_POD_SSH_KEY", env_file, overrides, _DEFAULT_SSH_KEY)
    ).expanduser()
    repo_dir = _resolve("RUNPOD_POD_REPO_DIR", env_file, overrides, _DEFAULT_REPO_DIR)
    user = _resolve("RUNPOD_POD_USER", env_file, overrides, _DEFAULT_USER)

    api_key = _resolve("RUNPOD_API_KEY", env_file, overrides)
    if not api_key:
        toml_path = (runpod_config_toml or _DEFAULT_RUNPOD_TOML).expanduser()
        api_key = _api_key_from_toml(toml_path)

    host = _resolve("RUNPOD_POD_SSH_HOST", env_file, overrides)
    port_raw = _resolve("RUNPOD_POD_SSH_PORT", env_file, overrides)
    port = int(port_raw) if port_raw else None

    return PodConfig(pod_id, ssh_key, repo_dir, user, api_key, host, port)
