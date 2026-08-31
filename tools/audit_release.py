"""Fail closed when a public release directory contains private or runtime data."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

FORBIDDEN_TOP_LEVEL = {"ip_vault", "private-recovery", ".zcode", "outputs", "var", "archive"}
FORBIDDEN_RUNTIME_DIRS = {"sessions", "saves", "logs"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".jsonl", ".log", ".wal", ".shm", ".zip", ".7z", ".apk", ".aab"}


def violations(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        parts = relative.parts
        # Only inspect actual release/runtime layout, not third-party Python module names
        # such as openai.resources.uploads.
        if parts and parts[0] in FORBIDDEN_TOP_LEVEL:
            found.append(relative)
        elif len(parts) >= 2 and parts[0] == "_internal" and parts[1] in FORBIDDEN_TOP_LEVEL:
            found.append(relative)
        elif path.suffix.lower() in FORBIDDEN_SUFFIXES:
            # PyInstaller legitimately embeds stdlib in _internal/base_library.zip.
            # Public archive files themselves are only forbidden at release root.
            if path.suffix.lower() not in {".zip", ".7z"} or len(parts) == 1:
                found.append(relative)
        elif any(part in FORBIDDEN_RUNTIME_DIRS for part in parts[:2]):
            found.append(relative)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="检查公开发布目录是否夹带运行态或私有数据")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    root = args.path.resolve()
    if not root.is_dir():
        print(f"发布目录不存在：{root}", file=sys.stderr)
        return 2
    bad = violations(root)
    if bad:
        print("发布审计失败：发现禁止公开的文件或目录", file=sys.stderr)
        for item in bad:
            print(f"- {item}", file=sys.stderr)
        return 1
    print(f"发布审计通过：{root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
