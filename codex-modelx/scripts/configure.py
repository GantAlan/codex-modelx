#!/usr/bin/env python3
"""Configure Codex ModelX for one upstream relay."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "assets" / "config"
CONFIG_PATH = CONFIG_DIR / "modelx.config.json"
EXAMPLE_PATH = CONFIG_DIR / "modelx.config.example.json"
STATE_DIR = ROOT / "state"
CODEX_FRAGMENT_PATH = STATE_DIR / "codex-config-fragment.toml"
CODEX_ROUTER_FRAGMENT_PATH = STATE_DIR / "codex-config-fragment-router-custom.toml"
CODEX_MODELX_FRAGMENT_PATH = STATE_DIR / "codex-config-fragment-add-modelx-optional.toml"

PROTOCOLS = {
    "1": "openai_chat_completions",
    "2": "openai_responses",
    "3": "anthropic_compatible",
    "openai_chat_completions": "openai_chat_completions",
    "chat": "openai_chat_completions",
    "chat_completions": "openai_chat_completions",
    "openai_responses": "openai_responses",
    "responses": "openai_responses",
    "anthropic": "anthropic_compatible",
    "anthropic_compatible": "anthropic_compatible",
}


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def prompt_value(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def normalize_protocol(raw: str) -> str:
    key = raw.strip().lower()
    if key not in PROTOCOLS:
        raise SystemExit(
            "Unsupported protocol. Use 1, 2, 3, openai_chat_completions, openai_responses, or anthropic_compatible."
        )
    return PROTOCOLS[key]


def list_models(base_url: str, api_key: str, timeout: int = 20) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[WARN] Could not fetch /v1/models from upstream: {exc}")
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for item in data:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            value = item.get("id") or item.get("name") or item.get("slug")
            if isinstance(value, str) and value:
                names.append(value)
    return names


def choose_model(models: list[str], default: str = "") -> str:
    if models:
        print("\nDetected models / ??????:")
        for idx, model in enumerate(models[:30], start=1):
            print(f"  {idx}. {model}")
        raw = prompt_value("Choose model number or type model name / ????????", default or "1")
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(models[:30]):
                return models[index - 1]
        return raw
    return prompt_value("Model name / ???", default or "mimo-v2.5")


def write_codex_fragment(host: str, port: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    router_fragment = (
        "# Recommended smart-router mode: keep provider name custom, but point it to the local ModelX router.\n"
        "# GPT models pass through as Responses. MiMo/Qwen/Kimi/DeepSeek/GLM convert to Chat Completions.\n"
        "# ??????????? provider ?? custom??? custom.base_url ???? ModelX ???\n"
        "# GPT ??? Responses?MiMo/Qwen/Kimi/DeepSeek/GLM ???? Chat Completions?\n"
        "model_provider = \"custom\"\n"
        "model = \"gpt-5.5\"\n"
        "\n"
        "[model_providers.custom]\n"
        "name = \"custom\"\n"
        f"base_url = \"http://{host}:{port}/v1\"\n"
        "wire_api = \"responses\"\n"
        "requires_openai_auth = true\n"
        "experimental_bearer_token = \"dummy-key\"\n"
    )
    optional_modelx_fragment = (
        "# Optional explicit provider. Desktop may not merge this provider into the current model picker.\n"
        "# ???? provider?Desktop ?????????????????? provider ????\n"
        "[model_providers.modelx]\n"
        "name = \"modelx\"\n"
        f"base_url = \"http://{host}:{port}/v1\"\n"
        "wire_api = \"responses\"\n"
        "requires_openai_auth = true\n"
        "experimental_bearer_token = \"dummy-key\"\n"
    )
    CODEX_ROUTER_FRAGMENT_PATH.write_text(router_fragment, encoding="utf-8")
    CODEX_MODELX_FRAGMENT_PATH.write_text(optional_modelx_fragment, encoding="utf-8")
    CODEX_FRAGMENT_PATH.write_text(router_fragment, encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Configure Codex ModelX")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--protocol", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--tool-strategy", default="full_tools", choices=["full_tools", "common_plugins_only", "no_tools"])
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    template = read_json(EXAMPLE_PATH) if EXAMPLE_PATH.exists() else {}
    proxy = template.get("proxy") if isinstance(template.get("proxy"), dict) else {"host": "127.0.0.1", "port": 17891}

    base_url = args.base_url or (
        "" if args.non_interactive else prompt_value("Base URL, usually ending with /v1 / ?? Base URL", "https://your-api.example.com/v1")
    )
    api_key = args.api_key or ("" if args.non_interactive else prompt_value("API Key / ??", "paste-your-api-key-here"))
    protocol_raw = args.protocol or (
        "openai_chat_completions"
        if args.non_interactive
        else prompt_value("Protocol: 1=openai_chat_completions, 2=openai_responses, 3=anthropic_compatible", "1")
    )
    protocol = normalize_protocol(protocol_raw)

    detected = list_models(base_url, api_key) if base_url and api_key and "your-api.example.com" not in base_url else []
    model = args.model or ("mimo-v2.5" if args.non_interactive else choose_model(detected, "mimo-v2.5"))

    config = {
        "proxy": proxy,
        "active_upstream": "default",
        "upstreams": [
            {
                "name": "default",
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "protocol": protocol,
                "tool_strategy": args.tool_strategy,
                "expose_upstream_models": True,
                "block_native_models": False,
                "models": [
                    {"name": model, "protocol": protocol, "tool_strategy": args.tool_strategy}
                ],
            }
        ],
        "tool_fallback": {
            "on_full_tools_failed": "common_plugins_only",
            "allow_no_tools_retry": False,
            "allow_gpt_fallback": False,
        },
    }
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_codex_fragment(str(proxy.get("host") or "127.0.0.1"), int(proxy.get("port") or 17891))

    print(f"\n[OK] Wrote config: {CONFIG_PATH}")
    print(f"[OK] Wrote recommended Codex config fragment: {CODEX_ROUTER_FRAGMENT_PATH}")
    print(f"[OK] Wrote optional modelx-provider fragment: {CODEX_MODELX_FRAGMENT_PATH}")
    print("\nWARNING / ???API Key is stored in plain text. Do not share modelx.config.json.")
    print("Next / ????run scripts/start_proxy.ps1, then point custom.base_url to the local router.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
