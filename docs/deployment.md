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
| **Volume** | network volume at `/workspace` only if you want full persistence (see env vars) — optional |
| **Env vars** | see the **Environment variables** table below |

Also ensure your **SSH public key is on your RunPod account before the pod boots** (RunPod injects
account keys at start; the base image's `/start.sh` writes `$PUBLIC_KEY` to `authorized_keys`).

### Environment variables

Set these on the **template** (RunPod console → Environment Variables). All are optional unless noted;
the start command degrades gracefully when one is missing.

| Variable | Default / example | Purpose |
|---|---|---|
| `STUDIO_ADMIN_PASSWORD` | *(your consistent pw, ≥8)* | **Enables password automation** — step 6 sets the Studio admin login to this on every fresh pod (no manual browser Setup). Omit → set it by hand in the browser. |
| `REPO_URL` | `https://github.com/L1nusB/unsloth-studio-trial` | Helper repo cloned to `/workspace/<name>` (step 4); its scripts (password automation, env sync, API helper) ride along. Required for the password automation. |
| `UNSLOTH_STUDIO_HOME` | `/root/.unsloth/studio` (set `/workspace/.studio` to persist) | Where Studio's venv + `auth/` + `studio.db` + llama.cpp build live. Point at `/workspace/.studio` **with a network volume** to persist across restarts (set once, fast reboots). Harmless without a volume. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | — | Git identity for commits made on the pod (optional). |
| `GITHUB_PAT` | — | PAT injected into the clone URL for a **private** `REPO_URL` (public repo needs none). |
| `HF_TOKEN` | — | Hugging Face token for gated models/datasets. |
| `HF_HOME`, `UV_CACHE_DIR` | e.g. `/workspace/.cache/hf`, `/workspace/.uv_cache` | Cache locations — put under `/workspace` (with a volume) to persist model/uv caches. |
| `HF_XET_HIGH_PERFORMANCE`, `OPENROUTER_API_KEY` | — | Optional (HF Xet perf; OpenRouter, mirrored from the fine-tuning template). |
| `STUDIO_API_KEY` | — | Optional alternative credential for `scripts/studio_api.py` (else it logs in with `STUDIO_ADMIN_PASSWORD`). |
| `JUPYTER_PASSWORD` | — | Optional token for the JupyterLab on 8888 (from the base image's `/start.sh`). |
| `PUBLIC_KEY` | *(injected by RunPod)* | Your account SSH key; the base `/start.sh` authorizes it. Not set by hand. |

### Container Start Command

Paste this as the pod's **Container Start Command** (replaces the repo-cloning one you used before;
the only base-image dependency is `/start.sh`, which brings up sshd+`PUBLIC_KEY`+Jupyter):

```bash
bash -lc 'set -uo pipefail
export DEBIAN_FRONTEND=noninteractive PIP_ROOT_USER_ACTION=ignore
# Studio home. Default = Studio own dir (container disk). Set UNSLOTH_STUDIO_HOME=/workspace/.studio
# (+ a network volume) to PERSIST install/auth/db/llama.cpp across restarts. Harmless without a volume.
export UNSLOTH_STUDIO_HOME="${UNSLOTH_STUDIO_HOME:-/root/.unsloth/studio}"

# 1. Base services (sshd w/ PUBLIC_KEY injection + JupyterLab) in the background — SSH reachable
#    immediately while Studio installs.
/start.sh &

# 2. System deps for Unsloth Studio (llama.cpp/GGUF build) + the pod CLI (rsync/tmux) + uv.
apt-get update && apt-get install -y --no-install-recommends \
  git ninja-build rsync nano tmux cmake tree build-essential libcurl4-openssl-dev libssl-dev curl && \
  rm -rf /var/lib/apt/lists/*
command -v uv >/dev/null || python -m pip install -qU uv

# 3. Git identity (optional).
[ -n "${GIT_USER_NAME:-}" ]  && git config --global user.name  "$GIT_USER_NAME"  || true
[ -n "${GIT_USER_EMAIL:-}" ] && git config --global user.email "$GIT_USER_EMAIL" || true

# 4. Clone/update the helper repo from $REPO_URL (PAT-injected if GITHUB_PAT set; public repo works
#    without). $REPO is derived from the URL basename so the rest of the script is repo-name-agnostic.
REPO=""
if [ -n "${REPO_URL:-}" ]; then
  REPO="/workspace/$(basename "${REPO_URL%.git}")"
  AUTH_URL="$REPO_URL"
  if [ -n "${GITHUB_PAT:-}" ] && [[ "$AUTH_URL" == https://* ]]; then AUTH_URL="https://$GITHUB_PAT@${AUTH_URL#https://}"; fi
  if [ -d "$REPO/.git" ]; then git -C "$REPO" remote set-url origin "$AUTH_URL"; git -C "$REPO" pull --ff-only || true;
  else git clone "$AUTH_URL" "$REPO" || true; fi
fi

# 5. Install Unsloth + Studio. Creates its OWN uv venv under $UNSLOTH_STUDIO_HOME (own Python 3.13 +
#    own cu-matched torch; base image torch ignored, no conflict). Do NOT set UNSLOTH_NO_TORCH=1
#    (GGUF-only, disables training).
curl -fsSL https://unsloth.ai/install.sh | sh

# 6. Auto-set the admin password from $STUDIO_ADMIN_PASSWORD once Studio is up (background;
#    idempotent; no-op if the var is unset, already configured, or the repo was not cloned). Replays
#    the browser "Setup Account" flow over the REST API → consistent password on every fresh pod.
[ -n "$REPO" ] && [ -f "$REPO/scripts/studio_set_password.py" ] && \
  ( python3 "$REPO/scripts/studio_set_password.py" || true ) &

# 7. Launch the Studio web UI in the foreground (keeps the container alive), bound to all interfaces
#    on 8000. The installer venv is named "unsloth_studio" (NOT ".venv") and the PATH shim is not
#    reliably present in this non-interactive shell — so discover the binary rather than hardcode it
#    (verified live 2026-06-29: hardcoding .venv/bin/unsloth fails the exec and kills the container).
UNSLOTH_BIN="$(command -v unsloth || find "$UNSLOTH_STUDIO_HOME" /root/.unsloth -type f -name unsloth -path "*/bin/*" 2>/dev/null | head -1)"
exec "$UNSLOTH_BIN" studio -H 0.0.0.0 -p 8000'
```

Notes:
- `set -uo pipefail` (no `-e`): network-ish steps (`git`, the password script) are guarded with
  `|| true` so a transient failure never kills the boot before Studio launches.
- The installer **does not auto-start** in a non-tty — that's why step 7 launches explicitly. It
  auto-detects the GPU and pulls a CUDA-matched torch into its venv.
- First boot is slow (apt + uv venv + torch download + llama.cpp build = several minutes). Watch over
  SSH (`pod doctor` once SSH is up; processes under `$UNSLOTH_STUDIO_HOME`).
- The password automation (step 6) requires `REPO_URL` set **and** the repo pushed to GitHub. Omit
  `STUDIO_ADMIN_PASSWORD` to set the password by hand in the browser instead.

### Password automation & persistence (reverse-engineered + confirmed live 2026-06-29)

**How Studio auth bootstraps (from the backend source):** on a fresh install Studio creates user
**`unsloth`** with `must_change_password=True`, generates a random 4-word bootstrap passphrase, and
writes it to **`$UNSLOTH_STUDIO_HOME/auth/.bootstrap_password`** (deleted on the first password
change). The browser **Setup Account** page replays: login as `unsloth` with that bootstrap pw →
`change-password`. There is a full REST API (`/api/auth/login`, `/api/auth/change-password`, bearer
tokens). The official-image env var `UNSLOTH_ADMIN_PASSWORD` is **NOT** honored on our CLI path.

**Two ways to avoid re-doing Setup on every fresh pod:**

1. **Password automation (works even with full teardowns — recommended for the spotty workflow).**
   Set the template env var **`STUDIO_ADMIN_PASSWORD=<your consistent pw>`**. Step 4b runs
   `scripts/studio_set_password.py`, which (once Studio is up) reads the bootstrap file and calls
   login → change-password over the API to set your password. Idempotent: no-op if already set or if
   the var is unset. Result: **a consistent password on every fresh pod, zero manual step.** The
   password lives only as a RunPod template env var; **do not** put it in `.env` (nothing reads it there).
2. **Full persistence (only if you keep a network volume).** Set `UNSLOTH_STUDIO_HOME=/workspace/.studio`
   + attach a volume at `/workspace`. Then `auth/`, `studio.db`, the venv and the llama.cpp build all
   survive restarts → Setup once ever **and** fast reboots (install.sh reuses the venv). Does not help
   a true teardown that deletes the volume. (`UNSLOTH_STUDIO_HOME` must be present at both install and
   launch — step 0 exports it.)

The two compose: with both, the script is a no-op after the first boot and the volume keeps everything.

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
