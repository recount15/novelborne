"""草稿晋升整合器：评价通过 → 归一化 → 晋升 data/characters/builtin/<槽位>/。

- 评价文件：data/characters/_drafts/<槽位>/<id>.review.json
  {"scores": {"立体度":9,"动机自洽":8,"代入感":9,"行为边界":8}, "level": "normal|enhanced", "pass": true, "note": "..."}
- 通过线：normal 总分≥32 且单项≥7；enhanced 总分≥36 且单项≥8（规格 §8）
- 归一化：
  * source_medium 轻小说 还原：以分片清单 (name, work) 匹配，草案 medium=轻小说 但卡里被映射成「小说」的改回「轻小说」
  * 同名同出处（name+work+role 或 name+work）去重，保留评价分高者
  * slot_keys 键∈四栏、值数组
- 产出：晋升卡文件 + outputs/promote_report.json + 打印统计

用法：python tools/promote_drafts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "assets" / "data" / "characters" / "_drafts"
BUILTIN = ROOT / "assets" / "data" / "characters" / "builtin"
SHARDS = ROOT / "assets" / "data" / "roster" / "shards"
REPORT = ROOT / "outputs" / "promote_report.json"

PASS = {"normal": (32, 7), "enhanced": (36, 8)}


def _load_shard_mediums() -> dict[tuple[str, str], str]:
    """(name, work) → 草案原始 medium（用于轻小说还原）。"""
    out: dict[tuple[str, str], str] = {}
    for pattern in ("shard-*.json", "partner-shard-*.json"):
        for p in SHARDS.glob(pattern):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for c in data.get("cards", []):
                out[(str(c.get("name", "")), str(c.get("work", "")))] = str(
                    c.get("medium") or c.get("source_medium") or "")
    return out


def _find_review(card_path: Path) -> dict | None:
    rp = card_path.with_suffix(".review.json")
    if not rp.is_file():
        return None
    try:
        return json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _review_pass(review: dict) -> bool:
    scores = review.get("scores") or {}
    if not scores:
        return False
    total = sum(int(v) for v in scores.values())
    floor, per = PASS.get(review.get("level", "normal"), (32, 7))
    return total >= floor and all(int(v) >= per for v in scores.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = ap.parse_args()

    mediums = _load_shard_mediums()
    cards: list[tuple[Path, dict, dict | None]] = []
    for p in sorted(DRAFTS.glob("**/*.json")):
        if p.name.endswith(".review.json"):
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        cards.append((p, raw, _find_review(p)))

    promoted, failed_no_review, failed_score = [], [], []
    for path, raw, review in cards:
        if review is None:
            failed_no_review.append((path, "缺评价文件"))
            continue
        if not _review_pass(review):
            failed_score.append((path, f"分数不达标 {review.get('scores')}"))
            continue
        promoted.append((path, raw, review))

    # 归一化：轻小说还原 + enhanced 布尔补齐 + 去重（name+work 保留高分者）
    by_key: dict[tuple[str, str], tuple] = {}
    for path, raw, review in promoted:
        name = str(raw.get("name", ""))
        work = str(raw.get("work", ""))
        med = mediums.get((name, work), "")
        if med == "轻小说":
            raw["source_medium"] = "轻小说"
        if raw.get("distill_level") == "enhanced":
            raw.setdefault("enhanced", True)  # 补齐旧布尔字段（eval-8 上报的矛盾）
        total = sum(int(v) for v in (review.get("scores") or {}).values())
        key = (name, work)
        if key not in by_key or total > by_key[key][2]:
            by_key[key] = (path, raw, total)

    def slot_of(r: dict) -> str:
        return {"主角": "主角栏", "女主": "主线栏", "男主": "主线栏",
                "配角": "伙伴栏", "反派": "宿敌栏"}.get(
            str(r.get("original_position", "")), "伙伴栏")

    written, skipped_dup = [], []
    for (name, work), (path, raw, total) in by_key.items():
        dest = BUILTIN / slot_of(raw) / path.name
        if args.dry_run:
            written.append((path, dest, raw.get("role"), total))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 去重：目标已存在则比较分数（此处同一 (name,work) 只写一次）
        if dest.exists():
            skipped_dup.append((path, dest))
            continue
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(dest)
        written.append((path, dest, raw.get("role"), total))

    print(f"草稿卡: {len(cards)} | 通过评价: {len(promoted)} | 缺评价: {len(failed_no_review)} | "
          f"分数不达标: {len(failed_score)} | 晋升/去重后写入: {len(written)} | 目标已存在跳过: {len(skipped_dup)}")
    if not args.dry_run:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({
            "generated_at": datetime.now().isoformat(),
            "total_drafts": len(cards), "promoted": len(written),
            "no_review": [str(p) for p, _ in failed_no_review],
            "score_failed": [str(p) for p, _ in failed_score],
            "dups_skipped": [str(p) for p, _ in skipped_dup],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"报告已写 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
