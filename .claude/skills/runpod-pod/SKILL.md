---
name: runpod-pod
description: "Drive an already-running RunPod GPU pod from the Mac over direct-TCP SSH via the `pod` CLI: push the working tree, run/smoke-test code on the GPU, stream logs, check detached jobs, pull results. Use when asked to test/run/smoke-test code on the pod or on RunPod, run a GPU check, sync the repo to the pod, or pull results back from the pod."
allowed-tools: "Bash(uv run pod:*)"
---

# Driving a RunPod pod with `pod`

`uv run pod …` is a Mac-side CLI that shrinks the slow manual push→run→read→pull loop
against an **already-running** pod into one streamed command per step. It is **SSH-only over
direct-TCP** — it does not create, start, or stop pods (use `runpodctl`/the dashboard for
lifecycle). This skill is the **workflow + gotchas** layer; the authoritative design is
`docs/superpowers/specs/2026-06-11-runpod-pod-interaction-design.md` and the code lives in
`src/llm_fine_tuning/tools/runpod_pod/`.

## The loop

Always run in this order; each step assumes the previous one passed:

1. `uv run pod doctor` — preflight. Verifies the agent key, that the pod resolves to a
   direct-TCP host/port, that SSH + the key are authorized, and that `tmux`/`bash`/`rsync`/the
   repo dir exist on the pod. **If `doctor` fails, fix that first** — every other command will
   fail the same way. It prints a `[PASS]`/`[FAIL]` checklist with remediation.
2. `uv run pod sync [--dry-run]` — rsync the local working tree into the pod's repo dir
   (`/workspace/llm-fine-tuning`). Overlay semantics, honors `.gitignore`, excludes `.git`/
   `.venv`/`__pycache__`/`.local_data`, and **never** `--delete`s. Use `--dry-run` first when unsure.
3. `uv run pod run "<command>" [--detach] [--label NAME] [--timeout SECONDS]` — run a command
   in the repo dir on the pod.
   - **Foreground** (default): streams output live and propagates the remote exit code.
   - **`--detach`**: launches under `tmux`, returns immediately, and prints a **session handle**
     (the only thing on stdout) for later `logs`/`status`. Use this for anything long-running
     (real training, multi-minute smokes).
4. `uv run pod logs <session> [--no-follow]` / `uv run pod status <session>` — for detached
   jobs. `status` prints `RUNNING` / `EXITED <code>` / `GONE`. `logs` tails the job's log
   (`-f` by default; `--no-follow` dumps from the start).
5. `uv run pod pull <remote_path> [dest]` — rsync a result file/dir from the pod back to a
   local dest (default `.local_data/pod_runs`).

`--pod <id>` overrides the configured pod. It is a global flag, so it goes **before** the
subcommand: `uv run pod --pod <id> doctor` (not `pod doctor --pod <id>`).

## Gotchas (read before you trust a result)

- **Foreground Ctrl-C / `--timeout` kills the *local* ssh, not the remote GPU job.** The
  process on the pod keeps running and keeps holding the GPU. For anything you might need to
  interrupt, use `--detach`, monitor with `status`/`logs`, and stop it for real on the pod
  with `tmux kill-session -t <session>`. (Foreground is fine for short, self-completing smokes.)
- **Pods are ephemeral and their public IP/port churn on every restart.** The tool resolves
  the host/port fresh on every invocation (never cached). `RUNPOD_POD_ID` in `.env` goes stale
  when the pod is recreated — refresh it (`runpodctl pod list`) or pass `--pod <id>`. A handle
  from a previous pod will warn on `logs`/`status` (host mismatch) but still try.
- **A live pod's direct-TCP port can briefly *refuse* connections mid-session.** You may hit
  `ssh: connect to host <ip> port <port>: Connection refused` on a freshly-resolved port even
  though the pod is up and healthy — RunPod's TCP mapping churns and `runpodctl ssh info` can hand
  back a port that is momentarily not accepting. This is **not** a restart (the container hostname
  stays stable). **Just retry** — it self-recovers within a few attempts/seconds. Only treat it as
  real if every retry over ~a minute fails (then re-check the pod in the dashboard). For unattended
  loops, wrap `pod run` in a small retry.
- **Direct-TCP only.** A proxy-only pod (no public IP / TCP port 22 exposed) cannot be driven
  — `doctor`/`resolve` aborts with that diagnosis; recreate the pod with TCP port 22 + a public IP.
- **The agent SSH key must already be on the pod.** RunPod injects account-synced keys at pod
  *start*, so a key added to your account after a pod booted is not on it. `doctor` detects this
  (`Permission denied`) and prints the `runpodctl ssh add-key … && redeploy` remediation.
- **`sync`/`pull` need `rsync` installed *on the pod*** (both ends run it). Some base images
  (e.g. stock `runpod/pytorch`) ship without it; `doctor` now checks for it and, if missing,
  tells you to `apt-get update && apt-get install -y rsync` on the pod. `run`/`logs`/`status`
  don't need rsync (pure SSH exec) and work regardless.

## Security posture

`pod run` is **unrestricted shell exec over SSH** — whatever string you pass runs on the pod
as the pod user. The command is base64-wrapped in transit so it is never re-parsed by an
intermediate shell (no quoting/injection surprises), but that is transport safety, **not** a
sandbox. Treat `pod run` exactly like a remote root shell on a machine you control.

## Config

Read from a gitignored `.env` at the repo root (see committed `.env.example` for keys:
`RUNPOD_POD_ID`, optional `RUNPOD_POD_SSH_KEY`/`RUNPOD_POD_USER`/`RUNPOD_POD_REPO_DIR`, and a
manual `RUNPOD_POD_SSH_HOST`/`RUNPOD_POD_SSH_PORT` override). The API key is read from
`~/.runpod/config.toml` if not in the environment, and is only ever sent as an HTTP header —
never on a command line.
