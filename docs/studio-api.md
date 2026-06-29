# Driving Unsloth Studio via its REST API

Studio's backend is **FastAPI** — there's a full REST API (**201 endpoints**), self-documented:
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Swagger UI: `http://localhost:8000/docs`

So Studio can be driven **programmatically**, not just through the web UI — including training,
dataset config, model management, and inference.

## How to reach it
- **From the pod (best — no proxy limits):** `uv run pod run "curl -s http://localhost:8000/api/..."`.
- **From the Mac:** the proxy URL `https://<POD_ID>-8000.proxy.runpod.net/api/...` (subject to the
  100 s Cloudflare timeout / ~30 MB cap), or over an SSH `-L` tunnel.

## Auth (bearer tokens; reverse-engineered 2026-06-29)
- Admin user is **`unsloth`**. `GET /api/auth/status` → `{initialized, default_username, requires_password_change}`.
- `POST /api/auth/login {username,password}` → `Token{access_token, refresh_token, token_type:"bearer", must_change_password}`.
- Protected routes need `Authorization: Bearer <access_token>` (scheme `HTTPBearer`). Refresh via `POST /api/auth/refresh`.
- `POST /api/auth/change-password {current_password,new_password}` (used by first-run Setup; see `deployment.md`).
- **API keys:** `POST /api/auth/api-keys {name, expires_in_days}` mints a long-lived key (table has
  `is_internal`/`is_active`). Cleaner than a password for automation — and you can hand an agent a key
  without sharing your password.
- `POST /api/auth/desktop-login {secret}` — local desktop secret path (secret lives in the `app_secrets`
  table in `auth.db`); not needed if you use a password or API key.

### To let me (the agent) drive Studio — no secret ever passed
Because `STUDIO_ADMIN_PASSWORD` is a RunPod template env var, it's in the pod's environment, and the
agent is root on the pod over SSH. So the credential is **read from the pod at runtime** — never
pasted into chat, the repo, or `.env`. Use **`scripts/studio_api.py`** (reads `STUDIO_API_KEY` or
`STUDIO_ADMIN_PASSWORD` from the env, authenticates, prints only the response):

```bash
# on the pod (driven via `uv run pod run`):
python3 /workspace/unsloth-studio-trial/scripts/studio_api.py GET /api/train/status
python3 /workspace/unsloth-studio-trial/scripts/studio_api.py POST /api/train/start @cfg.json
```

**API keys are per-pod-ephemeral** (stored in `auth.db`, which is wiped on teardown unless you keep a
`/workspace` volume). So a "shared persistent API key" does **not** survive fresh pods — the durable
shared credential is the **password** (`STUDIO_ADMIN_PASSWORD`). Mint a throwaway key per pod via
`POST /api/auth/api-keys` only if you want one for a single session.

Read-only/unauth endpoints (`/api/health`, `/api/auth/status`, `/openapi.json`) need no credential.

## Key endpoint groups
- **Training:** `POST /api/train/start`, `POST /api/train/stop`, `GET /api/train/status|progress|metrics`,
  `GET/PATCH/DELETE /api/train/runs[/{id}]`, `GET /api/train/hardware`.
- **Datasets (training step):** `POST /api/datasets/ai-assist-mapping`, `/check-format`, `/upload`,
  `GET /api/datasets/local`; HF-hub variants under `/api/hub/datasets/*` (download/cached/mapping).
- **Data Recipes** (separate synthetic-data graph tool — NeMo DataDesigner): `/api/data-recipe/*`
  (`seed/inspect`, `validate`, `jobs/{id}/status|events|dataset|publish`). See `dataset-formatting.md`.
- **Models:** `/api/models/*` (cached, checkpoints, config, export-size, gguf), `/api/hub/*`, `/api/inference/*`.

## Reproducibility note
The **training** side saves/loads a **YAML config** (model + dataset + hyperparams) — the version-
controllable artifact in Studio. It carries the (limited) dataset mapping, not rich prompt templates.
