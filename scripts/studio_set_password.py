#!/usr/bin/env python3
"""Auto-set the Unsloth Studio admin password from an env var, on a fresh pod.

WHY: on our CLI-install path there is no env-var that seeds the admin password
(`UNSLOTH_ADMIN_PASSWORD` only works in the official Docker image). On first launch
Studio generates a random 4-word bootstrap passphrase, writes it to
`$UNSLOTH_STUDIO_HOME/auth/.bootstrap_password`, creates user `unsloth` with
must_change_password=True, and deletes the file on the first password change. This
script replays the browser "Setup Account" flow over the REST API so a fully torn-down
pod comes up with a *consistent* password with zero manual step:

    read bootstrap file -> POST /api/auth/login -> POST /api/auth/change-password

Idempotent: if the instance is already configured (password already changed) it is a
no-op, so it is safe to run on every boot (incl. persistent /workspace/.studio setups).

RUN (unattended, from the pod start command, after launching Studio):
    STUDIO_ADMIN_PASSWORD='your-consistent-pw' python3 studio_set_password.py &

ENV:
    STUDIO_ADMIN_PASSWORD  (required; >=8 chars) — the password to set. Unset => skip (no-op).
    STUDIO_ADMIN_USERNAME  (default "unsloth")
    STUDIO_URL             (default "http://localhost:8000")
    UNSLOTH_STUDIO_HOME    (default "/root/.unsloth/studio") — to locate the bootstrap file
    STUDIO_WAIT_SECONDS    (default 1800) — how long to wait for Studio to come up

Exit 0 on success / no-op / skip; non-zero only on a real misconfiguration after Studio is up.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _log(msg: str) -> None:
    print(f"[studio-set-password] {msg}", flush=True)


def _post(url: str, body: dict, token: str | None = None) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed localhost)
        return json.load(resp)


def _get_status(url: str) -> dict | None:
    try:
        req = urllib.request.Request(f"{url}/api/auth/status")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError):
        return None


def main() -> int:
    desired = os.environ.get("STUDIO_ADMIN_PASSWORD")
    if not desired:
        _log("STUDIO_ADMIN_PASSWORD not set — leaving the manual browser Setup flow. No-op.")
        return 0
    if len(desired) < 8:
        _log("STUDIO_ADMIN_PASSWORD must be >= 8 chars. Aborting.")
        return 2

    username = os.environ.get("STUDIO_ADMIN_USERNAME", "unsloth")
    url = os.environ.get("STUDIO_URL", "http://localhost:8000").rstrip("/")
    studio_home = Path(os.environ.get("UNSLOTH_STUDIO_HOME", "/root/.unsloth/studio"))
    bootstrap_path = studio_home / "auth" / ".bootstrap_password"
    deadline = time.monotonic() + float(os.environ.get("STUDIO_WAIT_SECONDS", "1800"))

    # 1. Wait for Studio's auth API to answer.
    status: dict | None = None
    while time.monotonic() < deadline:
        status = _get_status(url)
        if status is not None:
            break
        time.sleep(5)
    if status is None:
        _log(f"Studio auth API never came up at {url} within the wait window. Aborting.")
        return 1

    # 2. Already configured? No-op (idempotent across reboots / persisted homes).
    if not status.get("requires_password_change", False):
        _log("Admin password already configured (requires_password_change=false). No-op.")
        return 0

    # 3. Read the one-time bootstrap passphrase Studio wrote to disk.
    #    It may appear a moment after status flips; retry briefly.
    bootstrap = ""
    while time.monotonic() < deadline:
        if bootstrap_path.is_file():
            bootstrap = bootstrap_path.read_text().strip()
            if bootstrap:
                break
        time.sleep(3)
    if not bootstrap:
        _log(f"Bootstrap password file not found/empty at {bootstrap_path}. Aborting.")
        return 1

    # 4. login(unsloth, bootstrap) -> bearer token -> change-password(bootstrap -> desired).
    try:
        tok = _post(f"{url}/api/auth/login", {"username": username, "password": bootstrap})
        access = tok["access_token"]
        _post(
            f"{url}/api/auth/change-password",
            {"current_password": bootstrap, "new_password": desired},
            token=access,
        )
    except urllib.error.HTTPError as exc:
        _log(f"Auth API call failed: HTTP {exc.code} {exc.reason}. Aborting.")
        return 1
    except (urllib.error.URLError, KeyError, TimeoutError) as exc:
        _log(f"Auth API call failed: {exc}. Aborting.")
        return 1

    _log(f"Admin password set for user '{username}' from STUDIO_ADMIN_PASSWORD. Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
