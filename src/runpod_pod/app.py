"""`pod` CLI entrypoint: parse args, load config, resolve once, dispatch a handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from runpod_pod import commands
from runpod_pod.config import load_config
from runpod_pod.errors import PodToolError
from runpod_pod.resolve import resolve_host_port


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pod",
        description="Drive an already-running RunPod pod over direct-TCP SSH.",
    )
    p.add_argument("--pod", help="Override RUNPOD_POD_ID.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Preflight checks.")
    s_sync = sub.add_parser("sync", help="rsync the working tree to the pod.")
    s_sync.add_argument("--dry-run", action="store_true")
    s_run = sub.add_parser("run", help="Run a command on the pod.")
    s_run.add_argument("command")
    s_run.add_argument("--detach", action="store_true")
    s_run.add_argument("--label", default="run")
    s_run.add_argument("--timeout", type=float, default=None)
    s_logs = sub.add_parser("logs", help="Stream a detached job's log.")
    s_logs.add_argument("name")
    s_logs.add_argument("--no-follow", action="store_true")
    s_status = sub.add_parser("status", help="Detached job state.")
    s_status.add_argument("name")
    s_pull = sub.add_parser("pull", help="Fetch a file/dir from the pod.")
    s_pull.add_argument("remote_path")
    s_pull.add_argument("dest", nargs="?", default=".local_data/pod_runs")
    return p


def main() -> int:
    args = build_parser().parse_args()
    overrides = {"RUNPOD_POD_ID": args.pod} if args.pod else {}
    try:
        cfg = load_config(project_root=Path.cwd(), overrides=overrides)
        if args.cmd == "doctor":
            return commands.cmd_doctor(cfg)
        host, port = resolve_host_port(cfg)
        if args.cmd == "sync":
            return commands.cmd_sync(
                cfg, host, port, local_root=Path.cwd(), dry_run=args.dry_run
            )
        if args.cmd == "run":
            return commands.cmd_run(
                cfg,
                host,
                port,
                command=args.command,
                detach=args.detach,
                timeout=args.timeout,
                label=args.label,
            )
        if args.cmd == "logs":
            return commands.cmd_logs(
                cfg, host, port, name=args.name, follow=not args.no_follow
            )
        if args.cmd == "status":
            return commands.cmd_status(cfg, host, port, name=args.name)
        if args.cmd == "pull":
            return commands.cmd_pull(
                cfg, host, port, remote_path=args.remote_path, dest=Path(args.dest)
            )
    except KeyboardInterrupt:
        return 130
    except PodToolError as exc:
        logger.error("{}", exc)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
