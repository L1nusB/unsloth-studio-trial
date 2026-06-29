# unsloth-studio-trial

A small sandbox for trying **Unsloth Studio** (Unsloth's no-code fine-tuning web UI) on a
**RunPod** GPU — a quick comparison against the code-based pipeline in the sibling
`llm-fine-tuning` repo. **Notes and deployment recipes only**, no fine-tuning code of our own.

## Start here
- **`docs/roadmap.md`** — the living plan, current status, and decisions.
- **`docs/deployment.md`** — RunPod template / container-image / start-command recipe (incl. password automation).
- **`docs/runpod-access.md`** — reaching the Studio UI (proxy URL vs SSH `-L` tunnel).
- **`docs/studio-api.md`** — Studio's REST API: auth (bearer / API keys) and how to drive it programmatically.
- **`docs/dataset-formatting.md`** — what dataset mapping / Data Recipes can and can't do vs the code pipeline.
- **`scripts/studio_set_password.py`** — auto-sets the admin password from `STUDIO_ADMIN_PASSWORD` on a fresh pod.
- **`scripts/studio_api.py`** — call the Studio REST API with auto-auth (reads the credential from the pod env; no secret on the CLI).
- **`CLAUDE.md`** — how to work in this repo.

## One-line gist
Studio is a FastAPI+React web app. Run it on a RunPod pod bound to `0.0.0.0:8000`, expose port
8000 (HTTP) + 22 (TCP), and reach it via `https://<POD_ID>-8000.proxy.runpod.net` or an SSH
`-L` tunnel. Set `UNSLOTH_ADMIN_PASSWORD` to log in.

## Layout
```
docs/                       roadmap, deployment recipe, access guide
.claude/skills/runpod-pod/  reference copy of the sibling repo's pod-CLI skill
CLAUDE.md                   project instructions
```
