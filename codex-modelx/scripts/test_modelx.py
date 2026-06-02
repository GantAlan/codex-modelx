#!/usr/bin/env python3
"""Layered smoke tests for Codex ModelX."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "assets" / "config" / "modelx.config.json"
LOG_DIR = ROOT / "logs"
DIAG_PATH = LOG_DIR / "diagnostics.md"


def read_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit("Config root must be an object")
    return data


def active_upstream(config: dict[str, Any]) -> dict[str, Any]:
    upstreams = config.get("upstreams")
    if isinstance(upstreams, list) and upstreams:
        return upstreams[0] if isinstance(upstreams[0], dict) else {}
    return {}


def first_model(upstream: dict[str, Any]) -> str:
    models = upstream.get("models")
    if isinstance(models, list) and models and isinstance(models[0], dict):
        return str(models[0].get("name") or "mimo-v2.5")
    return "mimo-v2.5"


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 120) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return int(response.status), parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), body
    except Exception as exc:
        return 0, str(exc)


def extract_text(response: Any) -> str:
    if not isinstance(response, dict):
        return str(response)
    output = response.get("output")
    parts: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
    if parts:
        return "".join(parts)
    return json.dumps(response, ensure_ascii=False)[:1000]


def first_function_call(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    output = response.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if isinstance(item, dict) and item.get("type") == "function_call":
            return item
    return None


def run_codex_exec(model: str, prompt: str, cwd: Path, timeout: int = 240) -> tuple[str, str]:
    codex = shutil.which("codex")
    if not codex:
        return "SKIP", "codex CLI not found on PATH"
    cmd = [
        codex, "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--ephemeral",
        "-m", model, "--cd", str(cwd), prompt,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as exc:
        return "FAIL", str(exc)
    status = "PASS" if result.returncode == 0 else "FAIL"
    return status, (result.stdout + "\n" + result.stderr).strip()[-4000:]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Test Codex ModelX proxy and optional plugin smoke tests")
    parser.add_argument("--model", default="")
    parser.add_argument("--run-codex-plugin-tests", action="store_true")
    args = parser.parse_args(argv)

    config = read_config()
    proxy = config.get("proxy") if isinstance(config.get("proxy"), dict) else {}
    host = str(proxy.get("host") or "127.0.0.1")
    port = int(proxy.get("port") or 17891)
    base = f"http://{host}:{port}"
    model = args.model or first_model(active_upstream(config))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Codex ModelX diagnostics", "", f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", f"Proxy: `{base}/v1`", f"Model: `{model}`", ""]

    status, body = http_json(base + "/__health")
    lines += ["## 1. Proxy health", "", f"Status: `{status}`", "", "```json", str(body), "```", ""]
    ok = status == 200

    payload = {
        "model": model,
        "instructions": "You are a smoke-test assistant. Do not call tools. Always answer in plain text.",
        "input": "Return exactly this plain text token and nothing else: MODELX_TEXT_OK",
        "tool_choice": "none",
        "max_output_tokens": 128,
        "stream": False,
    }
    status, body = http_json(base + "/v1/responses", payload)
    text = extract_text(body)
    text_ok = status == 200 and "MODELX_TEXT_OK" in text
    ok = ok and text_ok
    lines += ["## 2. Text response", "", f"Status: `{status}`", f"Result: `{'PASS' if text_ok else 'FAIL'}`", "", "```", text[:2000], "```", ""]

    tool_payload = {
        "model": model,
        "input": "Use the modelx_echo tool with text equal to MODELX_TOOL_OK.",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "modelx_echo",
                    "description": "Echo a short text value for smoke testing tool call compatibility.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "max_output_tokens": 128,
        "stream": False,
    }
    status, body = http_json(base + "/v1/responses", tool_payload)
    body_text = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
    call = first_function_call(body)
    closed_loop_text = ""
    closed_loop_status = 0
    if status == 200 and call:
        closed_payload = {
            "model": model,
            "input": [
                call,
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id") or call.get("id"),
                    "name": call.get("name") or "modelx_echo",
                    "output": "MODELX_TOOL_RESULT_OK",
                },
                "Now reply exactly MODELX_TOOL_CLOSED_OK.",
            ],
            "max_output_tokens": 64,
            "stream": False,
        }
        closed_loop_status, closed_body = http_json(base + "/v1/responses", closed_payload)
        closed_loop_text = extract_text(closed_body)
    tool_ok = (
        (status == 200 and call is not None and closed_loop_status == 200 and "MODELX_TOOL_CLOSED_OK" in closed_loop_text)
        or (status == 200 and "MODELX_TOOL_OK" in body_text)
    )
    lines += [
        "## 3. Basic function tool closed loop",
        "",
        f"Initial status: `{status}`",
        f"Function call detected: `{'yes' if call else 'no'}`",
        f"Closed-loop status: `{closed_loop_status}`",
        f"Result: `{'PASS' if tool_ok else 'WARN_OR_FAIL'}`",
        "",
        "```",
        (body_text[:2200] + "\n\nCLOSED_LOOP_TEXT:\n" + closed_loop_text[:800]),
        "```",
        "",
    ]

    lines += ["## 4. Plugin smoke tests", ""]
    if args.run_codex_plugin_tests:
        tests = [
            ("Chrome", "Open https://www.bilibili.com/ with Chrome and report the page title. End with PLUGIN_TEST chrome PASS or FAIL."),
            ("Zotero", "Use Zotero to list recent items or tags. End with PLUGIN_TEST zotero PASS or FAIL."),
            ("Presentations", "Create a one-slide PPTX in the current directory containing MODELX_PRESENTATIONS_OK. End with PLUGIN_TEST presentations PASS or FAIL."),
        ]
        for name, prompt in tests:
            status_name, output = run_codex_exec(model, prompt, ROOT)
            lines += [f"### {name}", "", f"Result: `{status_name}`", "", "```", output, "```", ""]
    else:
        lines += ["Plugin tests skipped. Re-run with `--run-codex-plugin-tests` inside Codex CLI to test Chrome, Zotero, and Presentations.", ""]

    DIAG_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Diagnostics written to {DIAG_PATH}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
