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
import urllib.parse
import urllib.request
from pathlib import Path


# RunPod's HTTP proxy (Cloudflare) returns 403 to the default Python-urllib User-Agent (bot
# filtering). A browser-like UA is required for any API call routed through the proxy URL.
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _origin(url: str) -> str:
    """scheme://host[:port] for the given URL — Studio's auth enforces a same-origin
    Origin/Referer (a CSRF guard), so requests via the RunPod proxy must echo the proxy
    origin the same way the browser does, or login is refused with 403 even on a valid password."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _request(method: str, url: str, token: str, body: bytes | None) -> tuple[int, str]:
    origin = _origin(url)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": _UA, "Origin": origin, "Referer": origin + "/"}
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
        headers={"Content-Type": "application/json", "User-Agent": _UA, "Origin": base, "Referer": base + "/"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.load(resp)["access_token"]
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError) as exc:
        sys.exit(f"Login failed: {exc}")


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a local .env into os.environ (without overriding existing).

    Lets this run from the Mac repo root with RUNPOD_POD_ID + STUDIO_ADMIN_PASSWORD in .env,
    so it targets the proxy and authenticates without any secret on the command line/in chat.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _resolve_base() -> str:
    """STUDIO_URL override > RunPod proxy (load-proof, when RUNPOD_POD_ID set) > localhost."""
    explicit = os.environ.get("STUDIO_URL")
    if explicit:
        return explicit.rstrip("/")
    pod = os.environ.get("RUNPOD_POD_ID")
    if pod:
        return f"https://{pod}-8000.proxy.runpod.net"
    return "http://localhost:8000"


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

    _load_dotenv()
    base = _resolve_base()
    token = os.environ.get("STUDIO_API_KEY") or _login(base)

    url = f"{base}{path if path.startswith('/') else '/' + path}"
    status, text = _request(method, url, token, body)
    print(f"HTTP {status}", file=sys.stderr)
    print(text)
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
