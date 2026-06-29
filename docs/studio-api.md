# Driving Unsloth Studio via its REST API

Studio's backend is **FastAPI** — a full REST API (**201 endpoints**), self-documented:
- OpenAPI JSON: `http://localhost:8000/openapi.json` — **full spec saved here: [`studio-openapi.json`](studio-openapi.json)**
- Swagger UI: `http://localhost:8000/docs`

So Studio can be driven **programmatically** (training, datasets, models, inference, export), not just via the UI.

## Reliable access — use the proxy + `scripts/studio_api.py`

There are two network paths to the pod; pick by reliability:

| Path | Works under training load? | Auth |
|---|---|---|
| **HTTP proxy** `https://<POD_ID>-8000.proxy.runpod.net` | ✅ yes (unaffected by CPU contention) | bearer token via login |
| direct-TCP SSH (`pod` CLI → `curl localhost:8000`) | ❌ **sshd starves under training CPU load** — drops for minutes | reads pod env |

**→ Prefer the proxy.** During an active run, SSH/`pod` go unreachable (the GPU box's CPU is pegged by
the dataloader/tokenization), but the proxy keeps working — that's how the browser UI stays live.

**`scripts/studio_api.py`** handles all of this. Run it from the Mac repo root:
```bash
uv run python scripts/studio_api.py GET  /api/train/status
uv run python scripts/studio_api.py GET  /api/train/runs
uv run python scripts/studio_api.py POST /api/train/start @payload.json
```
It: auto-loads `.env` (for `RUNPOD_POD_ID` + `STUDIO_ADMIN_PASSWORD`), targets the **proxy URL** when
`RUNPOD_POD_ID` is set (else `localhost:8000` when run on the pod), logs in for a bearer token, and
prints **only** the JSON response (status to stderr). The password is read from `.env` — never on the
CLI or in chat. See [`runpod-access.md`](runpod-access.md) for how the password got into `.env`.

### Two proxy gotchas baked into the helper (don't hand-roll a client without them)
1. **User-Agent:** RunPod's proxy (Cloudflare) returns **403** to the default `Python-urllib` UA (bot
   filter). A **browser-like `User-Agent`** is required for every proxied call. (Symptom: 403 on a
   *correct* password, 401 on a wrong one — because the UA block sits in front of the cred check… no,
   the cred check is reached but a valid login then trips the filter; either way, set the UA.)
2. **Origin/Referer:** the helper also sends `Origin`/`Referer` = the base URL (same-origin, like the
   browser) — harmless and future-proof against Studio-side CSRF checks.

## Auth model (reverse-engineered)
- Admin user is **`unsloth`**. `GET /api/auth/status` → `{initialized, default_username, requires_password_change}`.
- `POST /api/auth/login {username,password}` → `Token{access_token, refresh_token, token_type:"bearer", must_change_password}`.
  Login is **per-account + per-IP rate-limited** (429 after repeated failures) — don't brute-force.
- Protected routes need `Authorization: Bearer <access_token>`. Refresh via `POST /api/auth/refresh`.
- **API keys:** `POST /api/auth/api-keys {name, expires_in_days}` → a key. **Per-pod-ephemeral** (stored
  in `auth.db`, wiped on teardown unless `/workspace` is a persistent volume) — the **password is the
  durable credential**; mint a key only for a single session if you want one.
- The admin password is **not** seeded by any env var on the CLI path — it's set by the browser Setup
  flow or our `scripts/studio_set_password.py`. See [`deployment.md`](deployment.md).

## Endpoint catalog (by tag — full detail in `studio-openapi.json`)
- **auth** (10): login, change-password, refresh, logout, status, identity, api-keys (CRUD), desktop-login.
- **train** (12): `POST /start`, `POST /stop`, `POST /reset`, `GET /status|progress|metrics|hardware`,
  `GET /runs`, `GET|PATCH|DELETE /runs/{id}`.
- **models** (24): cached/finetuned/gguf listings, `config/{name}`, browse-folders, download-progress, delete.
- **hub** (30): HF model/dataset search + download + cache management (`/datasets/*`, `/models-folder`, …).
- **datasets** (5): `ai-assist-mapping`, `check-format`, `upload`, `local`, `download-progress`.
- **data-recipe** (15): NeMo-DataDesigner synthetic-data jobs (seed/validate/jobs/{id}/…). See [`dataset-formatting.md`](dataset-formatting.md).
- **inference** (23) / **chat** (24): llama.cpp serving + chat threads/projects.
- **export** (10): GGUF/merged export jobs (`/export/base`, `/status`, `/logs/stream`, `/cancel`).
- **mcp** (7), **llama** (2), **untagged** (10: `/api/health`, `/api/system`, `/api/studio/*`).

## Starting a run — `POST /api/train/start` (`TrainingStartRequest`)
**Required:** `model_name`, `training_type` (`"LoRA/QLoRA"` | `"Full Finetuning"` | `"Continued Pretraining"`),
`format_type` (`auto`/`alpaca`/`chatml`/`sharegpt`).
**Dataset:** `hf_dataset` (HF id) **or** `local_datasets[]`; `subset`, `train_split`/`eval_split`,
`custom_format_mapping` (the AI-assist / manual column→role mapping), `dataset_slice_start/end`.
**Model:** `load_in_4bit` (QLoRA), `max_seq_length`, `trust_remote_code`, `hf_token`.
**Hyperparams:** `learning_rate` (**string**), `batch_size`, `gradient_accumulation_steps`,
`num_epochs`, `max_steps`, `warmup_steps`/`warmup_ratio`, `save_steps`, `eval_steps`, `weight_decay`,
`max_grad_norm`, `optim`, `lr_scheduler_type`, `packing`, `train_on_completions`, `random_seed`,
`gradient_checkpointing`.
**LoRA:** `use_lora`, `lora_r`, `lora_alpha`, `lora_dropout`, `target_modules[]`, `use_rslora`, `use_loftq`.
**Vision/embedding:** `finetune_vision_layers`/`_language_layers`/`_attention_modules`/`_mlp_modules`,
`is_dataset_image/_audio`, `is_embedding`. **Logging:** `enable_wandb`/`wandb_*`, `enable_tensorboard`/`tensorboard_dir`.
**Resume/HW:** `resume_from_checkpoint`, `gpu_ids[]`, `s3_config`.

**Easiest valid payload = clone a prior run:** `GET /api/train/runs/{id}` returns `{run, config, metrics}`
where `config` is the full `TrainingStartRequest` (incl. `custom_format_mapping`). Filter to the schema's
properties, override the levers you want (e.g. `max_steps`, `lora_r`), and `POST` it back. Output lands
under `/workspace/.studio/outputs/<model>_<ts>/` (adapter_model.safetensors + `checkpoint-*`).
**Only one run at a time** — `train/status.is_training_running` must be false to start.
