#!/usr/bin/env python3
"""Back up Codex config.toml for advanced setup mode."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up Codex config.toml")
    parser.add_argument("--config", default=str(Path.home() / ".codex" / "config.toml"))
    parser.add_argument("--backup-dir", default=str(Path(__file__).resolve().parents[1] / "state" / "backups"))
    args = parser.parse_args()
    source = Path(args.config).expanduser()
    if not source.exists():
        raise SystemExit(f"Config not found: {source}")
    backup_dir = Path(args.backup_dir).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"config.toml.{stamp}.bak"
    shutil.copy2(source, dest)
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
