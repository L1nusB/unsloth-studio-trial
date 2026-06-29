---
name: unsloth-studio
description: >-
  Deploy, reach, and automate Unsloth Studio (the no-code fine-tuning web UI) on RunPod — from
  bringing up the pod, to logging in, to driving its REST API (start/monitor/stop training runs,
  manage datasets/models, export GGUF). Use this whenever the user mentions Unsloth Studio, the
  "studio pod", `studio_api.py` / `studio_set_password.py`, fine-tuning via the Studio UI or API
  (rather than the code pipeline), or hits a Studio-on-RunPod problem (SSH dropping under training
  load, proxy 403s, the admin-password/Setup prompt on every fresh pod, dataset-mapping limits).
  Reach for it even when "Unsloth Studio" isn't named but the task is clearly about driving that
  web UI / its API on a remote GPU.
---

# Automating Unsloth Studio on RunPod

Unsloth Studio is a **BETA no-code fine-tuning GUI**: a Python **FastAPI** backend (uvicorn, JWT
auth, WebSockets, llama.cpp for GGUF) serving a prebuilt **React** SPA. It is **not** Gradio/
Streamlit/Jupyter. You fine-tune by clicking through a UI **or** by calling its 201-endpoint REST
API. This skill is the workflow + hard-won gotchas for running it on a RunPod GPU and driving it
from a Mac. It lives in the `unsloth-studio-trial` repo alongside the scripts and docs it points to.

**Orientation (read as needed):**
- `../../docs/deployment.md` — the RunPod template / container start command (authoritative recipe).
- `../../docs/studio-api.md` — REST API reference (auth, endpoint catalog, `train/start` schema).
- `../../docs/studio-openapi.json` — the full OpenAPI spec (312 KB, 201 paths).
- `../../docs/runpod-access.md` — proxy URL vs SSH `-L` tunnel; how the password got into `.env`.
- `../../docs/dataset-formatting.md` — what Studio's dataset mapping / Data Recipes can and can't do.
- `../../scripts/` — `studio_api.py`, `studio_set_password.py`, `sync_pod_env.sh` (executable helpers).

## The mental model (internalize this — it explains every gotcha)

Studio is a local web app. On RunPod it runs on the pod and you reach it two ways, which behave
**very differently**, and choosing the right one is most of the battle:

| Channel | Reliability | Auth | Use it for |
|---|---|---|---|
| **HTTP proxy** `https://<POD_ID>-8000.proxy.runpod.net` | **Solid, incl. during training** | login → bearer token | the browser UI, and all scripted API calls |
| **direct-TCP SSH** (`pod` CLI → `curl localhost:8000`) | flaky — **sshd starves under training CPU load** (drops for minutes) and the TCP port churns | reads pod env | install-time setup, reading files, when the GPU is idle |

**→ Default to the proxy for anything you need to be reliable.** The single biggest mistake is
trying to monitor a run over SSH: during training the dataloader pegs the CPU, sshd stops
answering, and you'll think the pod died. It didn't — switch to the proxy.

## Deploying a pod (summary; full recipe in `docs/deployment.md`)

Use our own pod, **not** the `runpod-workers/unsloth-base` image (it isn't reliably pullable and
has no SSH-key injection, which breaks the `pod` CLI). Recipe:
- **Image:** `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04` (ships sshd + handles
  `PUBLIC_KEY`, so SSH works) — Studio's installer brings its own Python/torch in a separate venv.
- **Ports:** HTTP `8000` (Studio) + `8888` (Jupyter, optional); **TCP `22` + public IP** (for SSH).
- **Env vars:** `STUDIO_ADMIN_PASSWORD` (password automation), `REPO_URL` (clone the helper repo),
  optional `REPO_BRANCH`, `UNSLOTH_STUDIO_HOME`, `HF_TOKEN` — see the env table in `deployment.md`.
- **Start command:** background `/start.sh`, apt the build deps, clone `$REPO_URL`, run Studio's
  `install.sh`, run `studio_set_password.py` in the background, then launch Studio on `0.0.0.0:8000`.
  Paste it verbatim from `deployment.md` — the launch line **discovers** the `unsloth` binary
  (the installer venv is `unsloth_studio`, not `.venv`, and there's no PATH shim).

First boot is **slow** (torch + llama.cpp build + multi-env provisioning = tens of minutes). It is
also slow because RunPod GPUs are often network/CPU-throttled — that's normal, just wait.

## Authentication & the password (why it's fiddly)

On a fresh install Studio creates user **`unsloth`** with `must_change_password=True`, writes a
random bootstrap passphrase to `$UNSLOTH_STUDIO_HOME/auth/.bootstrap_password` (deleted on first
change), and shows the browser **Setup Account** page. **No env var seeds the admin password on the
CLI path** (`UNSLOTH_ADMIN_PASSWORD` is an official-image-only feature). So:

- **To automate it:** set `STUDIO_ADMIN_PASSWORD` on the template; the start command runs
  `scripts/studio_set_password.py`, which reads the bootstrap file and calls login → change-password
  over the API. Idempotent. Result: a consistent password on every fresh, fully-torn-down pod.
- **API keys** (`POST /api/auth/api-keys`) exist but are **per-pod-ephemeral** (wiped on teardown) —
  the password is the durable credential.
- The auth DB (`auth.db`) and the whole install persist across **restarts** only if
  `UNSLOTH_STUDIO_HOME=/workspace/.studio` **and** a network volume is attached; a true teardown
  wipes it, which is why the password automation matters for spotty/no-volume workflows.

## Interacting from the Mac — `scripts/studio_api.py` (the reliable path)

This is the workhorse. Run it from the repo root; it auto-loads `.env` (`RUNPOD_POD_ID` +
`STUDIO_ADMIN_PASSWORD`), targets the **proxy** when `RUNPOD_POD_ID` is set (else `localhost` on the
pod), logs in for a bearer token, and prints only the JSON response. The password stays in `.env` —
never on the command line or in chat.

```bash
uv run python scripts/studio_api.py GET  /api/train/status
uv run python scripts/studio_api.py GET  /api/train/runs
uv run python scripts/studio_api.py GET  /api/auth/status            # unauth, sanity check
uv run python scripts/studio_api.py POST /api/train/start @payload.json
uv run python scripts/studio_api.py POST /api/train/stop
```

**Two non-obvious requirements it bakes in** (don't hand-roll a client without them):
1. **Browser-like `User-Agent`.** RunPod's proxy (Cloudflare) returns **403** to the default
   `Python-urllib` UA as bot traffic. Symptom that wastes an hour: 403 on a *correct* password but
   401 on a wrong one — it's the UA filter, not the password.
2. **Same-origin `Origin`/`Referer`** headers (future-proof against Studio's CSRF checks).

Getting the password into `.env` without it touching the conversation: have the pod write
`$STUDIO_ADMIN_PASSWORD` to a file, `pod pull` it (rsync never prints content), and append it to
`.env` via a redirect (`{ printf 'STUDIO_ADMIN_PASSWORD='; cat pulled; echo; } >> .env`). Never
`echo`/`pod run "echo $PW"` — that surfaces it. `.env` is gitignored.

## Starting & varying a run via the API

`POST /api/train/start` takes `TrainingStartRequest`. Required: `model_name`, `training_type`
(`"LoRA/QLoRA"` | `"Full Finetuning"` | `"Continued Pretraining"`), `format_type`
(`auto`/`alpaca`/`chatml`/`sharegpt`). Then ~60 optional levers (QLoRA `load_in_4bit`, all
hyperparams, LoRA config, `custom_format_mapping`, eval, wandb/tensorboard) — full list in
`docs/studio-api.md`. `learning_rate` is a **string**. Only one run at a time
(`train/status.is_training_running` must be false).

**The easy, reliable way to launch — clone a prior run and vary it.** Hand-building the mapping is
error-prone; instead pull a completed run's exact config and override the levers you want:

```bash
# 1. fetch a prior run's full config (includes custom_format_mapping)
uv run python scripts/studio_api.py GET /api/train/runs/<RUN_ID> > prev.json
# 2. filter to TrainingStartRequest props + override levers (e.g. max_steps, lora_r) -> payload.json
#    (a few lines of python: json.load prev["config"], keep allowed keys, payload.update({...}))
# 3. launch
uv run python scripts/studio_api.py POST /api/train/start @payload.json
# 4. monitor via the proxy (works under load)
uv run python scripts/studio_api.py GET /api/train/status
```

Output lands under `/workspace/.studio/outputs/<model>_<ts>/` (adapter + `checkpoint-*` per
`save_steps`). `GET /api/train/runs/{id}` returns `{run, config, metrics}`.

## What Studio is good and bad at (set expectations honestly)

- **Good:** quick no-code QLoRA/LoRA SFT, a YAML-config-driven hyperparam/LoRA surface (downloadable
  per run — `training:` + `lora:`), live progress, GGUF export, llama.cpp chat, full REST automation.
- **Limited — dataset formatting.** The training Dataset step is single-column-per-role, **no system
  prompt**, fixed formats; `ai-assist-mapping` only *suggests* a mapping. **Data Recipes is a
  separate NeMo-DataDesigner synthetic-data generator, not a prompt formatter.** For precise
  per-experiment templating, the code pipeline wins. **Workaround:** pre-format outside Studio (bake
  the template into a `text`/`messages` column, push to HF), then map trivially. See
  `dataset-formatting.md`.

## Pitfalls checklist (each cost real time to find)

- **SSH unreachable during training** → not a crash; use the proxy. CPU contention starves sshd.
- **Transient `Connection refused` on a resolved SSH port** → RunPod TCP-port churn; just retry (see
  the `runpod-pod` skill). Wrap unattended pod loops in a retry.
- **Proxy 403 from a script** → set a browser `User-Agent` (Cloudflare blocks `Python-urllib`).
- **Setup page on every fresh pod** → expected without a volume; use the password automation. And it
  only fires if the **default branch** the start command clones actually contains
  `studio_set_password.py` — keep `main` (or `$REPO_BRANCH`) up to date, or the clone silently skips it.
- **Launch line `exec`s a missing binary → container dies** → the installer venv is `unsloth_studio`,
  not `.venv`; discover the binary (the recipe already does).
- **Don't** set `UNSLOTH_NO_TORCH=1` (GGUF-only, disables training).

## Quick start for a fresh session

1. `runpodctl get pod` → put `RUNPOD_POD_ID` in `.env`; confirm SSH with `uv run pod doctor`.
2. `uv run python scripts/studio_api.py GET /api/auth/status` (via proxy) → confirms Studio reachable.
3. Browse `https://<POD_ID>-8000.proxy.runpod.net` to use the UI, or drive the API per above.
