"""Builders for ssh / rsync argv. Pure functions — no execution. Every connection
carries fixed host-key options because the pod's public IP/port change on every
restart (spec §8.1)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from runpod_pod.config import PodConfig

# Host-key churn: accept new keys, never persist them, never prompt, never hang.
# `-F /dev/null` ignores the user's ~/.ssh/config so behavior is deterministic and
# personal config (e.g. a LocalCommand/terminfo hook) can't inject into our connections (LOW-1).
HOST_KEY_OPTS: tuple[str, ...] = (
    "-F",
    "/dev/null",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "LogLevel=ERROR",
    "-o",
    "BatchMode=yes",
)
_RSYNC_EXCLUDES: tuple[str, ...] = (
    "--exclude=.git",
    "--exclude=.venv",
    "--exclude=__pycache__",
    "--exclude=.local_data",
)
_GITIGNORE_FILTER = "--filter=:- .gitignore"
# Cross-host dev sync: never preserve owner/group. The Mac uid/gid do not exist on the
# pod, so `-a`'s implied -o/-g make rsync attempt a chown it cannot satisfy → repeated
# "chown ... Operation not permitted" and a non-zero exit even though every file's
# content transferred fine. --no-owner/--no-group keep perms+times but skip the chown.
_RSYNC_FLAGS: tuple[str, ...] = ("-az", "--no-owner", "--no-group")


def ssh_argv(
    cfg: PodConfig,
    host: str,
    port: int,
    *,
    remote: str | None = None,
    connect_timeout: int = 8,
    extra_opts: Sequence[str] = (),
) -> list[str]:
    """Build an ssh argv to ``user@host -p port`` running optional ``remote`` command."""
    argv = [
        "ssh",
        "-i",
        str(cfg.ssh_key),
        *HOST_KEY_OPTS,
        "-o",
        f"ConnectTimeout={connect_timeout}",
        *extra_opts,
        "-p",
        str(port),
        f"{cfg.user}@{host}",
    ]
    if remote is not None:
        argv.append(remote)
    return argv


def _ssh_transport(cfg: PodConfig, port: int, *, connect_timeout: int = 8) -> str:
    """The ``-e`` transport string rsync uses (key + host-key opts + ConnectTimeout + port).

    ConnectTimeout is required here too (HIGH-2): BatchMode prevents prompt hangs but
    does not bound the TCP connect, so without it ``sync``/``pull`` against an
    unreachable pod blocks on the OS default (~120s on macOS).
    """
    return " ".join(
        [
            "ssh",
            "-i",
            str(cfg.ssh_key),
            *HOST_KEY_OPTS,
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-p",
            str(port),
        ]
    )


def rsync_push_argv(
    cfg: PodConfig, host: str, port: int, local_root: Path, *, dry_run: bool = False
) -> list[str]:
    """rsync the local working tree (contents) into the pod repo dir. No --delete."""
    argv = [
        "rsync",
        *_RSYNC_FLAGS,
        _GITIGNORE_FILTER,
        *_RSYNC_EXCLUDES,
        "-e",
        _ssh_transport(cfg, port),
    ]
    if dry_run:
        argv.append("-n")
    src = str(local_root).rstrip("/") + "/"  # trailing slash = overlay, not nest
    argv += [src, f"{cfg.user}@{host}:{cfg.repo_dir}/"]
    return argv


def rsync_pull_argv(
    cfg: PodConfig, host: str, port: int, remote_path: str, dest: Path
) -> list[str]:
    """rsync a file/dir from the pod back to a local dest."""
    return [
        "rsync",
        *_RSYNC_FLAGS,
        "-e",
        _ssh_transport(cfg, port),
        f"{cfg.user}@{host}:{remote_path}",
        str(dest),
    ]
