"""Subcommand handlers. Each receives a resolved (cfg, host, port) and returns an exit code.
Streaming subprocesses go through process_runner.run_phase; structured results
(status/doctor) are printed to stdout (spec LOW-1)."""

from __future__ import annotations

import os
import secrets
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from runpod_pod import jobs, remote, ssh
from runpod_pod.config import PodConfig
from runpod_pod.errors import ResolveError
from runpod_pod.process_runner import PhaseResult, PhaseSpec, run_phase
from runpod_pod.resolve import resolve_host_port

_AGENT_RUNS = ".agent_runs"
_PERSONAL_KEY_CANDIDATES: tuple[str, ...] = (
    "~/.ssh/id_rsa",
    "~/.ssh/id_ed25519",
    "~/.ssh/id_runpod",
)


def _now_ts() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")


def _rand_hex() -> str:
    return secrets.token_hex(2)


def _result_rc(result: PhaseResult) -> int:
    return result.returncode if result.returncode is not None else 1


def _ssh_capture(
    argv: list[str], *, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    """Run an ssh argv capturing stdout/stderr, mapping a timeout to a synthetic failure.

    Used by ``status``/``doctor`` which need the captured stdout to classify or report
    (unlike the streaming handlers that go through run_phase). BatchMode + the ssh-level
    ConnectTimeout bound the connect, but a hung post-connect remote shell still needs a
    Python-level cap so the tool never blocks forever in automation.
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, "", f"ssh timed out after {timeout:g}s"
        )


def cmd_run(
    cfg: PodConfig,
    host: str,
    port: int,
    *,
    command: str,
    detach: bool,
    timeout: float | None,
    label: str = "run",
    store_dir: Path = jobs.DEFAULT_STORE,
) -> int:
    """Run ``command`` on the pod — foreground (streamed, exit-code propagated) or detached."""
    if not detach:
        remote_cmd = remote.foreground_remote(command, cfg.repo_dir)
        spec = PhaseSpec(
            name="pod-run",
            cmd=ssh.ssh_argv(cfg, host, port, remote=remote_cmd, connect_timeout=15),
            timeout_seconds=timeout,
            stream_to_terminal=True,
        )
        result: PhaseResult = run_phase(spec)
        return result.returncode if result.returncode is not None else 1

    session = jobs.make_session_name(label, ts=_now_ts(), rand_hex=_rand_hex())
    remote_log = f"{cfg.repo_dir}/{_AGENT_RUNS}/{session}.log"
    remote_cmd = remote.detached_remote(
        command, cfg.repo_dir, session=session, log_path=remote_log
    )
    result = run_phase(
        PhaseSpec(
            name="pod-run-detach",
            cmd=ssh.ssh_argv(cfg, host, port, remote=remote_cmd),
            stream_to_terminal=False,
        )
    )
    if result.returncode != 0:
        logger.error("detached launch failed: {}", result.detail)
        return _result_rc(result)
    handle = jobs.DetachedJobHandle(
        name=session,
        pod_id=cfg.pod_id,
        host=host,
        remote_log=remote_log,
        created_at=_now_ts(),
    )
    jobs.save_handle(handle, store_dir=store_dir)
    print(session)
    print(f"logs:   uv run pod logs {session}", file=sys.stderr)
    print(f"status: uv run pod status {session}", file=sys.stderr)
    return 0


def cmd_sync(
    cfg: PodConfig, host: str, port: int, *, local_root: Path, dry_run: bool
) -> int:
    """rsync the local working tree into the pod repo dir."""
    spec = PhaseSpec(
        name="pod-sync",
        cmd=ssh.rsync_push_argv(cfg, host, port, local_root, dry_run=dry_run),
        stream_to_terminal=True,
    )
    return _result_rc(run_phase(spec))


def cmd_pull(
    cfg: PodConfig, host: str, port: int, *, remote_path: str, dest: Path
) -> int:
    """rsync a result file/dir from the pod back to a local dest."""
    dest.mkdir(parents=True, exist_ok=True)
    spec = PhaseSpec(
        name="pod-pull",
        cmd=ssh.rsync_pull_argv(cfg, host, port, remote_path, dest),
        stream_to_terminal=True,
    )
    return _result_rc(run_phase(spec))


def _load_or_die(
    name: str, host: str, store_dir: Path
) -> jobs.DetachedJobHandle | None:
    handle = jobs.load_handle(name, store_dir=store_dir)
    if handle is None:
        logger.error(
            "no detached job handle named {!r} (looked in {})", name, store_dir
        )
        return None
    if handle.host != host:
        logger.warning(
            "handle {} was created for host {}, current target is {} — pod may have changed",
            name,
            handle.host,
            host,
        )
    return handle


def cmd_logs(
    cfg: PodConfig,
    host: str,
    port: int,
    *,
    name: str,
    follow: bool,
    store_dir: Path = jobs.DEFAULT_STORE,
) -> int:
    """Stream a detached job's remote log via tail (-f to follow)."""
    handle = _load_or_die(name, host, store_dir)
    if handle is None:
        return 1
    tail = f"tail {'-f' if follow else '-n +1'} {shlex.quote(handle.remote_log)}"
    spec = PhaseSpec(
        name="pod-logs",
        cmd=ssh.ssh_argv(cfg, host, port, remote=tail),
        stream_to_terminal=True,
    )
    return _result_rc(run_phase(spec))


def cmd_status(
    cfg: PodConfig,
    host: str,
    port: int,
    *,
    name: str,
    store_dir: Path = jobs.DEFAULT_STORE,
) -> int:
    """Print a detached job's state (RUNNING / EXITED <code> / GONE) to stdout."""
    handle = _load_or_die(name, host, store_dir)
    if handle is None:
        return 1
    # Direct capture (not run_phase): we need the remote one-liner's stdout to classify
    # the job; run_phase streams to stderr instead.
    proc = _ssh_capture(
        ssh.ssh_argv(
            cfg, host, port, remote=remote.status_remote(name, handle.remote_log)
        ),
        timeout=30,
    )
    if proc.returncode != 0:
        logger.error(
            "status probe failed (rc={}): {}", proc.returncode, proc.stderr.strip()
        )
        return proc.returncode or 1
    print(proc.stdout.strip())
    return 0


def _check(label: str, ok: bool, detail: str = "") -> bool:
    """Print one PASS/FAIL line to stdout (structured result; LOW-1) and return ok."""
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def is_passphraseless(key: Path) -> bool:
    """True iff the private key has no passphrase (BatchMode requires this)."""
    try:
        proc = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(key)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def is_personal_key(
    key: Path, candidates: tuple[str, ...] = _PERSONAL_KEY_CANDIDATES
) -> bool:
    """True iff ``key`` is the same file as a known personal key (MED-3). Missing
    candidates are skipped so this never raises on a clean machine (HIGH-1)."""
    for cand in candidates:
        p = Path(cand).expanduser()
        if not p.exists():
            continue
        try:
            if os.path.samefile(key, p):
                return True
        except OSError:
            continue
    return False


def cmd_doctor(cfg: PodConfig) -> int:
    """Preflight checks; prints a PASS/FAIL checklist to stdout. Returns 0 iff all pass."""
    # Accumulate key/env/repo failures into `ok` so all problems show in one pass; resolve
    # and the ssh probe early-return because later checks are meaningless without them.
    ok = True
    ok &= _check("agent key exists", cfg.ssh_key.exists(), str(cfg.ssh_key))
    if cfg.ssh_key.exists():
        ok &= _check(
            "agent key is passphraseless",
            is_passphraseless(cfg.ssh_key),
            "a passphrase breaks BatchMode",
        )
        ok &= _check(
            "agent key is not a personal key",
            not is_personal_key(cfg.ssh_key),
            "use a dedicated scoped key (MED-3)",
        )

    try:
        host, port = resolve_host_port(cfg)
    except ResolveError as exc:
        _check("direct-TCP resolved", False, str(exc))
        return 1
    _check("direct-TCP resolved", True, f"{host}:{port}")

    probe = _ssh_capture(
        ssh.ssh_argv(cfg, host, port, remote="true", connect_timeout=8), timeout=20
    )
    if probe.returncode != 0:
        err = probe.stderr.lower()
        if "host key verification failed" in err:
            _check("ssh host key", False, "host-key verification failed")
        elif "permission denied" in err:
            _check(
                "ssh key authorized",
                False,
                "key not on pod — run `runpodctl ssh add-key --key-file "
                f"{cfg.ssh_key}.pub` then redeploy, or append over existing access (spec §15)",
            )
        else:
            _check("ssh reachable", False, probe.stderr.strip()[:200])
        return 1
    _check("ssh reachable + key authorized", True)

    env = _ssh_capture(
        ssh.ssh_argv(
            cfg, host, port, remote="command -v tmux; command -v bash; command -v rsync"
        ),
        timeout=20,
    )
    ok &= _check("tmux present on pod", "tmux" in env.stdout)
    ok &= _check("bash present on pod", "bash" in env.stdout)
    ok &= _check(
        "rsync present on pod (needed for sync/pull)",
        "rsync" in env.stdout,
        "install with: apt-get update && apt-get install -y rsync",
    )

    repo = _ssh_capture(
        ssh.ssh_argv(
            cfg,
            host,
            port,
            remote=f"cd {cfg.repo_dir} && git rev-parse --abbrev-ref HEAD && git rev-parse --short HEAD",
        ),
        timeout=20,
    )
    ok &= _check(
        f"repo dir {cfg.repo_dir}",
        repo.returncode == 0,
        repo.stdout.strip().replace("\n", " @ "),
    )
    return 0 if ok else 1
