# Unsloth Studio on RunPod — feasibility + roadmap

**Status:** research complete (2026-06-29). No pod live yet. Deployment recipe being finalized
(verifying the official image). Goal: get Unsloth Studio running on a RunPod GPU and reachable
from the Mac browser, as a quick comparison/sandbox alongside our code-based pipeline.

**Verdict: feasible and low-effort.** Studio is a normal FastAPI+React web app, an official
RunPod image source exists, and our existing SSH/`pod` workflow covers the robust-access path.

---

## What Unsloth Studio actually is
- **No-code fine-tuning GUI**, currently **BETA**. Client/server, all local to the GPU box.
- **Backend:** Python **FastAPI** (uvicorn) — REST + **WebSocket** (live training progress &
  streaming chat). **JWT auth** built in (admin password on first run). llama.cpp for GGUF chat.
- **Frontend:** prebuilt **React** SPA served as static files by the same FastAPI server.
- **Not Gradio / Streamlit / Jupyter-based.** CLI: `unsloth studio` (lives in
  `github.com/unslothai/unsloth` under `studio/`).

### The one critical gotcha — network binding
- Bare CLI default bind: **`127.0.0.1:8888`** → unreachable through any remote proxy/tunnel.
  **Must launch with `-H 0.0.0.0`.**
- Flags: `-H/--host`, `-p/--port`, `--secure` (spawns a Cloudflare `trycloudflare.com` tunnel,
  fails closed). Auth: JWT; first run sets an admin password.
- **Port numbers:** bare CLI defaults to **8888**; the official image/template serves Studio on
  **8000** (Jupyter on 8888). Pick one and be explicit.

### GPU / CUDA
- NVIDIA RTX 30/40/50 / Blackwell / DGX. **CUDA 12.4+ (12.8+ Blackwell)**, Python **3.11–3.13**.
  Matches our **cu128** stack. No hard VRAM floor (T4 16 GB ≈ up to ~22B). `UNSLOTH_NO_TORCH=1`
  reuses the pod's torch instead of reinstalling.

---

## RunPod access mechanics (detail in `runpod-access.md`)

| Mechanism | How | Best for |
|---|---|---|
| **HTTP proxy** | `https://<POD_ID>-8000.proxy.runpod.net` (declare 8000 under *Expose HTTP Ports*) | quick look; zero client setup |
| **SSH `-L` tunnel** | `ssh -L 8000:localhost:8000 …` over direct-TCP | robust long sessions — **no 100 s proxy timeout / ~30 MB upload cap** |
| **TCP expose** | raw `IP:<random_port>` | any protocol, no TLS; port changes each reset |
| **`--secure`** | Studio prints a `*.trycloudflare.com` URL | no RunPod port config at all |

**Watch-out:** RunPod's HTTP proxy is Cloudflare-fronted → **100 s timeout (524)** on no-data
requests + ~30 MB body cap. Fine for browsing the UI and normal streaming; for big dataset
uploads / very long single requests, prefer the **SSH `-L` tunnel**.

---

## Deployment options (ranked) — see `deployment.md` for exact steps
- **A. Official image `runpod-workers/unsloth-base`** — paste its published image into RunPod's
  *Container Image* field, set ports + `UNSLOTH_ADMIN_PASSWORD`. Fastest. *(Confirming the
  pullable image ref + whether it ships sshd/rsync — see deployment.md.)*
- **B. Our PyTorch base + custom start command** — `runpod/pytorch:2.8.0-…cuda12.8.1…` (the image
  we already use; ships sshd+Jupyter so the `pod` CLI works), with a start command that installs
  deps (tmux/rsync/cmake), `curl … install.sh | sh`, then `unsloth studio -H 0.0.0.0 -p 8000`.
  Full control; reuses everything we know.
- **C. `unsloth/unsloth` Docker image, manual** — what A wraps; only if we want it without the
  RunPod wrapper.
- **Not yet:** `unsloth deploy run --provider runpod` (one-command provisioner) is an **unmerged
  PR** (`unslothai/unsloth#5961`). Watch it.

---

## Roadmap — to a working test

- **Phase 0 — Decide path** *(in progress)*: A vs B, finalize `deployment.md`.
- **Phase 1 — Spin up the pod** *(researcher)*: create from the chosen recipe with **TCP 22 +
  public IP** (required for SSH/`pod` CLI), **HTTP 8000** exposed, `UNSLOTH_ADMIN_PASSWORD` set,
  one GPU (A40/L40/4090-class is plenty). Put `RUNPOD_POD_ID` in `.env`.
- **Phase 2 — Reach the UI**: open `https://<POD_ID>-8000.proxy.runpod.net`, log in. If the proxy
  is flaky, fall back to the SSH `-L` tunnel and `http://localhost:8000`.
- **Phase 3 — Smoke fine-tune**: tiny model + tiny dataset, a few steps; confirm live loss/progress
  streams and a checkpoint lands under `/workspace`. That's the "does it run" bar.
- **Phase 4 — Notes & decision**: record what the GUI exposes vs our YAML/CLI pipeline (LoRA knobs,
  dataset formats, export/GGUF, eval), proxy-vs-tunnel reliability, keep-or-drop. Persist gotchas.

## Decisions locked
- **Path:** start with official image (A); our base (B) as controlled fallback — prep both.
- **Home:** this dedicated sibling repo (`unsloth-studio-trial/`), notes + recipes only.
