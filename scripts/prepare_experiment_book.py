# -*- coding: utf-8 -*-
"""长程实验准备：样本小说切章入库 + 真实模型开局蒸馏（生成前 3 章锚点）。

密钥经环境变量 REAL_ANTHROPIC_KEY 提供，不落盘。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import fate_engine as fe  # noqa: E402
from core.engine.chapter_tools import split_book  # noqa: E402
from core.engine.opening_distill import run_opening_pipeline  # noqa: E402
from core.services.native_gateway import native_complete  # noqa: E402

BOOK_ID = "sample_linyuan"


def main() -> int:
    key = os.environ["REAL_ANTHROPIC_KEY"]
    model_name = os.environ.get("REAL_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    base = os.environ.get("REAL_ANTHROPIC_BASE") or None
    client = fe.make_client(key, "anthropic", base)

    def model(prompt: str) -> str:
        result = native_complete(client, "anthropic", model_name, prompt,
                                 max_tokens=6000, timeout=180.0)
        return result.text

    source_path = ROOT / "data" / "samples" / "sample_novel.txt"
    source = source_path.read_text(encoding="utf-8")
    index = split_book(source, BOOK_ID, output_root=str(Path(fe.WRITABLE_DIR)))
    book_dir = Path(fe.WRITABLE_DIR) / "books" / BOOK_ID
    print(json.dumps({"book_id": BOOK_ID, "chapters": len(index["chapters"])},
                     ensure_ascii=False), flush=True)

    t0 = time.monotonic()
    report = run_opening_pipeline(book_dir, "临渊城的异客", model)
    elapsed = time.monotonic() - t0
    anchors = report.get("anchors") or []
    out = {"book_id": BOOK_ID, "elapsed_s": round(elapsed, 1),
           "ok": report.get("ok"), "anchors": anchors,
           "errors": report.get("errors"),
           "character_count": report.get("character_saved_count")}
    (ROOT / "artifacts" / "real_acceptance" / "book_distill_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    anchor_files = sorted((book_dir / "anchors").glob("*.json"))
    print("anchor files:", [p.name for p in anchor_files], flush=True)
    return 0 if report.get("ok") and anchor_files else 1


if __name__ == "__main__":
    sys.exit(main())
