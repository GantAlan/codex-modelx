# Troubleshooting / ??

## Proxy Cannot Start / ??????

- Check Python is available: `python --version`.
- Check port 17891 is free.
- Run `scripts/stop_proxy.ps1` and then `scripts/start_proxy.ps1`.
- Read `logs/proxy.stderr.log` and `logs/proxy.log`.

## 401 Or 403 From Upstream / ??????

Edit `assets/config/modelx.config.json` and verify `api_key` and `base_url`.
The key is stored in plain text for MVP simplicity; do not share this file.

## Desktop Only Shows GPT Models / Desktop ??? GPT ??

Codex Desktop usually shows models from the currently selected provider only. In smart-router mode, keep the current provider as `custom`, but point it to the local router:

```toml
model_provider = "custom"

[model_providers.custom]
base_url = "http://127.0.0.1:17891/v1"
wire_api = "responses"
```

Then verify `assets/config/modelx.config.json` contains:

```json
"expose_upstream_models": true
```

Restart the proxy, then fully restart Codex Desktop.

One-command repair path:

```powershell
cd C:\Users\<your-user-name>\.codex\skills\codex-modelx
.\scripts\repair_custom_provider.ps1
.\scripts\start_proxy.ps1
```

This backs up `config.toml`, preserves the provider name `custom`, and only repoints `[model_providers.custom].base_url` to the local router. Use `-SetTopLevelCustom` only if your top-level `model_provider` is not already `custom` and you intentionally want this Desktop window to use ModelX.

## GPT Accidentally Converts To Chat / GPT ?????? Chat

Check `logs/proxy.log`. GPT requests should show:

```text
model=gpt-5.5 converted_to_chat=False
```

If GPT shows `converted_to_chat=True`, remove any GPT entry from `models` or ensure it does not set `allow_chat_completions_conversion`.

## MiMo Not Converted / MiMo ????

MiMo requests should show:

```text
model=mimo-v2.5 converted_to_chat=True
```

If not, verify the model name starts with `mimo-` or is listed in `assets/config/modelx.config.json` with protocol `openai_chat_completions`.

## Session Or Sandbox Reset / ????????

Do not change the selected provider name unless you intentionally want a new Codex provider identity. Prefer keeping:

```toml
model_provider = "custom"
```

and only change `[model_providers.custom].base_url` to the local smart router. Do not switch the top-level provider to `modelx` for normal Desktop use.

## Tool Schema Rejected / ?? Schema ???

The proxy first sends `full_tools`. If the upstream rejects the request with HTTP 400, 413, or 422, it retries with `common_plugins_only` using `assets/config/tools.common.json`.
Add plugin-specific keywords there if your tool is not retained.

## Chrome, Zotero, Presentations / ????

The model can only use plugins exposed by the current Codex environment. Zotero Desktop must be open for Zotero tests. Chrome automation may need Codex approval settings or trusted execution flags in CLI smoke tests.
