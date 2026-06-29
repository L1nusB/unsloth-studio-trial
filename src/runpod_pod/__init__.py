"""Mac-side CLI to drive an already-running RunPod pod over direct-TCP SSH.

Console script ``pod`` (see pyproject [project.scripts]). Subcommands:
doctor / sync / run / logs / status / pull.

Ported verbatim (imports re-rooted to ``runpod_pod``) from the sibling ``llm-fine-tuning``
repo's ``llm_fine_tuning.tools.runpod_pod`` package so it can run standalone here. See
``docs/runpod-access.md`` and ``docs/deployment.md``.
"""
