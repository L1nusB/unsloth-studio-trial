"""Persisted detached-job handles. The agent is stateless between tool calls, so a
``run --detach`` handle is written to disk (incl. pod id + host) for later
status/logs (spec §7, MED-4)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_STORE = Path(".local_data/pod_jobs")
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class DetachedJobHandle:
    name: str
    pod_id: str
    host: str
    remote_log: str
    created_at: str


def make_session_name(label: str, *, ts: str, rand_hex: str) -> str:
    """Build a safe, collision-resistant tmux session name ``<ts>-<label>-<hex>``."""
    clean = _SAFE.sub("", label) or "run"
    return f"{ts}-{clean}-{rand_hex}"


def save_handle(handle: DetachedJobHandle, *, store_dir: Path = DEFAULT_STORE) -> Path:
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / f"{handle.name}.json"
    path.write_text(json.dumps(asdict(handle), indent=2))
    return path


def load_handle(
    name: str, *, store_dir: Path = DEFAULT_STORE
) -> DetachedJobHandle | None:
    path = store_dir / f"{name}.json"
    if not path.exists():
        return None
    return DetachedJobHandle(**json.loads(path.read_text()))
