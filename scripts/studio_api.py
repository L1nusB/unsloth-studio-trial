#!/usr/bin/env python3
"""Call the Unsloth Studio REST API with automatic auth — for driving Studio from a
script or over SSH without ever passing a secret on the command line or in chat.

Credential is read from the ENVIRONMENT (e.g. the RunPod template env var already present
on the pod), in this order:
  1. STUDIO_API_KEY            -> sent as `Authorization: Bearer <key>`
  2. STUDIO_ADMIN_PASSWORD     -> POST /api/auth/login as STUDIO_ADMIN_USERNAME (default
                                  "unsloth") to obtain a short-lived bearer token
The password/token never leave this process; only the JSON response is printed to stdout
(HTTP status to stderr). Run it ON the pod so it hits http://localhost:8000 and reads the
pod's own env (the password set via the template's STUDIO_ADMIN_PASSWORD).

USAGE (on the pod):
  python3 studio_api.py GET  /api/auth/status
  python3 studio_api.py GET  /api/train/status
  python3 studio_api.py POST /api/auth/api-keys '{"name":"agent","expires_in_days":7}'
  python3 studio_api.py POST /api/train/start   @payload.json
  echo '{"...":"..."}' | python3 studio_api.py POST /api/train/start -

ENV: STUDIO_URL (default http://localhost:8000)
Exit 0 on a 2xx response; non-zero otherwise.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _request(method: str, url: str, token: str, body: bytes | None) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (fixed localhost)
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _login(base: str) -> str:
    username = os.environ.get("STUDIO_ADMIN_USERNAME", "unsloth")
    password = os.environ.get("STUDIO_ADMIN_PASSWORD")
    if not password:
        sys.exit(
            "No credential: set STUDIO_API_KEY or STUDIO_ADMIN_PASSWORD in the environment."
        )
    data = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base}/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.load(resp)["access_token"]
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError) as exc:
        sys.exit(f"Login failed: {exc}")


def _read_body(arg: str | None) -> bytes | None:
    if arg is None:
        return None
    if arg == "-":
        return sys.stdin.buffer.read()
    if arg.startswith("@"):
        return Path(arg[1:]).read_bytes()
    return arg.encode()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.exit("usage: studio_api.py METHOD PATH [json-body | @file | -]")
    method, path = argv[0].upper(), argv[1]
    body = _read_body(argv[2] if len(argv) > 2 else None)

    base = os.environ.get("STUDIO_URL", "http://localhost:8000").rstrip("/")
    token = os.environ.get("STUDIO_API_KEY") or _login(base)

    url = f"{base}{path if path.startswith('/') else '/' + path}"
    status, text = _request(method, url, token, body)
    print(f"HTTP {status}", file=sys.stderr)
    print(text)
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
