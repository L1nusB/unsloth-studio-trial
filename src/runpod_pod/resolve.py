"""Resolve a pod's direct-TCP (host, port). Primary: ``runpodctl ssh info`` (clean
JSON; spec §6/§15). Fallback: REST GET /pods/{id} via in-process urllib (HIGH-1: the
API key never enters a subprocess argv). Retries on a not-ready mapping (HIGH-3),
then a single explicit abort (MED-2)."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from loguru import logger

from runpod_pod.config import PodConfig
from runpod_pod.errors import ResolveError

_REST_URL = "https://rest.runpod.io/v1/pods/{pod_id}"


def _runpodctl_ssh_info(pod_id: str) -> tuple[str, int] | None:
    try:
        proc = subprocess.run(
            ["runpodctl", "ssh", "info", pod_id],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if data.get("error"):  # e.g. {"error":"pod not ready"} → not yet
        return None
    ip, port = data.get("ip"), data.get("port")
    return (str(ip), int(port)) if ip and port else None


def _rest_resolve(pod_id: str, api_key: str) -> tuple[str, int] | None:
    req = urllib.request.Request(
        _REST_URL.format(pod_id=pod_id), headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (fixed https host)
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    ip = data.get("publicIp")
    port = (data.get("portMappings") or {}).get("22")
    return (str(ip), int(port)) if ip and port else None


def resolve_host_port(
    cfg: PodConfig,
    *,
    retries: int = 6,
    delay: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    """Return the pod's (host, port) for direct-TCP SSH, or raise ResolveError."""
    if cfg.ssh_host and cfg.ssh_port:
        return cfg.ssh_host, cfg.ssh_port
    # ``runpodctl ssh info`` is AUTHORITATIVE for the direct-TCP port and is retried across
    # all attempts. REST is only a last resort, AFTER ssh info has failed every attempt — a
    # transient ssh-info miss must never preempt a retry by falling to REST, because REST's
    # portMappings["22"] can report a different, non-working external port than ssh info
    # (observed live: ssh info 19867 reachable vs REST 19868 refused).
    for attempt in range(retries):
        found = _runpodctl_ssh_info(cfg.pod_id)
        if found is not None:
            return found
        if attempt < retries - 1:
            logger.debug(
                "pod {} not ready (attempt {}/{}), retrying",
                cfg.pod_id,
                attempt + 1,
                retries,
            )
            sleep(delay)
    if cfg.api_key:
        found = _rest_resolve(cfg.pod_id, cfg.api_key)
        if found is not None:
            logger.warning(
                "pod {} resolved via REST fallback (ssh info unavailable); "
                "if the port is wrong, set RUNPOD_POD_SSH_HOST/PORT explicitly",
                cfg.pod_id,
            )
            return found
    raise ResolveError(
        f"Could not resolve a direct-TCP host/port for pod {cfg.pod_id} after {retries} attempts. "
        "The pod is proxy-only or not ready — recreate it with TCP port 22 exposed + a public IP."
    )
