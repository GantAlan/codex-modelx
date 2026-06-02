# codex-modelx

A Windows-first Codex Skill that routes third-party OpenAI-compatible models through a local smart proxy while preserving Codex tools/plugins as much as possible.

## What it does

- Keeps Codex provider name as `custom` to avoid changing the Desktop provider/session bucket.
- Exposes one local endpoint: `http://127.0.0.1:17891/v1`.
- Merges GPT/Responses-compatible upstream models and third-party models in `/v1/models`.
- Passes GPT-style Responses models through without Chat Completions conversion.
- Converts MiMo/Qwen/Kimi/DeepSeek/GLM-style models from Codex `/v1/responses` to upstream `/v1/chat/completions`.
- Provides smoke-test and repair scripts for Chrome, Zotero, Presentations-oriented workflows.

## Quick install

Copy the `codex-modelx` folder into your Codex skills directory:

```powershell
Copy-Item -Recurse .\codex-modelx "$env:USERPROFILE\.codex\skills\codex-modelx"
cd "$env:USERPROFILE\.codex\skills\codex-modelx"
python .\scripts\configure.py
.\scripts\start_proxy.ps1
```

Then follow `INSTALL_PROMPT.md` or ask Codex to use the `codex-modelx` skill.

## Safety notes

Do not commit or share:

- `codex-modelx/assets/config/modelx.config.json`
- `codex-modelx/logs/`
- `codex-modelx/state/`
- real API keys or private upstream URLs

This repository includes only examples/templates and sanitized scripts.

## Status

MVP / experimental. Windows-first. Main path is OpenAI-compatible Chat Completions upstreams.
