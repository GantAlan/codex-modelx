# Protocol Notes / ????

Codex ModelX keeps Codex speaking the Responses API locally. The local proxy decides per model whether to pass the request through or convert it.

Codex ModelX ? Codex ?????? Responses API??????????????????????

## Smart Router Chain / ??????

```text
Codex Desktop / current provider custom
-> http://127.0.0.1:17891/v1/responses
-> proxy.py smart router
```

For GPT models:

```text
gpt-5.5 / GPT models
-> proxy.py pass-through
-> upstream /v1/responses
```

For third-party Chat Completions models:

```text
mimo-v2.5 / qwen / kimi / deepseek / glm
-> proxy.py converts /v1/responses to /v1/chat/completions
-> upstream /v1/chat/completions
```

## Model Classes / ????

1. GPT / OpenAI Responses-compatible models: pass through by default; do not convert to Chat Completions.
2. Claude / Anthropic native models: reserved for later deeper support.
3. MiMo, Qwen, Kimi, DeepSeek, GLM and similar third-party models: MVP target; default to `openai_chat_completions` conversion.

## Model List / ????

`/v1/models` should merge two sources:

- Upstream models from the relay, usually GPT models.
- Configured third-party models from `assets/config/modelx.config.json`.

This requires:

```json
"expose_upstream_models": true
```

?? Codex Desktop ? `custom` provider ????????? GPT ???????

## Protocol Values / ?????

- `openai_chat_completions`: default and recommended for third-party relay APIs.
- `openai_responses`: reserved for upstreams that truly implement Responses API.
- `anthropic_compatible`: reserved interface; MVP does not provide full tool_use conversion yet.

## GPT Conversion Guard / GPT ????

GPT models should not be converted to Chat Completions by default, even if they appear in the merged model list. Only set `allow_chat_completions_conversion` for a GPT model if you intentionally want that experimental behavior.
