# Reaching the Studio UI on a RunPod pod

Two ways in. Use the **proxy URL** for a quick look; switch to the **SSH `-L` tunnel** for real
work (it sidesteps the Cloudflare 100 s timeout and ~30 MB upload cap).

Either way, Studio **must** be launched bound to all interfaces: `unsloth studio -H 0.0.0.0 -p 8000`.
A `127.0.0.1` bind (the default) is unreachable from outside the process.

## A. RunPod HTTP proxy (quickest)
1. Declare the Studio port (8000) under **Expose HTTP Ports** in the template/pod.
2. Open: `https://<POD_ID>-8000.proxy.runpod.net`
3. Log in with `UNSLOTH_ADMIN_PASSWORD` (set as a pod env var).

Caveats: Cloudflare-fronted → **100 s timeout (524)** on requests that send no data for 100 s, and
a ~30 MB request-body cap. Normal browsing and token streaming are fine; very large dataset
uploads or very long single requests may fail — use the tunnel for those.

## B. SSH `-L` tunnel (robust)
Requires the pod to expose **public IP + TCP port 22** (direct-TCP SSH, not the exec-only proxy
SSH — `-L` is unreliable over the latter).

```bash
# Resolve the pod's direct-TCP host/port (dashboard "Connect", or runpodctl)
ssh -L 8000:localhost:8000 root@<PUBLIC_IP> -p <SSH_PORT> -i ~/.ssh/<your_key>
# leave it open, then browse:
#   http://localhost:8000
```

No proxy timeout, no upload cap, traffic stays private. One terminal stays held open.

### Reusing the sibling `pod` CLI to find the host/port (and move files)
The `llm-fine-tuning` repo's `pod` CLI drives this *same* direct-TCP channel. From that repo, with
this pod's id in its `.env` (`RUNPOD_POD_ID=...`) or via `--pod`:

```bash
cd ../llm-fine-tuning            # the CLI lives there, not in this repo
uv run pod doctor                # confirms SSH + resolves host/port
uv run pod --pod <id> doctor     # or target a specific pod
```

`pod doctor` prints the resolved direct-TCP host/port you can feed into the `ssh -L` command above.
`pod sync`/`run`/`pull` also work if you ever need to push files or run a command on the pod (e.g.
to install a missing dep). See that repo's `runpod-pod` skill (copied here under
`.claude/skills/runpod-pod/` for reference). It needs `rsync` on the pod for `sync`/`pull`.

## C. `--secure` (Cloudflare tunnel, no port config)
On the pod: `unsloth studio --secure` → prints a `https://*.trycloudflare.com` URL. No RunPod HTTP
port needed; still subject to Cloudflare's timeout. Handy if proxy port config is fiddly.

## Pod creation checklist (so every access path works)
- [ ] **TCP port 22** exposed + **public IP** (enables SSH `-L` and the `pod` CLI)
- [ ] **HTTP port 8000** exposed (Studio proxy URL)
- [ ] (optional) **HTTP port 8888** exposed (JupyterLab)
- [ ] `UNSLOTH_ADMIN_PASSWORD` env var set
- [ ] your SSH **public** key on the account *before* the pod boots (RunPod injects keys at start)
- [ ] enough disk / a network volume mounted at `/workspace` for checkpoints
