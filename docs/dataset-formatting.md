# Dataset formatting in Studio — what's possible (and what isn't)

Researched 2026-06-29 (docs are sparse/BETA; some inferred from the live API + UI behavior).

## TL;DR
Studio's dataset **mapping is deliberately simple** and **Data Recipes is a different tool than you'd
expect**. For precise per-experiment prompt templating (like the variants in `llm-fine-tuning`),
**Studio is less capable** — pre-format the data outside Studio and feed it in.

## Two distinct subsystems (don't conflate them)

1. **Training "Dataset" step** (the column-mapper you hit with `pubmedqa`).
   - Formats: `auto` / `alpaca` (instruction/input/output) / `chatml` (messages) / `sharegpt`.
   - Mapping: **one source column per role**. **No system-prompt field**; **no combining multiple
     columns into one role**; **no custom chat template / format string**.
   - **`ai-assist-mapping`** (the "AI Feature"): an LLM **auto-mapper** — inspects sample rows and
     *suggests* a mapping, can synthesize a system prompt / guess a combination. Convenience, not
     control — exactly what you observed. (Endpoints: `/api/datasets/ai-assist-mapping`,
     `/api/hub/datasets/ai-assist-mapping`.)

2. **Data Recipes** (`/api/data-recipe/*`) — a **graph-node synthetic-data generation** tool powered
   by **NVIDIA NeMo DataDesigner**. It is for *creating/transforming* datasets (from PDFs/CSVs/HF/
   GitHub seeds, via LLM blocks, Jinja Expression blocks, validators, samplers), **not** for binding
   existing columns to chat roles with a precise template. Recipes are built in the UI and stored in
   the browser — **no exportable file / version-controllable spec** documented.

So Data Recipes is **not** the answer to "format my pubmedqa prompt exactly." It's powerful for
generating data, but it won't give you per-experiment role/template control either.

## What this means for the comparison
For reproducible, precisely-templated prompts (custom system prompts, multi-column composition,
Jinja/format variants per experiment), the **code-based `llm-fine-tuning` pipeline is strictly more
capable**. Studio's value here is no-code convenience, not template precision.

## Practical workaround (gets you both)
Pre-format **outside** Studio, then feed Studio a trivially-mapped dataset:
- In your pipeline (or a small script), bake the exact template into the data → emit either a single
  `text` column (fully rendered) or a proper `messages` array (chatml) with system/user/assistant
  already composed the way you want.
- Push that to HF (or upload), then in Studio's Dataset step pick `chatml`/`auto` and map the one
  prepared column. You keep your formatting control and still use Studio for the training UX.
- The training YAML config Studio saves then captures the (now-trivial) mapping reproducibly.

This also sidesteps the limited manual mapper entirely.
