"""修复 _drafts 内 background 字段含未转义 ASCII 引号导致的坏 JSON。一次性工具。"""
from __future__ import annotations

import json
from pathlib import Path

FILES = [
    Path("data/characters/_drafts/伙伴栏/r2-d2.json"),
    Path("data/characters/_drafts/伙伴栏/yu-xiulian.json"),
]
KEY = '"background": "'


def main() -> int:
    for p in FILES:
        text = p.read_text(encoding="utf-8")
        start = text.index(KEY) + len(KEY)
        end = text.index("\n", start)
        line = text[start:end]
        assert line.endswith('"'), line[-20:]
        value = line[:-1]  # 去掉收尾引号
        parts = value.split('"')
        rebuilt = parts[0]
        for i, seg in enumerate(parts[1:], start=1):
            rebuilt += ("“" if i % 2 == 1 else "”") + seg
        new_text = text[:start] + rebuilt + '"' + text[end:]
        json.loads(new_text)  # 先验证再落盘
        p.write_text(new_text, encoding="utf-8")
        print("fixed & parsed OK:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
