"""Unified phase runner for subprocess-based pipeline phases.

Spawns a phase as an OS subprocess, drains its merged stdout/stderr in the
main thread via ``selectors.PollSelector``, enforces a timeout via a monotonic
deadline, and escalates termination over the entire descendant tree (``killpg``
plus a ``psutil`` sweep for grandchildren that detached via their own
``setsid()``). Forwards orchestrator ``SIGINT``/``SIGTERM`` to the active
phase so a job-scheduler kill never orphans a GPU-holding subprocess.

This single primitive is used by both the training subprocess (unbounded
timeout, terminal streaming) and the per-eval subprocess (bounded timeout,
durable log file). It is also the seam at which a future workflow-library
integration (e.g. Prefect / Snakemake / Metaflow) can substitute its own
implementation without touching the orchestrator.
"""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import IO

import psutil
from loguru import logger


class PhaseOutcome(StrEnum):
    SUCCESS = "success"
    NONZERO_EXIT = "nonzero_exit"
    TIMEOUT = "timeout"
    LAUNCH_FAILED = "launch_failed"
    KEYBOARD_INTERRUPT = "keyboard_interrupt"
    KILLED_BY_SIGNAL = "killed_by_signal"


@dataclass(frozen=True, slots=True)
class PhaseSpec:
    """Configuration for a single phase invocation."""

    name: str
    cmd: Sequence[str]
    env_overrides: Mapping[str, str] = field(default_factory=dict)
    env_strip: Sequence[str] = ()
    env_strip_prefixes: Sequence[str] = ()
    timeout_seconds: float | None = None
    log_file: Path | None = None
    stream_to_terminal: bool = True
    cwd: Path | None = None


@dataclass(frozen=True, slots=True)
class PhaseResult:
    """Structured result of a single phase invocation."""

    name: str
    outcome: PhaseOutcome
    returncode: int | None
    duration_s: float
    detail: str | None = None


def _classify_outcome(
    returncode: int | None, *, timed_out: bool, interrupted: bool
) -> PhaseOutcome:
    """Map the raw subprocess return code + flags to a PhaseOutcome.

    The order of checks is load-bearing: ``returncode == 0`` wins over
    ``timed_out`` so a child that exited cleanly microseconds after the
    deadline is reported as SUCCESS (HIGH-4 race fix).
    """
    if interrupted:
        return PhaseOutcome.KEYBOARD_INTERRUPT
    if returncode == 0:
        return PhaseOutcome.SUCCESS
    if timed_out:
        return PhaseOutcome.TIMEOUT
    if returncode is not None and returncode < 0:
        return PhaseOutcome.KILLED_BY_SIGNAL
    return PhaseOutcome.NONZERO_EXIT


def _build_env(spec: PhaseSpec) -> dict[str, str]:
    """Resolve the child process environment from os.environ + spec rules.

    Order: copy os.environ → drop spec.env_strip keys → drop keys matching
    spec.env_strip_prefixes → apply spec.env_overrides (last wins).
    """
    env = dict(os.environ)
    # Default the child to unbuffered stdio so progress bars and log lines
    # stream live through the parent's drain loop. Caller overrides win.
    env.setdefault("PYTHONUNBUFFERED", "1")
    for key in spec.env_strip:
        env.pop(key, None)
    if spec.env_strip_prefixes:
        prefixes = tuple(spec.env_strip_prefixes)
        for key in [k for k in env if k.startswith(prefixes)]:
            env.pop(key, None)
    env.update(spec.env_overrides)
    return env


def _emit(data: bytes, log_fh: IO[bytes] | None, *, stream_to_terminal: bool) -> None:
    """Forward ``data`` to the persisted log file and/or the parent terminal.

    Both sinks are best-effort; write failures on either are swallowed so a
    broken sink does not stall the drain loop. The terminal sink flushes
    after every write so ``\\r``-based progress bars (tqdm) and partial
    chunks render live instead of stalling inside the parent's
    ``BufferedWriter`` until a buffer fill or shutdown.
    """
    if log_fh is not None:
        with contextlib.suppress(OSError, ValueError):
            log_fh.write(data)
    if stream_to_terminal:
        buffer = getattr(sys.stderr, "buffer", None)
        if buffer is not None:
            with contextlib.suppress(OSError, ValueError):
                buffer.write(data)
                buffer.flush()


def _terminate_tree(
    proc: subprocess.Popen[bytes], *, grace_s: float, kill_wait_s: float
) -> None:
    """Best-effort termination of the entire descendant tree.

    Strategy:
      1. SIGTERM to the process group via ``os.killpg``.
      2. Wait up to ``grace_s`` — root reaped via ``proc.wait`` (subprocess
         module owns the waitpid for direct children), descendants via
         ``psutil.wait_procs`` in the time remaining.
      3. SIGKILL to the process group for any survivors.
      4. ``psutil.children(recursive=True)`` reverse-ordered, individually
         SIGKILL'd — catches grandchildren that detached via their own
         ``setsid()``.
      5. Final reap with ``kill_wait_s``.

    The root is intentionally kept out of ``psutil.wait_procs``: psutil's
    ``Process.wait`` calls ``os.waitpid`` for direct children, which would
    steal the exit status from ``subprocess.Popen`` and leave its
    ``returncode`` set to 0 on ECHILD.

    Idempotent. Safe to call on already-exited processes. Never raises:
    NoSuchProcess / ProcessLookupError / OSError / AttributeError suppressed.
    """
    if proc.poll() is not None:
        return

    try:
        root = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return

    descendants = root.children(recursive=True)
    descendants.reverse()  # grandchildren first

    pgid: int | None
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None

    if pgid is not None:
        with contextlib.suppress(ProcessLookupError, OSError, AttributeError):
            os.killpg(pgid, signal.SIGTERM)

    grace_deadline = time.monotonic() + grace_s
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=grace_s)
    remaining = max(0.0, grace_deadline - time.monotonic())
    _, alive_descendants = psutil.wait_procs(descendants, timeout=remaining)

    if proc.poll() is None or alive_descendants:
        if pgid is not None:
            with contextlib.suppress(ProcessLookupError, OSError, AttributeError):
                os.killpg(pgid, signal.SIGKILL)

        # Sweep any descendants that escaped the original session.
        for child in alive_descendants:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                child.kill()

        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=kill_wait_s)
        final_alive = psutil.wait_procs(alive_descendants, timeout=kill_wait_s)[1]
        if final_alive:
            survivor_pids = [p.pid for p in final_alive]
            # D-state processes can be in this list and cannot be killed.
            logger.warning(
                "[process_runner] {} subprocess descendants still alive after SIGKILL: {}",
                len(final_alive),
                survivor_pids,
            )


_SIGNAL_FORWARDER_GRACE_S = 5.0
_SIGNAL_FORWARDER_KILL_WAIT_S = 2.0


class _SignalForwarder:
    """Install SIGINT/SIGTERM handlers that terminate an attached subprocess
    tree and re-raise as KeyboardInterrupt. Restores prior handlers on exit.

    Only works on the main thread (CPython requirement for ``signal.signal``).
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._prior_int: object = None
        self._prior_term: object = None

    def attach(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc

    def __enter__(self) -> _SignalForwarder:
        self._prior_int = signal.getsignal(signal.SIGINT)
        self._prior_term = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)
        return self

    def __exit__(self, *_exc: object) -> None:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, self._prior_int)  # type: ignore[arg-type]
            signal.signal(signal.SIGTERM, self._prior_term)  # type: ignore[arg-type]

    def _handler(self, signum: int, _frame: object) -> None:
        if self._proc is not None and self._proc.poll() is None:
            with contextlib.suppress(Exception):
                _terminate_tree(
                    self._proc,
                    grace_s=_SIGNAL_FORWARDER_GRACE_S,
                    kill_wait_s=_SIGNAL_FORWARDER_KILL_WAIT_S,
                )
        # Convert SIGTERM to KeyboardInterrupt so the main loop's exception
        # path runs uniformly for both Ctrl-C and scheduler-initiated kills.
        raise KeyboardInterrupt(f"signal {signum}")


_DEFAULT_KILL_GRACE_S = 15.0
_DEFAULT_POST_KILL_WAIT_S = 2.0
_FINAL_DRAIN_CAP_S = 2.0
_FINAL_REAP_TIMEOUT_S = 2.0


def run_phase(spec: PhaseSpec) -> PhaseResult:
    """Spawn ``spec.cmd`` as a subprocess, drain its merged output, and enforce
    timeout / signal-escalation policy. Returns a structured PhaseResult.

    The child runs in its own POSIX session (``start_new_session=True``) so
    ``_terminate_tree`` can reach the entire descendant graph. Output is
    drained in this thread via ``selectors.PollSelector`` (matching CPython's
    POSIX ``_communicate``) and tee'd to ``spec.log_file`` and/or the parent
    terminal via ``_emit``.

    Raises:
        KeyboardInterrupt: if SIGINT or SIGTERM was delivered to the
            orchestrator while the phase was running. The subprocess tree
            is fully terminated before the exception propagates.
    """
    log_fh: IO[bytes] | None = None
    if spec.log_file is not None:
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = spec.log_file.open("ab", buffering=0)

    started = time.monotonic()
    interrupted = False
    timed_out = False
    proc: subprocess.Popen[bytes] | None = None
    rc: int | None = None
    outcome = PhaseOutcome.LAUNCH_FAILED

    try:
        with _SignalForwarder() as forwarder:
            try:
                proc = subprocess.Popen(
                    list(spec.cmd),
                    env=_build_env(spec),
                    cwd=str(spec.cwd) if spec.cwd is not None else None,
                    start_new_session=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                )
            except (OSError, ValueError) as e:
                return PhaseResult(
                    name=spec.name,
                    outcome=PhaseOutcome.LAUNCH_FAILED,
                    returncode=None,
                    duration_s=time.monotonic() - started,
                    detail=f"{type(e).__name__}: {e}",
                )
            forwarder.attach(proc)
            assert proc.stdout is not None  # guaranteed by stdout=PIPE

            sel = selectors.PollSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            buf = bytearray()
            deadline = (
                started + spec.timeout_seconds
                if spec.timeout_seconds is not None
                else None
            )

            try:
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        timed_out = True
                        break
                    select_timeout = (
                        min(deadline - time.monotonic(), 0.5)
                        if deadline is not None
                        else 0.5
                    )
                    for key, _ in sel.select(timeout=max(select_timeout, 0.0)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            sel.unregister(key.fileobj)
                            continue
                        # Stream to terminal immediately so \r-based progress
                        # bars render live; line-buffer the log file so
                        # records aren't split across writes.
                        _emit(
                            chunk,
                            log_fh=None,
                            stream_to_terminal=spec.stream_to_terminal,
                        )
                        buf += chunk
                        while (nl := buf.find(b"\n")) >= 0:
                            _emit(
                                bytes(buf[: nl + 1]),
                                log_fh,
                                stream_to_terminal=False,
                            )
                            del buf[: nl + 1]
                    if proc.poll() is not None and not sel.get_map():
                        break
            except KeyboardInterrupt:
                interrupted = True
            finally:
                if buf:
                    _emit(
                        bytes(buf),
                        log_fh,
                        stream_to_terminal=False,
                    )
                    buf.clear()

            if timed_out or interrupted:
                _terminate_tree(
                    proc,
                    grace_s=_DEFAULT_KILL_GRACE_S,
                    kill_wait_s=_DEFAULT_POST_KILL_WAIT_S,
                )

            _drain_remaining(
                proc,
                log_fh,
                stream_to_terminal=spec.stream_to_terminal,
                cap_s=_FINAL_DRAIN_CAP_S,
            )

            with contextlib.suppress(OSError):
                proc.stdout.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_FINAL_REAP_TIMEOUT_S)

        rc = proc.returncode
        outcome = _classify_outcome(rc, timed_out=timed_out, interrupted=interrupted)
    finally:
        if log_fh is not None:
            with contextlib.suppress(OSError):
                log_fh.close()

    if interrupted:
        raise KeyboardInterrupt
    return PhaseResult(
        name=spec.name,
        outcome=outcome,
        returncode=rc,
        duration_s=time.monotonic() - started,
        detail=_detail_for(outcome, rc),
    )


def _drain_remaining(
    proc: subprocess.Popen[bytes],
    log_fh: IO[bytes] | None,
    *,
    stream_to_terminal: bool,
    cap_s: float,
) -> None:
    """Drain any final bytes from ``proc.stdout`` after termination, with a
    short hard cap so we never block waiting on a wedged grandchild fd.
    """
    if proc.stdout is None:
        return
    deadline = time.monotonic() + cap_s
    sel = selectors.PollSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    buf = bytearray()
    try:
        while time.monotonic() < deadline:
            for key, _ in sel.select(timeout=0.1):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    return
                _emit(chunk, log_fh=None, stream_to_terminal=stream_to_terminal)
                buf += chunk
                while (nl := buf.find(b"\n")) >= 0:
                    _emit(
                        bytes(buf[: nl + 1]),
                        log_fh,
                        stream_to_terminal=False,
                    )
                    del buf[: nl + 1]
    finally:
        if buf:
            _emit(bytes(buf), log_fh, stream_to_terminal=False)


def _detail_for(outcome: PhaseOutcome, rc: int | None) -> str | None:
    if outcome is PhaseOutcome.SUCCESS:
        return None
    if outcome is PhaseOutcome.NONZERO_EXIT:
        return f"exit code {rc}"
    if outcome is PhaseOutcome.KILLED_BY_SIGNAL:
        return f"killed by signal {abs(rc) if rc is not None else '?'}"
    if outcome is PhaseOutcome.TIMEOUT:
        return "timed out and tree was terminated"
    if outcome is PhaseOutcome.KEYBOARD_INTERRUPT:
        return "interrupted by signal"
    return None
