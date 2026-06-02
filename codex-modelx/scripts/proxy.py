#!/usr/bin/env python3
"""Small reversible proxy for Codex /v1/responses requests.

It forwards /v1/* to an upstream OpenAI-compatible base URL and removes only
the image_generation tool from POST /v1/responses bodies.

For Xiaomi/MiMo chat-only models, it also adapts Codex's /v1/responses request
to /v1/chat/completions and wraps the result back into a Responses-shaped
payload. This keeps the Codex UI on one base_url while allowing model-specific
wire compatibility.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}

DEFAULT_USER_AGENT = "Codex-Desktop/26.506 local-image-generation-filter"
CHAT_COMPLETIONS_MODELS = ("mimo-", "qwen", "kimi", "moonshot", "deepseek", "glm")
NATIVE_GPT_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "o5")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "assets" / "config" / "modelx.config.json"
DEFAULT_COMMON_TOOL_KEYWORDS = (
    "chrome",
    "zotero",
    "presentation",
    "presentations",
    "mcp__chrome",
    "mcp__zotero",
    "slide",
    "ppt",
    "pptx",
)
MODEL_CACHE_TTL_SECONDS = 300
FORWARD_TOOLS_TO_CHAT_COMPLETIONS = True
CHAT_FUNCTION_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
CHAT_TOOL_TRIGGER_KEYWORDS = (
    "tool",
    "tools",
    "plugin",
    "browser",
    "google",
    "search",
    "open",
    "http://",
    "https://",
    "shell",
    "command",
    "execute",
    "run",
    "read",
    "write",
    "edit",
    "file",
    "插件",
    "工具",
    "浏览器",
    "网页",
    "谷歌",
    "搜索",
    "打开",
    "访问",
    "命令",
    "运行",
    "读取",
    "写入",
    "修改",
    "文件",
)
GENERIC_CODEX_BASE_INSTRUCTIONS = (
    "You are Codex, a coding agent. Help the user with software engineering "
    "tasks in the current workspace, follow the request carefully, and keep "
    "responses concise unless more detail is needed."
)
GENERIC_CODEX_MODEL_MESSAGES = {
    "instructions_template": "{{ personality }}\n\n"
    "You are Codex, a coding agent. Help the user with software engineering "
    "tasks in the current workspace.",
    "instructions_variables": {
        "personality_default": "",
        "personality_friendly": "",
        "personality_pragmatic": "",
    },
}


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def contains_image_generation(value: Any) -> bool:
    if isinstance(value, str):
        return value == "image_generation" or value == "built-in image_gen"
    if isinstance(value, dict):
        return any(contains_image_generation(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_image_generation(v) for v in value)
    return False


def rewrite_responses_body(body: bytes) -> tuple[bytes, int, bool]:
    """Remove image_generation from a JSON /responses request body."""
    if not body:
        return body, 0, False

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return body, 0, False

    if not isinstance(payload, dict):
        return body, 0, False

    removed = 0
    tools = payload.get("tools")
    if isinstance(tools, list):
        kept = []
        for tool in tools:
            if isinstance(tool, dict) and tool.get("type") == "image_generation":
                removed += 1
            else:
                kept.append(tool)
        if removed:
            payload["tools"] = kept

    tool_choice_changed = False
    if "tool_choice" in payload and contains_image_generation(payload.get("tool_choice")):
        payload["tool_choice"] = "auto"
        tool_choice_changed = True

    if removed or tool_choice_changed:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    return body, removed, tool_choice_changed


def should_use_chat_completions(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    model_lower = model.lower()
    return model_lower.startswith(CHAT_COMPLETIONS_MODELS)


def is_native_gpt_model(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    model_lower = model.strip().lower()
    return model_lower.startswith(NATIVE_GPT_MODEL_PREFIXES)


def model_is_explicitly_configured(upstream: dict[str, Any], model_name: str) -> bool:
    return bool(model_config_for_name(upstream, model_name))


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise SystemExit(f"Could not read JSON config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Config file must contain a JSON object: {path}")
    return data


def active_upstream_from_config(config: dict[str, Any]) -> dict[str, Any]:
    upstreams = config.get("upstreams")
    if not isinstance(upstreams, list) or not upstreams:
        return {}
    active_name = config.get("active_upstream") or "default"
    for upstream in upstreams:
        if isinstance(upstream, dict) and upstream.get("name") == active_name:
            return upstream
    first = upstreams[0]
    return first if isinstance(first, dict) else {}


def model_entries_from_upstream(upstream: dict[str, Any]) -> list[dict[str, Any]]:
    models = upstream.get("models")
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, dict)]


def model_config_for_name(upstream: dict[str, Any], model_name: str) -> dict[str, Any]:
    model_lower = model_name.lower()
    for model in model_entries_from_upstream(upstream):
        name = model.get("name")
        if isinstance(name, str) and name.lower() == model_lower:
            return model
    return {}


def protocol_is_chat_completions(protocol: Any) -> bool:
    return str(protocol or "").strip().lower() in {
        "openai_chat_completions",
        "chat_completions",
        "chat",
        "openai-compatible",
        "openai_compatible",
    }


def normalize_tool_strategy(value: Any) -> str:
    strategy = str(value or "full_tools").strip().lower()
    if strategy in {"full", "full_tools"}:
        return "full_tools"
    if strategy in {"common", "common_plugins", "common_plugins_only"}:
        return "common_plugins_only"
    if strategy in {"none", "no_tools", "disabled"}:
        return "no_tools"
    return "full_tools"


def chat_tool_search_blob(tool: dict[str, Any]) -> str:
    function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    parts = [
        tool.get("type"),
        function.get("name") if isinstance(function, dict) else None,
        function.get("description") if isinstance(function, dict) else None,
    ]
    return " ".join(str(part).lower() for part in parts if part)


def filter_chat_tools_by_keywords(
    chat_payload: dict[str, Any],
    keywords: tuple[str, ...] | list[str],
    exclude_keywords: tuple[str, ...] | list[str] = (),
) -> tuple[int, int]:
    tools = chat_payload.get("tools")
    if not isinstance(tools, list):
        return 0, 0
    lowered = [str(keyword).lower() for keyword in keywords if str(keyword).strip()]
    excluded = [str(keyword).lower() for keyword in exclude_keywords if str(keyword).strip()]
    kept: list[Any] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        blob = chat_tool_search_blob(tool)
        if any(keyword in blob for keyword in lowered) and not any(keyword in blob for keyword in excluded):
            kept.append(tool)
    before = len(tools)
    if kept:
        chat_payload["tools"] = kept
    else:
        chat_payload.pop("tools", None)
        chat_payload.pop("tool_choice", None)
        chat_payload.pop("parallel_tool_calls", None)
    return before, len(kept)


def remove_chat_tools(chat_payload: dict[str, Any]) -> int:
    tools = chat_payload.get("tools")
    removed = len(tools) if isinstance(tools, list) else 0
    chat_payload.pop("tools", None)
    chat_payload.pop("tool_choice", None)
    chat_payload.pop("parallel_tool_calls", None)
    return removed


def should_override_user_agent(value: str) -> bool:
    lowered = value.lower()
    return (
        not value.strip()
        or lowered.startswith("python-urllib/")
        or lowered.startswith("python-requests/")
        or lowered.startswith("curl/")
        or lowered.startswith("powershell/")
        or lowered.startswith("mozilla/")
        or "codex-modelx" in lowered
    )


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"input_text", "output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif part_type in {"input_image", "image_url"}:
                # MiMo is used here as a chat-completions text fallback.
                # Preserve the fact that an image existed without forwarding it
                # to a model/path that may not support image input.
                parts.append("[image input omitted by local proxy]")
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def normalize_chat_role(role: Any) -> str:
    if role in {"assistant", "user", "system"}:
        return role
    if role == "developer":
        return "system"
    if role == "tool":
        return "user"
    return "user"


def stringify_tool_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return "{}"
    if isinstance(arguments, (dict, list, int, float, bool)):
        return compact_json(arguments)
    return str(arguments)


def normalize_chat_tool_parameters(parameters: Any) -> dict[str, Any]:
    if isinstance(parameters, dict):
        normalized = dict(parameters)
    else:
        normalized = {}
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    return normalized


def sanitize_chat_tool_name(name: str) -> str:
    cleaned = CHAT_FUNCTION_NAME_RE.sub("_", name).strip("_")
    if not cleaned:
        cleaned = "tool"
    if len(cleaned) <= 64:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[:55]}_{digest}"


def flatten_namespace_tool_name(namespace: str, name: str) -> str:
    if not namespace:
        return sanitize_chat_tool_name(name)
    if not name:
        return sanitize_chat_tool_name(namespace)
    if namespace.endswith("__") or name.startswith("__"):
        return sanitize_chat_tool_name(f"{namespace}{name}")
    return sanitize_chat_tool_name(f"{namespace}__{name}")


def empty_tool_context() -> dict[str, Any]:
    return {"custom_tools": {}, "function_tools": {}}


def add_custom_tool_context(context: dict[str, Any], upstream_name: str, original_name: str) -> None:
    context["custom_tools"][upstream_name] = {"name": original_name}


def add_function_tool_context(
    context: dict[str, Any],
    upstream_name: str,
    original_name: str,
    namespace: str = "",
) -> None:
    context["function_tools"][upstream_name] = {"name": original_name, "namespace": namespace}


def upstream_name_for_custom_tool(context: dict[str, Any], name: str) -> str:
    for upstream_name, spec in context.get("custom_tools", {}).items():
        if spec.get("name") == name:
            return upstream_name
    return sanitize_chat_tool_name(name)


def upstream_name_for_function_tool(context: dict[str, Any], name: str, namespace: str = "") -> str:
    for upstream_name, spec in context.get("function_tools", {}).items():
        if spec.get("name") == name and spec.get("namespace", "") == namespace:
            return upstream_name
    return flatten_namespace_tool_name(namespace, name) if namespace else sanitize_chat_tool_name(name)


def response_output_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = text_from_content(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("output_text", "text", "content", "input"):
            item = value.get(key)
            if isinstance(item, str):
                return item
        return compact_json(value)
    if value is None:
        return ""
    return str(value)


def custom_tool_arguments_from_input(value: Any) -> str:
    return compact_json({"input": response_output_text(value)})


def custom_tool_input_from_arguments(arguments: str) -> str:
    try:
        value = json.loads(arguments)
    except Exception:
        return arguments
    if isinstance(value, dict) and "input" in value:
        return response_output_text(value.get("input"))
    return arguments


def responses_input_to_chat_messages(
    payload: dict[str, Any],
    tool_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    tool_context = tool_context or empty_tool_context()
    messages: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []

    def flush_pending_tool_calls() -> None:
        nonlocal pending_tool_calls
        if not pending_tool_calls:
            return
        messages.append({"role": "assistant", "content": "", "tool_calls": pending_tool_calls})
        pending_tool_calls = []

    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})

    value = payload.get("input")
    if isinstance(value, str):
        messages.append({"role": "user", "content": value})
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                flush_pending_tool_calls()
                messages.append({"role": "user", "content": item})
                continue
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "function_call_output":
                flush_pending_tool_calls()
                call_id = item.get("call_id") or item.get("id") or "unknown"
                output = text_from_content(item.get("output"))
                tool_message: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": str(call_id),
                    "content": output or "",
                }
                name = item.get("name")
                if isinstance(name, str) and name:
                    tool_message["name"] = name
                messages.append(tool_message)
                continue

            if item_type == "function_call":
                call_id = item.get("call_id") or item.get("id") or f"call_proxy_hist_{len(messages)}"
                name = item.get("name") or "tool"
                namespace = item.get("namespace") if isinstance(item.get("namespace"), str) else ""
                upstream_name = upstream_name_for_function_tool(tool_context, str(name), namespace)
                pending_tool_calls.append(
                    {
                        "id": str(call_id),
                        "type": "function",
                        "function": {
                            "name": upstream_name,
                            "arguments": stringify_tool_arguments(item.get("arguments")),
                        },
                    }
                )
                continue

            if item_type == "custom_tool_call":
                call_id = item.get("call_id") or item.get("id") or f"call_proxy_custom_{len(messages)}"
                name = str(item.get("name") or "tool")
                upstream_name = upstream_name_for_custom_tool(tool_context, name)
                pending_tool_calls.append(
                    {
                        "id": str(call_id),
                        "type": "function",
                        "function": {
                            "name": upstream_name,
                            "arguments": custom_tool_arguments_from_input(
                                item.get("input") if "input" in item else item.get("arguments")
                            ),
                        },
                    }
                )
                continue

            if item_type == "custom_tool_call_output":
                flush_pending_tool_calls()
                call_id = item.get("call_id") or item.get("id") or "unknown"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call_id),
                        "content": response_output_text(item.get("output")),
                    }
                )
                continue

            flush_pending_tool_calls()
            role = normalize_chat_role(item.get("role"))
            content = text_from_content(item.get("content"))
            if role == "assistant" or content:
                messages.append({"role": role, "content": content})

        flush_pending_tool_calls()

    if not messages:
        messages.append({"role": "user", "content": compact_json(payload.get("input", payload))})

    return messages


def should_forward_chat_tools(payload: dict[str, Any]) -> bool:
    if not FORWARD_TOOLS_TO_CHAT_COMPLETIONS:
        return False
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    tool_choice = payload.get("tool_choice")
    if tool_choice == "none":
        return False
    return True


def make_chat_function_tool(
    name: str,
    description: Any = "",
    parameters: Any = None,
    strict: Any = None,
) -> dict[str, Any]:
    function: dict[str, Any] = {
        "name": name,
        "parameters": normalize_chat_tool_parameters(parameters),
    }
    if isinstance(description, str) and description.strip():
        function["description"] = description.strip()
    if isinstance(strict, bool):
        function["strict"] = strict
    return {"type": "function", "function": function}


def make_generic_custom_proxy_tool(name: str, description: Any = "") -> dict[str, Any]:
    if isinstance(description, str) and description.strip():
        tool_description = (
            description.strip()
            + "\n\nThis is a freeform Codex tool. Put only the raw tool input in the input field."
        )
    else:
        tool_description = f"Freeform Codex tool: {name}. Put only the raw tool input in the input field."
    return make_chat_function_tool(
        name,
        tool_description,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Raw freeform input for this Codex tool.",
                }
            },
            "required": ["input"],
        },
    )


def response_tool_to_chat_tool(tool: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    if tool.get("type") == "image_generation":
        return []

    tool_type = tool.get("type")
    if tool_type == "namespace":
        namespace = tool.get("name") if isinstance(tool.get("name"), str) else ""
        namespace_description = tool.get("description") if isinstance(tool.get("description"), str) else ""
        children = tool.get("tools")
        if not isinstance(children, list):
            return []
        converted: list[dict[str, Any]] = []
        for child in children:
            if not isinstance(child, dict) or child.get("type") != "function":
                continue
            child_name = child.get("name")
            if not isinstance(child_name, str) or not child_name:
                continue
            upstream_name = flatten_namespace_tool_name(namespace, child_name)
            add_function_tool_context(context, upstream_name, child_name, namespace)
            child_description = child.get("description") if isinstance(child.get("description"), str) else ""
            description = "\n\n".join(part for part in (namespace_description, child_description) if part)
            converted.append(
                make_chat_function_tool(
                    upstream_name,
                    description,
                    child.get("parameters") or child.get("input_schema") or child.get("schema"),
                    child.get("strict"),
                )
            )
        return converted

    if tool_type in {"custom", "web_search", "local_shell", "computer_use"}:
        name = tool.get("name") if isinstance(tool.get("name"), str) and tool.get("name") else str(tool_type)
        upstream_name = sanitize_chat_tool_name(name)
        add_custom_tool_context(context, upstream_name, name)
        return [make_generic_custom_proxy_tool(upstream_name, tool.get("description"))]

    function = tool.get("function")
    if isinstance(function, dict):
        function = dict(function)
    else:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            return []
        function = {"name": name}

    name = function.get("name")
    if not isinstance(name, str) or not name:
        return []
    namespace = function.get("namespace") or tool.get("namespace")
    namespace = namespace if isinstance(namespace, str) else ""
    upstream_name = upstream_name_for_function_tool(context, name, namespace)
    add_function_tool_context(context, upstream_name, name, namespace)
    function["name"] = upstream_name

    description = function.get("description")
    if not isinstance(description, str) or not description:
        description = tool.get("description")
    if isinstance(description, str) and description:
        function["description"] = description

    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        parameters = tool.get("parameters") or tool.get("input_schema") or tool.get("schema")
    function["parameters"] = normalize_chat_tool_parameters(parameters)

    if not isinstance(function.get("strict"), bool) and isinstance(tool.get("strict"), bool):
        function["strict"] = tool["strict"]

    function.pop("namespace", None)
    return [{"type": "function", "function": function}]


def responses_tools_to_chat_tools(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return [], 0

    converted: list[dict[str, Any]] = []
    dropped = 0
    for tool in tools:
        if isinstance(tool, str) and tool:
            upstream_name = sanitize_chat_tool_name(tool)
            add_custom_tool_context(context, upstream_name, tool)
            converted.append(make_generic_custom_proxy_tool(upstream_name))
            continue
        if not isinstance(tool, dict):
            dropped += 1
            continue
        converted_tools = response_tool_to_chat_tool(tool, context)
        if not converted_tools:
            dropped += 1
            continue
        converted.extend(converted_tools)

    return converted, dropped


def response_tool_choice_to_chat(tool_choice: Any, context: dict[str, Any]) -> Any:
    if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "function":
        function = tool_choice.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            namespace = function.get("namespace")
        else:
            name = tool_choice.get("name")
            namespace = tool_choice.get("namespace")
        if isinstance(name, str) and name:
            upstream_name = upstream_name_for_function_tool(
                context,
                name,
                namespace if isinstance(namespace, str) else "",
            )
            return {"type": "function", "function": {"name": upstream_name}}
    if choice_type == "custom":
        name = tool_choice.get("name")
        if isinstance(name, str) and name:
            return {
                "type": "function",
                "function": {"name": upstream_name_for_custom_tool(context, name)},
            }
    return None


def count_response_tools(payload: dict[str, Any]) -> int:
    tools = payload.get("tools")
    return len(tools) if isinstance(tools, list) else 0


def response_payload_to_chat_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int, int, dict[str, Any]]:
    tool_context = empty_tool_context()
    tools: list[dict[str, Any]] = []
    dropped_tools = 0
    forwarded_tools = 0
    if should_forward_chat_tools(payload):
        tools, dropped_tools = responses_tools_to_chat_tools(payload, tool_context)
        forwarded_tools = len(tools)
    elif isinstance(payload.get("tools"), list):
        dropped_tools = count_response_tools(payload)

    chat_payload: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": responses_input_to_chat_messages(payload, tool_context),
    }
    for key in (
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "stream",
        "frequency_penalty",
        "presence_penalty",
        "response_format",
        "seed",
        "stop",
        "user",
        "metadata",
    ):
        if key in payload:
            chat_payload[key] = payload[key]
    if "max_output_tokens" in payload and "max_tokens" not in chat_payload:
        chat_payload["max_tokens"] = payload["max_output_tokens"]

    if tools:
        chat_payload["tools"] = tools
        tool_choice = response_tool_choice_to_chat(payload.get("tool_choice"), tool_context)
        if tool_choice is not None:
            chat_payload["tool_choice"] = tool_choice
        if "parallel_tool_calls" in payload:
            chat_payload["parallel_tool_calls"] = payload["parallel_tool_calls"]

    return chat_payload, dropped_tools, forwarded_tools, tool_context


def normalize_responses_usage(usage: Any) -> dict[str, Any]:
    if isinstance(usage, dict) and "input_tokens" in usage:
        return usage
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    else:
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": total_tokens,
    }


def model_id_from_catalog_entry(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return ""
    for key in ("id", "slug", "name"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def make_codex_model_entry(entry: Any, priority: int) -> dict[str, Any] | None:
    model_id = model_id_from_catalog_entry(entry)
    if not model_id:
        return None

    display_name = model_id
    description = "Upstream model exposed by the configured relay."
    if isinstance(entry, dict):
        for key in ("display_name", "displayName", "name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                display_name = value
                break
        value = entry.get("description")
        if isinstance(value, str) and value.strip():
            description = value

    is_mimo = should_use_chat_completions(model_id)
    is_gpt = model_id.lower().startswith("gpt-")
    input_modalities = ["text"] if is_mimo else ["text", "image"]
    supports_search_tool = bool(is_gpt)

    # Codex Desktop's model catalog uses "models"; OpenAI-compatible APIs use
    # "data". Keep the upstream fields and add the catalog shape Codex expects.
    return {
        "id": model_id,
        "slug": model_id,
        "display_name": display_name,
        "description": description,
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "medium", "description": "Balanced reasoning"},
            {"effort": "high", "description": "Greater reasoning depth"},
            {"effort": "xhigh", "description": "Extra high reasoning depth"},
        ],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "availability_nux": {"message": ""},
        "upgrade": None,
        "base_instructions": GENERIC_CODEX_BASE_INSTRUCTIONS,
        "model_messages": GENERIC_CODEX_MODEL_MESSAGES,
        "supports_reasoning_summaries": True,
        "default_reasoning_summary": "none",
        "support_verbosity": True,
        "default_verbosity": "low",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": not is_mimo,
        "supports_image_detail_original": not is_mimo,
        "context_window": 272000 if is_gpt else 128000,
        "max_context_window": 1000000 if is_gpt else 128000,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": input_modalities,
        "supports_search_tool": supports_search_tool,
    }


def add_codex_models_field(
    body: bytes,
    configured_models: list[dict[str, Any]] | None = None,
    expose_upstream_models: bool = False,
) -> tuple[bytes, bool]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return body, False
    if not isinstance(payload, dict) or isinstance(payload.get("models"), list):
        return body, False

    source_models = payload.get("data")
    if not isinstance(source_models, list):
        source_models = []

    configured_models = configured_models or []
    merged_source_models: list[Any] = list(source_models) if expose_upstream_models else []
    seen_ids = {model_id_from_catalog_entry(entry).lower() for entry in merged_source_models}
    for entry in configured_models:
        model_id = model_id_from_catalog_entry(entry).lower()
        if model_id and model_id not in seen_ids:
            merged_source_models.append(entry)
            seen_ids.add(model_id)

    models: list[dict[str, Any]] = []
    for index, entry in enumerate(merged_source_models):
        model = make_codex_model_entry(entry, index + 100)
        if model is not None:
            models.append(model)

    if not models:
        return body, False

    payload["data"] = merged_source_models
    payload["models"] = models
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), True


def make_response_object(
    *,
    response_id: str,
    message_id: str,
    model: str,
    content: str,
    usage: Any = None,
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": [
            {
                "id": message_id,
                "type": "message",
                "status": status,
                "role": "assistant",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        ],
        "output_text": content,
        "usage": normalize_responses_usage(usage),
        "tools": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
    }


def response_request_fields(original_request: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(original_request, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "instructions",
        "max_output_tokens",
        "parallel_tool_calls",
        "previous_response_id",
        "reasoning",
        "temperature",
        "tool_choice",
        "tools",
        "top_p",
        "metadata",
    ):
        if key in original_request:
            result[key] = original_request[key]
    return result


def response_tool_call_item(
    tool_call: dict[str, Any],
    index: int,
    tool_context: dict[str, Any],
) -> dict[str, Any] | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    upstream_name = function.get("name")
    if not isinstance(upstream_name, str) or not upstream_name:
        return None
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        arguments = "{}"
    call_id = tool_call.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"call_proxy_{int(time.time() * 1000)}_{index}"

    custom_spec = tool_context.get("custom_tools", {}).get(upstream_name)
    if isinstance(custom_spec, dict):
        return {
            "id": f"ctc_{call_id}",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": call_id,
            "name": custom_spec.get("name") or upstream_name,
            "input": custom_tool_input_from_arguments(arguments),
        }

    function_spec = tool_context.get("function_tools", {}).get(upstream_name)
    item: dict[str, Any] = {
        "id": f"fc_{call_id}",
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": upstream_name,
        "arguments": arguments,
    }
    if isinstance(function_spec, dict):
        item["name"] = function_spec.get("name") or upstream_name
        namespace = function_spec.get("namespace")
        if isinstance(namespace, str) and namespace:
            item["namespace"] = namespace
    return item


def make_function_call_response_object(
    *,
    response_id: str,
    model: str,
    tool_calls: list[dict[str, Any]],
    usage: Any = None,
    tool_context: dict[str, Any] | None = None,
    original_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_context = tool_context or empty_tool_context()
    output: list[dict[str, Any]] = []
    for index, tool_call in enumerate(tool_calls):
        if not isinstance(tool_call, dict):
            continue
        item = response_tool_call_item(tool_call, index, tool_context)
        if item is not None:
            output.append(item)

    body = {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "output_text": "",
        "usage": normalize_responses_usage(usage),
        "tools": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
    }
    body.update(response_request_fields(original_request))
    return body


def chat_response_to_responses_body(
    chat_body: bytes,
    model: str,
    tool_context: dict[str, Any] | None = None,
    original_request: dict[str, Any] | None = None,
) -> bytes:
    tool_context = tool_context or empty_tool_context()
    chat = json.loads(chat_body.decode("utf-8"))
    response_id = "resp_" + str(chat.get("id") or int(time.time() * 1000))
    message_suffix = response_id[5:] if response_id.startswith("resp_") else response_id
    message_id = "msg_" + message_suffix
    content = ""
    output: list[dict[str, Any]] = []
    choices = chat.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(message, dict):
            content_value = message.get("content")
            if isinstance(content_value, str):
                content = content_value
                if content:
                    output.append(
                        {
                            "id": message_id,
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content, "annotations": []}],
                        }
                    )
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for index, tool_call in enumerate(tool_calls):
                    if isinstance(tool_call, dict):
                        item = response_tool_call_item(tool_call, index, tool_context)
                        if item is not None:
                            output.append(item)
    if not output:
        output.append(
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        )
    body = {
        "id": response_id,
        "object": "response",
        "created_at": int(chat.get("created") or time.time()),
        "status": "completed",
        "model": str(chat.get("model") or model),
        "output": output,
        "output_text": content,
        "usage": normalize_responses_usage(chat.get("usage")),
        "tools": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
    }
    body.update(response_request_fields(original_request))
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ResponsesProxyHandler(BaseHTTPRequestHandler):
    server_version = "CodexResponsesProxy/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def proxy(self) -> "ProxyServer":
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        if self.path == "/__health":
            body = b'{"ok":true}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        self.forward()

    def do_POST(self) -> None:
        self.forward()

    def do_PUT(self) -> None:
        self.forward()

    def do_PATCH(self) -> None:
        self.forward()

    def do_DELETE(self) -> None:
        self.forward()

    def forward(self) -> None:
        started = time.time()
        path = urllib.parse.urlsplit(self.path).path
        if not path.startswith("/v1/") and path != "/v1":
            self.send_error(404, "proxy only serves /v1/*")
            return

        if self.command.upper() == "GET" and path == "/v1/models":
            cached = self.proxy.get_model_cache(self.path)
            if cached is not None:
                self._send_synthetic_json_response(200, cached)
                duration_ms = int((time.time() - started) * 1000)
                self.proxy.write_log(
                    method=self.command.upper(),
                    path=self.path,
                    status=200,
                    removed_count=0,
                    tool_choice_changed=False,
                    converted_to_chat=False,
                    model="cache",
                    dropped_tools=0,
                    duration_ms=duration_ms,
                )
                return

        upstream_url = self.proxy.upstream_base_url.rstrip("/") + self.path[len("/v1") :]
        body = self._read_body()
        removed_count = 0
        tool_choice_changed = False
        converted_to_chat = False
        model = ""
        dropped_tools = 0
        forwarded_tools = 0
        stream_converted_tool_call = False
        tool_context = empty_tool_context()
        original_request: dict[str, Any] | None = None
        chat_payload_for_retry: dict[str, Any] | None = None
        retried_without_tools = False
        retry_strategy = ""

        if self.command.upper() == "POST" and path == "/v1/responses":
            body, removed_count, tool_choice_changed = rewrite_responses_body(body)
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict):
                original_request = payload
                model = str(payload.get("model") or "")
                if self.proxy.should_block_native_model(model):
                    status = 409
                    message = json.dumps(
                        {
                            "error": {
                                "message": (
                                    f"Model {model} is a native GPT/OpenAI model and is intentionally not routed "
                                    "through codex-modelx. Use Codex's native OpenAI provider for GPT models, and "
                                    "use codex-modelx only for third-party models such as MiMo, Qwen, Kimi, DeepSeek, or GLM."
                                ),
                                "type": "modelx_native_model_blocked",
                            }
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self._send_synthetic_json_response(status, message)
                    return
                if self.proxy.should_use_chat_completions(model):
                    chat_payload, dropped_tools, forwarded_tools, tool_context = response_payload_to_chat_payload(payload)
                    tool_strategy = self.proxy.tool_strategy_for_model(model)
                    if tool_strategy == "no_tools":
                        removed_tools = remove_chat_tools(chat_payload)
                        dropped_tools += removed_tools
                        forwarded_tools = 0
                    elif tool_strategy == "common_plugins_only":
                        before_tools, after_tools = filter_chat_tools_by_keywords(
                            chat_payload,
                            self.proxy.common_tool_keywords,
                            self.proxy.common_tool_exclude_keywords,
                        )
                        dropped_tools += max(0, before_tools - after_tools)
                        forwarded_tools = after_tools
                    chat_payload_for_retry = chat_payload
                    if chat_payload.get("tools") and chat_payload.get("stream"):
                        chat_payload["stream"] = False
                        stream_converted_tool_call = True
                    body = compact_json(chat_payload).encode("utf-8")
                    upstream_url = self.proxy.upstream_base_url.rstrip("/") + "/chat/completions"
                    converted_to_chat = True

        headers = self._forward_headers(len(body) if body else None)
        request = urllib.request.Request(
            upstream_url,
            data=body if body else None,
            headers=headers,
            method=self.command.upper(),
        )

        status = 502
        try:
            with urllib.request.urlopen(request, timeout=self.proxy.timeout_seconds) as response:
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "")
                if converted_to_chat and "text/event-stream" in content_type.lower():
                    self._send_chat_stream_as_responses(response, model=model)
                elif "text/event-stream" in content_type.lower():
                    self._send_streaming_response(response)
                else:
                    response_body = response.read()
                    if self.command.upper() == "GET" and path == "/v1/models":
                        wrapped, changed = add_codex_models_field(
                            response_body,
                            self.proxy.configured_models,
                            expose_upstream_models=self.proxy.expose_upstream_models,
                        )
                        if changed:
                            self.proxy.set_model_cache(self.path, wrapped)
                            self._send_synthetic_json_response(int(response.status), wrapped)
                        else:
                            self.proxy.set_model_cache(self.path, response_body)
                            self._send_buffered_response(response, response_body)
                    elif converted_to_chat:
                        wrapped = chat_response_to_responses_body(
                            response_body,
                            model=model,
                            tool_context=tool_context,
                            original_request=original_request,
                        )
                        if stream_converted_tool_call:
                            self._send_synthetic_responses_stream(int(response.status), wrapped)
                        else:
                            self._send_synthetic_json_response(int(response.status), wrapped)
                    else:
                        self._send_buffered_response(response, response_body)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            error_body = exc.read()
            retryable_chat_tool_error = (
                converted_to_chat
                and status in {400, 413, 422}
                and isinstance(chat_payload_for_retry, dict)
                and bool(chat_payload_for_retry.get("tools"))
                and chat_payload_for_retry.get("tool_choice") != "required"
            )
            if retryable_chat_tool_error:
                retry_payload = dict(chat_payload_for_retry)
                before_retry_tools, after_retry_tools = filter_chat_tools_by_keywords(
                    retry_payload,
                    self.proxy.common_tool_keywords,
                    self.proxy.common_tool_exclude_keywords,
                )
                if before_retry_tools and after_retry_tools and after_retry_tools < before_retry_tools:
                    retry_strategy = "common_plugins_only"
                    forwarded_tools = after_retry_tools
                    dropped_tools += before_retry_tools - after_retry_tools
                elif self.proxy.allow_no_tools_retry:
                    removed_retry_tools = remove_chat_tools(retry_payload)
                    retry_strategy = "no_tools"
                    retried_without_tools = removed_retry_tools > 0
                    forwarded_tools = 0
                    dropped_tools += removed_retry_tools
                else:
                    retry_strategy = "common_plugins_only_unavailable"
                messages = retry_payload.get("messages")
                if isinstance(messages, list):
                    retry_payload["messages"] = list(messages) + [
                        {
                            "role": "system",
                            "content": (
                                "The previous request failed after attaching the full Codex tool schema. "
                                "Retry with the reduced common plugin tool set. If no relevant tool is "
                                "available, explain the failure and give the best final response possible."
                            ),
                        }
                    ]
                retry_body = compact_json(retry_payload).encode("utf-8")
                retry_headers = self._forward_headers(len(retry_body))
                retry_request = urllib.request.Request(
                    self.proxy.upstream_base_url.rstrip("/") + "/chat/completions",
                    data=retry_body,
                    headers=retry_headers,
                    method=self.command.upper(),
                )
                try:
                    with urllib.request.urlopen(retry_request, timeout=self.proxy.timeout_seconds) as retry_response:
                        status = int(retry_response.status)
                        retry_response_body = retry_response.read()
                        wrapped = chat_response_to_responses_body(
                            retry_response_body,
                            model=model,
                            tool_context=tool_context,
                            original_request=original_request,
                        )
                        if stream_converted_tool_call:
                            self._send_synthetic_responses_stream(status, wrapped)
                        else:
                            self._send_synthetic_json_response(status, wrapped)
                        if retry_strategy == "no_tools":
                            retried_without_tools = True
                except urllib.error.HTTPError as retry_exc:
                    status = int(retry_exc.code)
                    retry_error_body = retry_exc.read()
                    self._send_error_response(retry_exc, retry_error_body)
                except Exception as retry_exc:
                    status = 502
                    message = json.dumps(
                        {
                            "error": {
                                "message": (
                                    f"upstream returned HTTP {exc.code}; retry strategy {retry_strategy} also failed: "
                                    f"{retry_exc}"
                                )
                            }
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(message)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(message)
            else:
                self._send_error_response(exc, error_body)
        except Exception as exc:
            message = json.dumps({"error": {"message": str(exc)}}, ensure_ascii=False).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(message)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(message)
        finally:
            duration_ms = int((time.time() - started) * 1000)
            self.proxy.write_log(
                method=self.command.upper(),
                path=self.path,
                status=status,
                removed_count=removed_count,
                tool_choice_changed=tool_choice_changed,
                converted_to_chat=converted_to_chat,
                model=model,
                dropped_tools=dropped_tools,
                forwarded_tools=forwarded_tools,
                retried_without_tools=retried_without_tools,
                retry_strategy=retry_strategy,
                duration_ms=duration_ms,
            )

    def _read_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        if "chunked" in transfer_encoding.lower():
            chunks: list[bytes] = []
            while True:
                size_line = self.rfile.readline()
                if not size_line:
                    break
                size_text = size_line.split(b";", 1)[0].strip()
                try:
                    size = int(size_text, 16)
                except ValueError:
                    break
                if size == 0:
                    # Consume trailer headers until the blank line.
                    while True:
                        trailer_line = self.rfile.readline()
                        if trailer_line in (b"\r\n", b"\n", b""):
                            break
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)  # trailing CRLF
            return b"".join(chunks)

        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            size = int(length)
        except ValueError:
            return b""
        return self.rfile.read(size)

    def _forward_headers(self, body_length: int | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP_HEADERS:
                headers[key] = value
        if should_override_user_agent(headers.get("User-Agent") or ""):
            headers["User-Agent"] = DEFAULT_USER_AGENT
        headers["Accept"] = headers.get("Accept") or "application/json"
        headers["Connection"] = "close"
        if self.proxy.upstream_api_key:
            headers["Authorization"] = f"Bearer {self.proxy.upstream_api_key}"
        if body_length is not None:
            headers["Content-Length"] = str(body_length)
        return headers

    def _copy_response_headers(self, response: Any, body_length: int | None, streaming: bool) -> None:
        for key, value in response.headers.items():
            lower = key.lower()
            if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                continue
            self.send_header(key, value)
        if body_length is not None:
            self.send_header("Content-Length", str(body_length))
        if streaming:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")

    def _send_buffered_response(self, response: Any, body: bytes) -> None:
        self.send_response(int(response.status))
        self._copy_response_headers(response, len(body), streaming=False)
        self.end_headers()
        self.wfile.write(body)

    def _send_streaming_response(self, response: Any) -> None:
        self.send_response(int(response.status))
        self._copy_response_headers(response, None, streaming=True)
        self.end_headers()
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()

    def _send_error_response(self, exc: urllib.error.HTTPError, body: bytes) -> None:
        self.send_response(int(exc.code))
        self._copy_response_headers(exc, len(body), streaming=False)
        self.end_headers()
        self.wfile.write(body)

    def _send_synthetic_json_response(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_synthetic_responses_stream(self, status: int, body: bytes) -> None:
        response_obj = json.loads(body.decode("utf-8"))
        created_response = dict(response_obj)
        created_response["status"] = "in_progress"
        created_response["output"] = []

        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def send_event(event: dict[str, Any]) -> None:
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
            self.wfile.flush()

        send_event({"type": "response.created", "response": created_response})
        output = response_obj.get("output")
        if isinstance(output, list):
            for index, item in enumerate(output):
                if not isinstance(item, dict):
                    continue
                added_item = dict(item)
                if added_item.get("status") == "completed":
                    added_item["status"] = "in_progress"
                send_event({"type": "response.output_item.added", "output_index": index, "item": added_item})
                if item.get("type") == "function_call":
                    send_event(
                        {
                            "type": "response.function_call_arguments.done",
                            "item_id": item.get("id"),
                            "output_index": index,
                            "arguments": item.get("arguments", "{}"),
                        }
                    )
                elif item.get("type") == "custom_tool_call":
                    send_event(
                        {
                            "type": "response.custom_tool_call_input.delta",
                            "item_id": item.get("id"),
                            "call_id": item.get("call_id"),
                            "output_index": index,
                            "delta": item.get("input", ""),
                        }
                    )
                send_event({"type": "response.output_item.done", "output_index": index, "item": item})
        send_event({"type": "response.completed", "response": response_obj})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_chat_stream_as_responses(self, response: Any, model: str) -> None:
        response_id = f"resp_proxy_{int(time.time() * 1000)}"
        message_id = f"msg_proxy_{int(time.time() * 1000)}"
        accumulated: list[str] = []

        self.send_response(int(response.status))
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def send_event(event: dict[str, Any]) -> None:
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
            self.wfile.flush()

        send_event(
            {
                "type": "response.created",
                "response": make_response_object(
                    response_id=response_id,
                    message_id=message_id,
                    model=model,
                    content="",
                    status="in_progress",
                ),
            }
        )
        send_event(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": message_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            }
        )
        send_event(
            {
                "type": "response.content_part.added",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }
        )

        for raw_line in response:
            line = raw_line.strip()
            if not line or not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                chunk = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                accumulated.append(content)
                send_event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": content,
                    }
                )

        text = "".join(accumulated)
        send_event(
            {
                "type": "response.output_text.done",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "text": text,
            }
        )
        send_event(
            {
                "type": "response.content_part.done",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": text, "annotations": []},
            }
        )
        item = {
            "id": message_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        send_event({"type": "response.output_item.done", "output_index": 0, "item": item})
        send_event(
            {
                "type": "response.completed",
                "response": make_response_object(
                    response_id=response_id,
                    message_id=message_id,
                    model=model,
                    content=text,
                    status="completed",
                ),
            }
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class ProxyServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        upstream_base_url: str,
        log_path: Path,
        timeout_seconds: int,
        upstream_config: dict[str, Any] | None = None,
        tool_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(server_address, ResponsesProxyHandler)
        self.upstream_base_url = upstream_base_url
        self.upstream_config = upstream_config or {}
        self.upstream_api_key = str(self.upstream_config.get("api_key") or "")
        self.default_protocol = str(self.upstream_config.get("protocol") or "openai_chat_completions")
        self.configured_models = model_entries_from_upstream(self.upstream_config)
        self.expose_upstream_models = bool(self.upstream_config.get("expose_upstream_models", False))
        self.block_native_models = bool(self.upstream_config.get("block_native_models", False))
        fallback = tool_config.get("tool_fallback") if isinstance(tool_config, dict) else {}
        if not isinstance(fallback, dict):
            fallback = {}
        self.allow_no_tools_retry = bool(fallback.get("allow_no_tools_retry", False))
        common = tool_config.get("common_plugins_only") if isinstance(tool_config, dict) else {}
        if not isinstance(common, dict):
            common = {}
        configured_keywords = common.get("include_keywords")
        if isinstance(configured_keywords, list):
            self.common_tool_keywords = tuple(str(item).lower() for item in configured_keywords if str(item).strip())
        else:
            self.common_tool_keywords = DEFAULT_COMMON_TOOL_KEYWORDS
        configured_exclude_keywords = common.get("exclude_keywords")
        if isinstance(configured_exclude_keywords, list):
            self.common_tool_exclude_keywords = tuple(
                str(item).lower() for item in configured_exclude_keywords if str(item).strip()
            )
        else:
            self.common_tool_exclude_keywords = ()
        self.log_path = log_path
        self.timeout_seconds = timeout_seconds
        self._log_lock = threading.Lock()
        self._model_cache_lock = threading.Lock()
        self._model_cache: dict[str, tuple[float, bytes]] = {}

    def should_use_chat_completions(self, model: str) -> bool:
        model_config = model_config_for_name(self.upstream_config, model)
        if is_native_gpt_model(model) and not bool(model_config.get("allow_chat_completions_conversion", False)):
            return False
        protocol = model_config.get("protocol") or self.default_protocol
        if protocol:
            return protocol_is_chat_completions(protocol)
        return should_use_chat_completions(model)

    def should_block_native_model(self, model: str) -> bool:
        return (
            self.block_native_models
            and is_native_gpt_model(model)
            and not model_is_explicitly_configured(self.upstream_config, model)
        )

    def tool_strategy_for_model(self, model: str) -> str:
        model_config = model_config_for_name(self.upstream_config, model)
        return normalize_tool_strategy(model_config.get("tool_strategy") or self.upstream_config.get("tool_strategy"))

    def get_model_cache(self, path: str) -> bytes | None:
        with self._model_cache_lock:
            cached = self._model_cache.get(path)
            if cached is None:
                return None
            expires_at, body = cached
            if time.time() >= expires_at:
                self._model_cache.pop(path, None)
                return None
            return body

    def set_model_cache(self, path: str, body: bytes) -> None:
        with self._model_cache_lock:
            self._model_cache[path] = (time.time() + MODEL_CACHE_TTL_SECONDS, body)

    def write_log(
        self,
        *,
        method: str,
        path: str,
        status: int,
        removed_count: int,
        tool_choice_changed: bool,
        duration_ms: int,
        converted_to_chat: bool = False,
        model: str = "",
        dropped_tools: int = 0,
        forwarded_tools: int = 0,
        retried_without_tools: bool = False,
        retry_strategy: str = "",
    ) -> None:
        line = (
            f"{utc_now()} method={method} path={path} status={status} "
            f"removed_image_generation={removed_count > 0} removed_count={removed_count} "
            f"tool_choice_changed={tool_choice_changed} converted_to_chat={converted_to_chat} "
            f"model={model or '-'} forwarded_tools={forwarded_tools} "
            f"dropped_tools={dropped_tools} retried_without_tools={retried_without_tools} "
            f"retry_strategy={retry_strategy or '-'} "
            f"duration_ms={duration_ms}\n"
        )
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex ModelX local /v1/responses bridge")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to modelx.config.json")
    parser.add_argument("--tools-config", default="", help="Path to tools.common.json")
    parser.add_argument("--listen-host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--upstream-base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).expanduser()
    config = load_json_file(config_path)
    upstream_config = active_upstream_from_config(config)
    proxy_config = config.get("proxy") if isinstance(config.get("proxy"), dict) else {}

    tools_config_path = Path(args.tools_config).expanduser() if args.tools_config else config_path.with_name("tools.common.json")
    tool_config = load_json_file(tools_config_path)

    listen_host = args.listen_host or str(proxy_config.get("host") or "127.0.0.1")
    port = int(args.port or proxy_config.get("port") or 17891)
    upstream_base_url = args.upstream_base_url or str(upstream_config.get("base_url") or "")
    if not upstream_base_url:
        raise SystemExit(
            f"No upstream base_url configured. Run configure.py or edit {config_path}."
        )
    if args.api_key:
        upstream_config["api_key"] = args.api_key

    default_log = Path(__file__).resolve().parents[1] / "logs" / "proxy.log"
    log_path = Path(args.log).expanduser() if args.log else default_log
    server = ProxyServer(
        (listen_host, port),
        upstream_base_url=upstream_base_url,
        log_path=log_path,
        timeout_seconds=args.timeout_seconds,
        upstream_config=upstream_config,
        tool_config=tool_config,
    )
    server.write_log(
        method="START",
        path=f"http://{listen_host}:{port}/v1 -> {upstream_base_url}",
        status=0,
        removed_count=0,
        tool_choice_changed=False,
        duration_ms=0,
    )
    print(f"Codex ModelX proxy listening on http://{listen_host}:{port}/v1", flush=True)
    print(f"Forwarding to {upstream_base_url}", flush=True)
    print(f"Config: {config_path}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
