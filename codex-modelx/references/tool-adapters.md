# Tool adapter notes / 工具适配说明

MVP strategy:

1. Forward all converted Codex tools by default: `full_tools`.
2. If the upstream rejects the full tool schema with HTTP 400, 413, or 422, retry with `common_plugins_only`.
3. The maintained common plugin set focuses on Chrome, Zotero, and Presentations.

Configure common tools in:

```text
assets/config/tools.common.json
```

You can add keywords for more plugins. Matching is done against converted Chat Completions tool names and descriptions.

高级扩展预留：

- `assets/config/adapters.example.json` describes adapter metadata.
- `scripts/adapters/example_adapter.py` shows the future Python adapter shape.
- The MVP keeps this as a stable extension point; do not rely on automatic adapter loading yet.

The example adapter config intentionally includes fields for whitelist/blacklist matching, namespace rules, schema cleanup, name mapping, description compression, parameter pruning, and fallback strategy. These fields are meant to make community optimization shareable even before full automatic adapter loading is implemented.

`adapters.example.json` 已经预留工具白名单 / 黑名单、namespace 规则、schema 清洗、名称映射、描述压缩、参数裁剪和降级策略字段。这样后续用户优化某个插件时，可以先共享配置方案，再逐步升级为 Python adapter。
