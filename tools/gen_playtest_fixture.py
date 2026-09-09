# -*- coding: utf-8 -*-
"""一次性夹具生成：把矩阵测试的合成书写到指定路径，供 PLAYTEST_TXT 使用。

用法：python tools/gen_playtest_fixture.py <输出路径>
内容与 tests 夹具同源（李青/白芷/北墙/旧册 世界观），不含任何私有资产。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_mode_matrix import build_synthetic_book


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python tools/gen_playtest_fixture.py <输出路径>", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    text = build_synthetic_book()
    target.write_text(text, encoding="utf-8")
    print(f"fixture: {target} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
