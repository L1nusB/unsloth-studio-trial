# Unsloth Studio Trial — project instructions

A small, self-contained sandbox for trying **Unsloth Studio** (Unsloth's no-code fine-tuning
web UI) on a **RunPod** GPU, as a quick comparison against our code-based pipeline in the
sibling `llm-fine-tuning` repo. This repo holds **notes, deployment recipes, and pod-access
procedure only** — no fine-tuning code of our own. It is deliberately kept separate from
`llm-fine-tuning` to keep context light (none of that repo's pipeline skills/CLAUDE.md apply here).

## What this repo is / isn't
- **Is:** the roadmap, the RunPod template/container recipe, the SSH/proxy access procedure, and
  running notes on what Studio can do vs our pipeline.
- **Isn't:** a place for training configs, datasets, or the fine-tuning/eval code. Those live in
  the sibling repos under `/Users/linusbeckhaus/Intern/Research/FineTuning/`.

## How we work here
- **Read `docs/roadmap.md` first** — it's the living plan and current status.
- `docs/deployment.md` — the RunPod template / container-image / start-command recipe.
- `docs/runpod-access.md` — how to reach the Studio UI (proxy URL vs SSH `-L` tunnel) and how to
  drive the pod over SSH.
- **context7** before touching fast-moving external APIs (unsloth, RunPod CLI, vLLM, etc.).
- Large outputs (>20 lines): use context-mode tools; never `cat` big logs/JSON.

## Pod interaction
- Studio is reached **in a browser**, not by pushing code — so the main access paths are the
  **RunPod HTTP proxy URL** and an **SSH `-L` tunnel** (see `docs/runpod-access.md`).
- The sibling `llm-fine-tuning` repo's **`pod` CLI** (`uv run pod …`, see its `runpod-pod` skill,
  copied here under `.claude/skills/runpod-pod/` for reference) can drive the *same* pod over
  direct-TCP SSH if we want to `sync`/`run`/`pull` files — run it from that repo, pointed at this
  pod's `RUNPOD_POD_ID`. It is **SSH-only** and needs the pod to expose **public IP + TCP 22**.
- The pod must therefore always be created with **TCP port 22 + a public IP**, plus the Studio
  HTTP port (8000).

## Git discipline
- **Work on a branch**, never commit straight to the default branch.
- Commit completed changes via the **git-committer** agent (semantic messages).
- No remote is configured yet — this is local/researcher-owned. **No `git push`/PR** unless the
  researcher asks; surface local commit hashes instead.

## Secrets
- The RunPod API key, `UNSLOTH_ADMIN_PASSWORD`, and SSH private keys are **never** committed or
  printed. Pod config (e.g. `RUNPOD_POD_ID`) goes in a gitignored `.env`.
