#!/usr/bin/env python3
"""Generate a safe Codex ModelX model catalog.

Default behavior is deliberately non-destructive:

- query the local ModelX proxy for available upstream models;
- build a schema-compatible preview catalog under the Skill state directory;
- do not modify config.toml unless --install is explicitly passed.

This avoids the previous failure mode where an invalid model_catalog_json made
Codex Desktop reload a different/empty state and appear to lose conversations or
sandbox context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SKILL_ROOT = Path(__file__).resolve().parents[1]
CODEX_HOME = Path.home() / ".codex"
DEFAULT_PROXY_BASE = "http://127.0.0.1:17891/v1"
DEFAULT_OFFICIAL_CATALOG = CODEX_HOME / "model-catalog.gpt-5.5.json"
DEFAULT_PREVIEW_CATALOG = SKILL_ROOT / "state" / "model-catalog.codex-modelx.preview.json"
DEFAULT_INSTALL_CATALOG = CODEX_HOME / "model-catalog.codex-modelx.json"
CONFIG_PATH = CODEX_HOME / "config.toml"
BACKUP_DIR = CODEX_HOME / "backups"

GPT_PREFIXES = ("gpt", "o1", "o3", "o4", "o5")
ALWAYS_EXCLUDE_PREFIXES = ("sora",)
COMMON_THIRD_PARTY_PREFIXES = ("mimo", "qwen", "kimi", "moonshot", "deepseek", "glm")
EXPERIMENTAL_THIRD_PARTY_PREFIXES = ("grok",)
EXCLUDE_NAME_PARTS = (
    "tts",
    "voice",
    "audio",
    "image",
    "embedding",
    "embed",
    "rerank",
    "whisper",
)


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_no_bom(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def model_slug(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return ""
    for key in ("slug", "id", "name"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def display_name(entry: Dict[str, Any], fallback: str) -> str:
    for key in ("display_name", "displayName", "name", "id", "slug"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def fetch_proxy_models(proxy_base: str, timeout: int = 20) -> Dict[str, Any]:
    url = proxy_base.rstrip("/") + "/models?client_version=0.135.0&catalog_refresh=1"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer dummy-key"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_native_gpt(slug: str) -> bool:
    value = slug.lower()
    return value.startswith(GPT_PREFIXES)


def is_always_excluded(slug: str) -> bool:
    value = slug.lower()
    return value.startswith(ALWAYS_EXCLUDE_PREFIXES) or any(part in value for part in EXCLUDE_NAME_PARTS)


def is_common_third_party(slug: str) -> bool:
    value = slug.lower()
    return value.startswith(COMMON_THIRD_PARTY_PREFIXES)


def is_experimental_third_party(slug: str) -> bool:
    value = slug.lower()
    return value.startswith(EXPERIMENTAL_THIRD_PARTY_PREFIXES)


def configured_model_names() -> List[str]:
    config_path = SKILL_ROOT / "assets" / "config" / "modelx.config.json"
    if not config_path.exists():
        return []
    try:
        config = read_json(config_path)
    except Exception:
        return []
    active_name = config.get("active_upstream") or "default"
    upstreams = config.get("upstreams")
    if not isinstance(upstreams, list):
        return []
    active = None
    for upstream in upstreams:
        if isinstance(upstream, dict) and upstream.get("name") == active_name:
            active = upstream
            break
    if active is None:
        active = upstreams[0] if upstreams and isinstance(upstreams[0], dict) else {}
    names: List[str] = []
    for item in active.get("models", []) if isinstance(active, dict) else []:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def should_include_third_party(slug: str, mode: str, configured: Iterable[str]) -> bool:
    if not slug or is_native_gpt(slug) or is_always_excluded(slug):
        return False
    configured_set = {item.lower() for item in configured}
    lower = slug.lower()
    if mode == "configured":
        return lower in configured_set
    if mode == "common":
        return lower in configured_set or is_common_third_party(slug)
    if mode == "common_plus_experimental":
        return lower in configured_set or is_common_third_party(slug) or is_experimental_third_party(slug)
    if mode == "all":
        return True
    raise ValueError("unknown include mode: %s" % mode)


def clean_catalog_model(entry: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(entry)
    # model_catalog_json is parsed by a stricter Rust type than /v1/models.
    # Keep the official schema shape and omit service tier structs unless we
    # know the exact object format.
    cleaned.pop("id", None)
    cleaned.pop("service_tiers", None)
    cleaned.pop("default_service_tier", None)
    return cleaned


def make_template(official_models: List[Dict[str, Any]]) -> Dict[str, Any]:
    for preferred in ("gpt-5.4", "gpt-5.5", "gpt-5.4-mini"):
        for entry in official_models:
            if model_slug(entry) == preferred:
                return clean_catalog_model(entry)
    if official_models:
        return clean_catalog_model(official_models[0])
    # Minimal fallback, used only if the official catalog is missing.
    return {
        "slug": "template",
        "display_name": "template",
        "description": "Template model.",
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
        "priority": 100,
        "additional_speed_tiers": [],
        "availability_nux": {"message": ""},
        "upgrade": None,
        "supports_reasoning_summaries": True,
        "default_reasoning_summary": "none",
        "support_verbosity": True,
        "default_verbosity": "low",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": False,
        "supports_image_detail_original": False,
        "context_window": 128000,
        "max_context_window": 128000,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
    }


def build_catalog(
    official_catalog: Dict[str, Any],
    proxy_catalog: Dict[str, Any],
    include_mode: str,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    official_models_raw = official_catalog.get("models") or []
    official_models = [entry for entry in official_models_raw if isinstance(entry, dict)]
    proxy_models = [entry for entry in (proxy_catalog.get("models") or []) if isinstance(entry, dict)]
    configured = configured_model_names()

    models: List[Dict[str, Any]] = []
    seen = set()

    # Keep official GPT entries first to avoid breaking native GPT UX.
    for entry in official_models:
        slug = model_slug(entry)
        if not slug or slug in seen:
            continue
        cleaned = clean_catalog_model(entry)
        models.append(cleaned)
        seen.add(slug)

    template = make_template(official_models)
    included_third_party: List[str] = []
    skipped: List[str] = []

    for entry in proxy_models:
        slug = model_slug(entry)
        if not slug or slug in seen:
            continue
        if should_include_third_party(slug, include_mode, configured):
            model = clean_catalog_model(template)
            model["slug"] = slug
            model["display_name"] = display_name(entry, slug)
            model["description"] = "Third-party model routed through Codex ModelX local proxy."
            model["priority"] = 20 + len(included_third_party)
            model["additional_speed_tiers"] = []
            model["input_modalities"] = ["text"]
            model["supports_search_tool"] = False
            model["supports_parallel_tool_calls"] = False
            model["supports_image_detail_original"] = False
            model["context_window"] = int(entry.get("context_window") or 128000)
            model["max_context_window"] = int(entry.get("max_context_window") or model["context_window"])
            models.append(model)
            included_third_party.append(slug)
            seen.add(slug)
        elif not is_native_gpt(slug):
            skipped.append(slug)

    catalog = {
        "fetched_at": utc_now(),
        "client_version": official_catalog.get("client_version") or "0.135.0",
        "models": models,
    }
    return catalog, included_third_party, skipped


def validate_catalog(catalog: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    models = catalog.get("models")
    if not isinstance(models, list) or not models:
        errors.append("catalog.models must be a non-empty list")
        return errors
    seen = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            errors.append("models[%d] is not an object" % index)
            continue
        slug = model.get("slug")
        if not isinstance(slug, str) or not slug:
            errors.append("models[%d] missing slug" % index)
        elif slug in seen:
            errors.append("duplicate slug: %s" % slug)
        else:
            seen.add(slug)
        if "service_tiers" in model:
            errors.append("%s has service_tiers; omit in model_catalog_json until exact struct is known" % slug)
        if "default_service_tier" in model:
            errors.append("%s has default_service_tier; omit in model_catalog_json until exact struct is known" % slug)
        if not isinstance(model.get("display_name"), str):
            errors.append("%s missing display_name" % slug)
        if not isinstance(model.get("supported_reasoning_levels"), list):
            errors.append("%s missing supported_reasoning_levels list" % slug)
    return errors


def backup_file(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / ("%s.%s.bak" % (label, now_stamp()))
    shutil.copy2(path, backup)
    return backup


def toml_path_string(path: Path) -> str:
    # Use forward slashes to avoid TOML backslash escapes such as \U.
    return path.resolve().as_posix()


def install_catalog(preview_path: Path, install_path: Path) -> None:
    install_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(preview_path, install_path)
    backup = backup_file(CONFIG_PATH, "config.toml.before-modelx-catalog-install")
    text = CONFIG_PATH.read_text(encoding="utf-8-sig") if CONFIG_PATH.exists() else ""
    line = 'model_catalog_json = "%s"' % toml_path_string(install_path)
    if re.search(r"(?m)^\s*model_catalog_json\s*=", text):
        text = re.sub(r"(?m)^\s*model_catalog_json\s*=.*$", line, text, count=1)
    else:
        lines = text.splitlines()
        insert_at = 0
        for index, existing in enumerate(lines):
            if re.match(r"\s*model_reasoning_effort\s*=", existing):
                insert_at = index + 1
                break
            if re.match(r"\s*model\s*=", existing):
                insert_at = index + 1
        lines.insert(insert_at, line)
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    CONFIG_PATH.write_text(text, encoding="utf-8")
    print("installed_catalog=%s" % install_path)
    if backup:
        print("backup_config=%s" % backup)


def uninstall_catalog() -> None:
    if not CONFIG_PATH.exists():
        print("config_not_found=%s" % CONFIG_PATH)
        return
    backup = backup_file(CONFIG_PATH, "config.toml.before-modelx-catalog-uninstall")
    text = CONFIG_PATH.read_text(encoding="utf-8-sig")
    text = re.sub(r"(?m)^\s*model_catalog_json\s*=.*\n?", "", text)
    CONFIG_PATH.write_text(text, encoding="utf-8")
    print("removed model_catalog_json from %s" % CONFIG_PATH)
    if backup:
        print("backup_config=%s" % backup)


def print_current_config_status() -> None:
    if not CONFIG_PATH.exists():
        print("config_exists=false")
        return
    raw = CONFIG_PATH.read_bytes()
    print("config_exists=true")
    print("config_starts_with_bom=%s" % raw.startswith(b"\xef\xbb\xbf"))
    text = raw.decode("utf-8-sig", errors="replace")
    match = re.search(r"(?m)^\s*model_catalog_json\s*=\s*['\"]?([^'\"\n]+)['\"]?", text)
    if not match:
        print("model_catalog_json=<not set>")
        return
    raw_path = match.group(1).strip()
    print("model_catalog_json=%s" % raw_path)
    path = Path(raw_path)
    print("catalog_exists=%s" % path.exists())
    if path.exists():
        try:
            catalog = read_json(path)
            errors = validate_catalog(catalog)
            print("catalog_model_count=%s" % len(catalog.get("models") or []))
            print("catalog_valid=%s" % (not errors))
            for error in errors:
                print("catalog_error=%s" % error)
        except Exception as exc:
            print("catalog_parse_error=%s" % exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or manage a safe Codex ModelX model catalog")
    parser.add_argument("--proxy-base", default=DEFAULT_PROXY_BASE)
    parser.add_argument("--official-catalog", default=str(DEFAULT_OFFICIAL_CATALOG))
    parser.add_argument("--output", default=str(DEFAULT_PREVIEW_CATALOG))
    parser.add_argument("--install-path", default=str(DEFAULT_INSTALL_CATALOG))
    parser.add_argument(
        "--include",
        choices=["configured", "common", "common_plus_experimental", "all"],
        default="common",
        help="Which third-party models to include in the generated catalog.",
    )
    parser.add_argument("--install", action="store_true", help="Install generated catalog and set model_catalog_json")
    parser.add_argument("--uninstall", action="store_true", help="Remove model_catalog_json from config.toml")
    parser.add_argument("--check-current", action="store_true", help="Only inspect current model_catalog_json status")
    args = parser.parse_args()

    if args.check_current:
        print_current_config_status()
        return 0
    if args.uninstall:
        uninstall_catalog()
        return 0

    official_path = Path(args.official_catalog)
    if not official_path.exists():
        print("warning=official catalog missing, using minimal fallback: %s" % official_path)
        official_catalog: Dict[str, Any] = {"client_version": "0.135.0", "models": []}
    else:
        official_catalog = read_json(official_path)

    try:
        proxy_catalog = fetch_proxy_models(args.proxy_base)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print("error=failed to fetch proxy models: %s" % exc, file=sys.stderr)
        return 2

    catalog, included, skipped = build_catalog(official_catalog, proxy_catalog, args.include)
    errors = validate_catalog(catalog)
    output = Path(args.output)
    write_json_no_bom(output, catalog)

    print("preview_catalog=%s" % output)
    print("catalog_model_count=%s" % len(catalog.get("models") or []))
    print("included_third_party=%s" % ", ".join(included))
    print("skipped_non_gpt=%s" % ", ".join(skipped))
    print("catalog_valid=%s" % (not errors))
    for error in errors:
        print("catalog_error=%s" % error)

    if errors:
        print("not installing because catalog validation failed", file=sys.stderr)
        return 3

    if args.install:
        install_catalog(output, Path(args.install_path))
    else:
        print("not_installed=true")
        print("install_hint=python scripts/generate_catalog.py --install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
