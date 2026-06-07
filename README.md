# codex-modelx

A Windows-first Codex Skill that routes third-party OpenAI-compatible models through a local smart proxy while preserving Codex tools/plugins as much as possible.

中文说明 / Chinese guide: [README-cn.md](README-cn.md)

## What it does

- Keeps Codex provider name as `custom` to avoid changing the Desktop provider/session bucket.
- Exposes one local endpoint: `http://127.0.0.1:17891/v1`.
- Merges GPT/Responses-compatible upstream models and third-party models in `/v1/models`.
- Passes GPT-style Responses models through without Chat Completions conversion.
- Converts MiMo/Qwen/Kimi/DeepSeek/GLM-style models from Codex `/v1/responses` to upstream `/v1/chat/completions`.
- Provides smoke-test and repair scripts for Chrome, Zotero, and Presentations-oriented workflows.

## Installation

### Option A: clone from GitHub

```powershell
cd $env:USERPROFILE\.codex\skills
git clone https://github.com/GantAlan/codex-modelx.git codex-modelx-repo
Copy-Item -Recurse .\codex-modelx-repo\codex-modelx .\codex-modelx
cd .\codex-modelx
python .\scripts\configure.py
.\scripts\start_proxy.ps1
```

The final path should be:

```text
%USERPROFILE%\.codex\skills\codex-modelx\SKILL.md
```

Avoid this nested layout:

```text
%USERPROFILE%\.codex\skills\codex-modelx\codex-modelx\SKILL.md
```

### Option B: download ZIP

1. Open <https://github.com/GantAlan/codex-modelx>.
2. Click `Code -> Download ZIP`.
3. Extract it.
4. Copy the inner `codex-modelx` folder into `%USERPROFILE%\.codex\skills\`.

## First configuration

Run:

```powershell
cd $env:USERPROFILE\.codex\skills\codex-modelx
python .\scripts\configure.py
```

Fill in:

- Base URL, usually ending with `/v1`.
- API Key.
- Protocol, usually `openai_chat_completions` for MiMo/Qwen/Kimi/DeepSeek/GLM-style models.
- Model name, such as `mimo-v2.5`, `qwen-max`, `kimi-k2.5`, `deepseek-chat`, or `glm-5.1`.

Then start the proxy:

```powershell
.\scripts\start_proxy.ps1
```

Safe default: `start_proxy.ps1` starts or checks the local proxy only. It does not modify `%USERPROFILE%\.codex\config.toml` unless you explicitly pass:

```powershell
.\scripts\start_proxy.ps1 -RepairCodexConfig
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:17891/__health
```

## Codex config

Point Codex `custom` provider to the local proxy:

```toml
model_provider = "custom"
model = "gpt-5.5"

[model_providers.custom]
name = "custom"
base_url = "http://127.0.0.1:17891/v1"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "dummy-key"
```

The real upstream Base URL and API Key live in:

```text
codex-modelx/assets/config/modelx.config.json
```

Do not share that file.

## Tests

```powershell
python .\scripts\test_modelx.py
```

Optional plugin smoke tests:

```powershell
python .\scripts\test_modelx.py --run-codex-plugin-tests
```

## Repair

If Codex Desktop only shows GPT models again:

```powershell
cd $env:USERPROFILE\.codex\skills\codex-modelx
Invoke-RestMethod http://127.0.0.1:17891/v1/models
.\scripts\repair_custom_provider.ps1
.\scripts\start_proxy.ps1 -RepairCodexConfig
```

Then fully restart Codex Desktop.

## Model Catalog

By default, `codex-modelx` does not install `model_catalog_json`. A malformed catalog can break the Desktop model picker. Preview first:

```powershell
python .\scripts\generate_catalog.py --check-current
python .\scripts\generate_catalog.py --include common
```

Install only if you accept the risk:

```powershell
python .\scripts\generate_catalog.py --include common --install
```

Undo catalog installation:

```powershell
python .\scripts\generate_catalog.py --uninstall
```

## CC Switch note

If you use CC Switch, it usually exposes:

```text
http://127.0.0.1:15721/v1
```

`codex-modelx` uses:

```text
http://127.0.0.1:17891/v1
```

They do not conflict directly. Codex uses whichever endpoint is configured in `config.toml`.

## Safety notes

Do not commit or share:

- `codex-modelx/assets/config/modelx.config.json`
- `codex-modelx/logs/`
- `codex-modelx/state/`
- real API keys or private upstream URLs

This repository includes only examples/templates and sanitized scripts.

## Status

MVP / experimental. Windows-first. Main path is OpenAI-compatible Chat Completions upstreams.
