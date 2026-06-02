---
name: codex-modelx
description: Configure and diagnose a Windows local smart router that lets Codex use GPT/Responses models and third-party non-GPT models such as MiMo, Qwen, Kimi, DeepSeek, and GLM from one custom provider while preserving common tools/plugins.
---

# Codex ModelX

Codex ModelX is a one-time setup and repair skill for using third-party non-GPT models in Codex through a local Windows smart router.

Codex ModelX ????????? / ?????????? Windows ???????? Codex ???? `custom` provider ????? GPT/Responses ??? MiMo?Qwen?Kimi?DeepSeek?GLM ???????

## Current Design / ????

Use one visible Codex provider:

```text
Codex Desktop
-> model_provider = "custom"
-> http://127.0.0.1:17891/v1
-> Codex ModelX smart router
```

Router behavior:

- GPT models such as `gpt-5.5`: pass through to the upstream Responses API without Chat Completions conversion.
- Third-party models such as `mimo-v2.5`, `qwen3.6-plus`, `kimi-k2.5`, `glm-5.1`: convert Codex `/v1/responses` to upstream `/v1/chat/completions`.
- `/v1/models`: merge upstream GPT models and configured third-party models so Codex Desktop's model picker can show both groups.

?????

- `gpt-5.5` ? GPT ?????????? Responses API??? Chat Completions?
- `mimo-v2.5`?`qwen3.6-plus`?`kimi-k2.5`?`glm-5.1` ???????? Codex `/v1/responses` ???? `/v1/chat/completions`?
- `/v1/models`????? GPT ?????????????? Codex Desktop ????????????????

This is different from the split-provider design. Keeping a separate `[model_providers.modelx]` is optional, but Desktop may only show models for the currently selected provider. For the best Desktop model-picker experience, use the smart-router `custom` mode.

?????custom ? GPT?modelx ??????? provider ???? provider ??????????? Desktop ???????????? provider ???????????????? GPT ? MiMo/Qwen/Kimi??????? `custom` ???

## Agent Workflow / ? Codex ?????

When this skill is invoked:

1. Check whether `assets/config/modelx.config.json` exists.
2. If it does not exist, tell the user installation does not auto-run and ask for Base URL, API Key, protocol, and model name.
3. Prefer protocol `openai_chat_completions` for MiMo/Qwen/Kimi/DeepSeek/GLM-style models.
4. Run `python .\scripts\configure.py ...` when values are available.
5. Run `.\scripts\start_proxy.ps1` after configuration.
6. Generate or show the router config fragment that keeps `model_provider = "custom"` and points `[model_providers.custom].base_url` to `http://127.0.0.1:17891/v1`.
7. Do not switch the top-level provider name to `modelx` unless the user explicitly asks for advanced split-provider mode.

???????????

1. ??? `assets/config/modelx.config.json` ?????
2. ??????????? Skill ???????????????? URL / Key????????? Base URL?API Key??????????
3. MiMo / Qwen / Kimi / DeepSeek / GLM ???????? `openai_chat_completions`?
4. ????????? `python .\scripts\configure.py ...`?
5. ??????? `.\scripts\start_proxy.ps1`?
6. ????????????? `model_provider = "custom"`??? `[model_providers.custom].base_url` ?? `http://127.0.0.1:17891/v1`?
7. ????? provider ?? `modelx`???????????? provider ???

## First-Time Setup / ????

Run from PowerShell and replace the example values:

```powershell
cd C:\Users\Alan\.codex\skills\codex-modelx
python .\scripts\configure.py --base-url "https://your-api.example.com/v1" --api-key "paste-your-api-key-here" --protocol openai_chat_completions --model "mimo-v2.5"
.\scripts\start_proxy.ps1
```

Interactive mode is also supported:

```powershell
python .\scripts\configure.py
```

The API Key is stored in plain text in `assets/config/modelx.config.json` for MVP simplicity. Do not share that file.

?????????API Key ?????? `assets/config/modelx.config.json`?????????????

## Codex Config Snippet / Codex ????

Recommended smart-router config:

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
assets/config/modelx.config.json
```

???? Base URL ? API Key ????

```text
assets/config/modelx.config.json
```

Use GPT through the same `custom` provider:

```powershell
codex exec -m gpt-5.5 -c model_provider="custom" "??? OK"
```

Use MiMo through the same `custom` provider:

```powershell
codex exec -m mimo-v2.5 -c model_provider="custom" "??? OK"
```

?????Desktop ?? provider ??? `custom`?GPT ?????????MiMo / Qwen / Kimi / DeepSeek / GLM ?????????

## Start / Stop

```powershell
.\scripts\start_proxy.ps1
.\scripts\stop_proxy.ps1
```

Logs are written under `logs/`; runtime state is written under `state/`.

??? `logs/`?????? `state/`?

If Desktop suddenly only shows GPT models again, repair the `custom` provider and restart the proxy:

```powershell
.\scripts\repair_custom_provider.ps1
.\scripts\start_proxy.ps1
```

Then fully restart Codex Desktop. This repair keeps `model_provider = "custom"` and only points `[model_providers.custom].base_url` back to `http://127.0.0.1:17891/v1`.

CN: If Desktop only shows GPT models again, run the two commands above. The repair keeps provider `custom`; it does not switch the provider to `modelx`.

## Testing / ??

Basic layered test:

```powershell
python .\scripts\test_modelx.py
```

Optional Codex/plugin smoke tests:

```powershell
python .\scripts\test_modelx.py --run-codex-plugin-tests
```

The test flow is layered: proxy health, text response, function-tool loop, then optional Chrome / Zotero / Presentations smoke tests. Failures are written to `logs/diagnostics.md`.

??????????????????? function tool ???????? Chrome / Zotero / Presentations smoke test??????? `logs/diagnostics.md`?

## Tool Strategy / ????

Default tool strategy is `full_tools`. If full tool schemas fail upstream, the proxy can retry with `common_plugins_only` for Chrome, Zotero, and Presentations. Browser-use, Documents, Spreadsheets, and Presentations runtime integrations are experimental beyond the smoke-tested path.

??????? `full_tools`???????????? schema???????? `common_plugins_only`????? Chrome?Zotero?Presentations?Browser-use?Documents?Spreadsheets ????????

## Troubleshooting / ??

- If Desktop only shows GPT models, confirm `[model_providers.custom].base_url` points to `http://127.0.0.1:17891/v1`, restart the proxy, then fully restart Codex Desktop.
- If GPT requests show `converted_to_chat=True`, check whether the GPT model was explicitly configured with `allow_chat_completions_conversion`; normally it should be absent or false.
- If MiMo is not listed, ensure `expose_upstream_models` is true and `models` contains `mimo-v2.5` in `modelx.config.json`.
- If the proxy port is busy, edit `assets/config/modelx.config.json` and update `[model_providers.custom].base_url`.
- If tools fail, check `logs/proxy.log` for `forwarded_tools`, `dropped_tools`, `retried_without_tools`, `retry_strategy`, and `status`.

- ?? Desktop ??? GPT ????? `[model_providers.custom].base_url` ?? `http://127.0.0.1:17891/v1`???????????? Codex Desktop?
- ?? GPT ???? `converted_to_chat=True`?????? GPT ????? `allow_chat_completions_conversion`????????????? false?
- ?? MiMo ????????? `modelx.config.json` ? `expose_upstream_models` ? true?? `models` ??? `mimo-v2.5`?
- ????????? `assets/config/modelx.config.json`???? `[model_providers.custom].base_url`?
- ???????? `logs/proxy.log` ?? `forwarded_tools`?`dropped_tools`?`retried_without_tools`?`retry_strategy`?`status`?
