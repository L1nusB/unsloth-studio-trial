#!/bin/bash
# Run ON the pod (over SSH). RunPod injects template env vars into PID 1, but interactive
# SSH login shells do NOT inherit PID 1's environment — so this copies the vars we care
# about from /proc/1/environ into ~/.bashrc, making them visible in your SSH sessions.
#
# NOT needed for Unsloth Studio itself: the Studio process is launched by the container
# start command (PID 1 lineage) and already sees these vars directly. This is purely a
# convenience for interactive SSH debugging (e.g. running huggingface-cli / uv by hand).
#
# Trimmed var list vs the fine-tuning repo's setup_env.sh (only Studio-relevant ones).

set -e
export PATH="$PATH:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin"

# UNSLOTH_STUDIO_HOME is included so an interactive `unsloth …` over SSH targets the same
# venv/auth/db as the running server (the server itself inherits it from PID 1, so it doesn't
# strictly need this — this is the SSH-shell convenience).
VARS_TO_EXTRACT=("OPENROUTER_API_KEY" "UV_CACHE_DIR" "HF_HOME" "HF_XET_HIGH_PERFORMANCE" "HF_TOKEN" "UNSLOTH_STUDIO_HOME")

echo "🔄 Syncing env vars from PID 1 into ~/.bashrc ..."
for var in "${VARS_TO_EXTRACT[@]}"; do
    value=$(tr '\0' '\n' < /proc/1/environ | grep "^${var}=" | cut -d= -f2- || true)
    if [ -n "$value" ]; then
        if ! grep -q "export ${var}=" ~/.bashrc; then
            printf 'export %s=%q\n' "${var}" "${value}" >> ~/.bashrc
            echo "  ✅ saved $var"
        else
            echo "  ⚡ $var already in ~/.bashrc"
        fi
        export "${var}"="${value}"
    else
        echo "  ❌ $var not found in PID 1 environment"
    fi
done
echo "Done. Re-source your shell:  source ~/.bashrc"
