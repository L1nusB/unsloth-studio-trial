"""Build remote command strings for ssh. User commands are base64-wrapped so no
shell parses them in transit (injection-proof; spec §7 / CRIT-1)."""

from __future__ import annotations

import base64
import shlex


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _wrap(script: str) -> str:
    """Wrap a full bash script so the pod decodes and runs it: no quoting in transit."""
    return f"printf %s {_b64(script)} | base64 -d | bash"


def _single_quote(text: str) -> str:
    """Always wrap ``text`` in single quotes (POSIX-safe, incl. embedded quotes).

    Unlike ``shlex.quote`` this never elides the quotes for shell-safe strings, so
    paths land inside an explicit literal — deterministic and grep-able in tests.
    """
    return "'" + text.replace("'", "'\\''") + "'"


def foreground_remote(cmd: str, repo_dir: str) -> str:
    """Run ``cmd`` in ``repo_dir`` on the pod, foreground; exit code propagates."""
    script = f"cd {_single_quote(repo_dir)} || exit 1\n{cmd}\n"
    return _wrap(script)


def detached_remote(cmd: str, repo_dir: str, *, session: str, log_path: str) -> str:
    """Launch ``cmd`` under tmux, tee to ``log_path``, capture exit code to ``log_path``.exitcode.

    ``session``/``log_path`` are tool-generated and safe-charactered; ``cmd`` is
    written to a file via base64 decode so it never enters a quoted literal.
    """
    payload_b64 = _b64(cmd)
    cmd_file = f"{log_path}.cmd"
    inner = (
        f"bash {shlex.quote(cmd_file)} 2>&1 | tee {shlex.quote(log_path)}; "
        f"echo ${{PIPESTATUS[0]}} > {shlex.quote(log_path)}.exitcode"
    )
    launcher = (
        f"cd {_single_quote(repo_dir)} || exit 1\n"
        f"mkdir -p {shlex.quote(log_path.rsplit('/', 1)[0])}\n"
        f"printf %s {payload_b64} | base64 -d > {shlex.quote(cmd_file)}\n"
        f"tmux new-session -d -s {shlex.quote(session)} bash -c {shlex.quote(inner)}\n"
    )
    return _wrap(launcher)


def status_remote(session: str, log_path: str) -> str:
    """One-liner that prints RUNNING / EXITED <code> / GONE for a detached job."""
    s, lg = shlex.quote(session), shlex.quote(log_path)
    return (
        f"if tmux has-session -t {s} 2>/dev/null; then echo RUNNING; "
        f"elif [ -f {lg}.exitcode ]; then echo EXITED $(cat {lg}.exitcode); "
        f"else echo GONE; fi"
    )
