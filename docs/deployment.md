# Deploying Unsloth Studio on a RunPod pod

Two paths. **Path B (our own pod from the `runpod/pytorch` base + a start command) is recommended**
for the first test — full control, guaranteed SSH/`pod`-CLI access, no dependency on an unverified
image. Path A (official image) is documented but has real caveats.

---

## Why not the "official" image (Path A)

The GitHub repo `runpod-workers/unsloth-base` builds an image **intended** to be published as
`runpod/unsloth-studio:latest` (`:cu128` / `:cu130`). It is a real, sensible image (base
`runpod/pytorch:1.0.3-cu1281-torch260-ubuntu2404`, EXPOSE `8000 8888 22`, `start.sh` runs the
Studio backend on `0.0.0.0:8000`, requires `UNSLOTH_ADMIN_PASSWORD`). **But:**

1. **Not confirmed publicly pullable** — Docker Hub `runpod/unsloth-studio` returns 404/401 as of
   2026-06-29. It may be private/unpublished or RunPod-template-internal. Don't paste it into
   *Container Image* until you've confirmed it pulls.
2. **No SSH key injection** — its `start.sh` has no `PUBLIC_KEY`/`authorized_keys` handling and
   starts sshd only conditionally. Our `pod` CLI and the SSH `-L` tunnel (the robust access path)
   would likely **not work out of the box**.
3. **No `rsync`/`tmux`/`cmake`** installed by the image itself (only Node.js) — depends on the base.

If you *do* try Path A later: Container Image = the confirmed image ref; Expose HTTP `8000,8888`;
Expose TCP `22`; env `UNSLOTH_ADMIN_PASSWORD` (required), optional `JUPYTER_PASSWORD`,
`EXIT_ON_CRASH=true` (keeps the container alive if Studio crashes, for debugging). Reaching it over
SSH would need adding key injection + sshd via an override.

---

## Path B — our own pod (recommended)

Reuses the base image we already know, which **does** inject `PUBLIC_KEY` and run sshd (so
`pod doctor` / SSH `-L` / Jupyter all work), and installs Studio in the start command. No Docker
build, no registry.

### Template / pod settings

| Field | Value |
|---|---|
| **Container Image** | `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04` |
| **Expose HTTP Ports** | `8000` (Studio), `8888` (JupyterLab, optional) |
| **Expose TCP Ports** | `22` (SSH — required for `pod` CLI + `-L` tunnel) |
| **Public IP** | yes (required for direct-TCP SSH) |
| **GPU** | one A40 / L40 / 4090-class is plenty for a smoke test |
| **Volume** | network volume at `/workspace` recommended (checkpoints persist across restarts) |
| **Env vars** | `UNSLOTH_ADMIN_PASSWORD=<choose>` (see auth note below); ensure your **SSH public key is on your RunPod account before the pod boots** |

### Container Start Command

Paste this as the pod's **Container Start Command** (replaces the repo-cloning one you used before;
the only base-image dependency is `/start.sh`, which brings up sshd+`PUBLIC_KEY`+Jupyter):

```bash
bash -lc 'set -euo pipefail
export DEBIAN_FRONTEND=noninteractive PIP_ROOT_USER_ACTION=ignore

# 1. Bring up base services (sshd w/ PUBLIC_KEY injection + JupyterLab) in the background,
#    so SSH is reachable immediately while Studio installs.
/start.sh &

# 2. Build deps for Unsloth Studio (llama.cpp/GGUF) + tools for the pod CLI.
#    The devel base already has git/gcc/curl; cmake + libcurl headers are the usual gaps.
apt-get update && apt-get install -y --no-install-recommends \
  cmake build-essential libcurl4-openssl-dev git curl rsync tmux nano && \
  rm -rf /var/lib/apt/lists/*

# 3. Install Unsloth + Studio. Creates its OWN uv venv at /root/.unsloth/studio/.venv
#    (own Python 3.13 + own cu-matched torch); the base image torch is ignored, no conflict.
#    Do NOT set UNSLOTH_NO_TORCH=1 — that is GGUF-only and disables training.
curl -fsSL https://unsloth.ai/install.sh | sh

# 4. Launch the Studio web UI in the foreground (keeps the container alive), bound to all
#    interfaces on 8000 so the RunPod proxy / SSH tunnel can reach it.
exec /root/.unsloth/studio/.venv/bin/unsloth studio -H 0.0.0.0 -p 8000'
```

Notes:
- The installer **does not auto-start** in a non-tty (it just prints instructions) — that's why we
  launch explicitly in step 4. It auto-detects the GPU and pulls a CUDA-matched torch into its venv.
- First boot is slow (apt + uv venv + torch download + llama.cpp build = several minutes). Watch
  progress over SSH: `tail -f` the container log, or just `pod doctor` once SSH is up.
- `nano`/`tmux`/`rsync` are there so the `pod` CLI's `sync`/`pull` and interactive debugging work.

### First-run auth (verify live)
The official *image* reads `UNSLOTH_ADMIN_PASSWORD` to bootstrap the admin account. The **CLI
install** path uses Studio's built-in JWT auth with a **first-run setup flow** (a setup token /
`/change-password` step). Whether the bare CLI also honors `UNSLOTH_ADMIN_PASSWORD` is **unconfirmed**
— set it anyway (harmless), and on first launch **check the Studio logs for a setup token / signup
URL**. We'll pin this down during the Phase-2 smoke test and record the exact flow here.

### After the pod is up
1. `RUNPOD_POD_ID=<id>` in this repo's `.env` (and/or the `llm-fine-tuning` `.env` to use `pod`).
2. From `../llm-fine-tuning`: `uv run pod doctor` → confirms SSH + resolves the direct-TCP host/port.
3. Open `https://<POD_ID>-8000.proxy.runpod.net` (or set up the SSH `-L` tunnel — see
   `runpod-access.md`) and complete first-run auth.
4. Run a tiny LoRA fine-tune in the UI; confirm live progress + a checkpoint under `/workspace`.

---

## Path B2 — bake our own Dockerfile (later, only if we keep Studio)
If the trial sticks, fold steps 2–3 into a Dockerfile `FROM runpod/pytorch:2.8.0-…`, pre-install
Studio + deps, push to a registry, and use that image so pods boot in seconds instead of rebuilding
each time. Not worth it for a one-off test.
