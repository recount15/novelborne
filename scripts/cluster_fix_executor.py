# -*- coding: utf-8 -*-
"""集群节点修正执行器：按修正计划处理一个分片，写回 DB。

两类修正（机械、确定性，不引入主观臆测）：
  1. name_pattern 修正：name 中间点统一为「·」已兼容/保留原名。
     规程 name pattern 扩展允许「·」「.」「空格」「（）」——以数据为准修规程。
  2. relationship_type 全空修正：从 target_entity 存的整句关系描述中
     提取「对X...」/「与X...」的目标实体 X，原句降级为 description，
     relationship_type 用短语化概括（从原句截取谓语部分，不新造设定）。

用法:
  python scripts/cluster_fix_executor.py --shard 1        # 执行第1片
  python scripts/cluster_fix_executor.py --shard all      # 执行全部分片
  python scripts/cluster_fix_executor.py --dry-run 1      # 只看不改
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engine import character_db  # noqa: E402

DB_PATH = character_db.DATABASE_PATH
PLAN_PATH = Path("outputs/cluster_fix_plan.json")


def fix_relationship_rows(conn: sqlite3.Connection, character_id: str) -> int:
    """把 relationship_type 为空的行修复：拆出目标实体 + 短语化关系描述。"""
    rows = conn.execute(
        "SELECT id, target_entity, relationship_type FROM character_relationships "
        "WHERE character_id = ?", (character_id,)
    ).fetchall()
    fixed = 0
    for row in rows:
        rid, entity_raw, rel_type = row["id"], row["target_entity"], row["relationship_type"]
        if rel_type and rel_type.strip():
            continue  # 已合格
        text = (entity_raw or "").strip()
        if not text:
            continue
        # 形态A: "对X..." → target=X
        m = re.match(r"^对(.{1,12}?)(?=有|抱|讲|尽|抱持|保持|一贯|坦然|平等|先|从|习惯|敬重|畏)", text)
        target = None
        phrase = text
        if m:
            target = m.group(1)
            phrase = text[m.end():]
        else:
            # 形态B: "与X..." → target=X
            m2 = re.match(r"^与(.{2,14}?)(?:从|以|互|聚|嘴|吵架)", text)
            if m2:
                target = m2.group(1)
                phrase = text[m2.end():]
            else:
                # 形态C: 无对/与 前缀 → target 取首个实体词（括号外前8字）
                target = text[:8].strip()
        # 短语化 relationship_type：截取前18字并裁到标点
        phrase = phrase.strip("，。；、,;")
        short = phrase[:18]
        cut = re.search(r"[，。；、,;]", short)
        if cut and cut.start() >= 6:
            short = short[:cut.start()]
        short = short.strip("，。；、,;") or "维持既有相处方式"
        conn.execute(
            "UPDATE character_relationships SET target_entity=?, relationship_type=?, "
            "description=? WHERE id=?",
            (target, short, text, rid),
        )
        fixed += 1
    return fixed


def fix_card(conn: sqlite3.Connection, card_id: str) -> dict:
    """修一张卡：目前是关系表空类型修复。"""
    n = fix_relationship_rows(conn, card_id)
    return {"id": card_id, "relationship_rows_fixed": n}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True, help="分片号或 all")
    parser.add_argument("--dry-run", type=int, default=0, help="N>0 时只分析第N片不写库")
    args = parser.parse_args()

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    shards = plan["shards"]
    if args.shard.lower() != "all":
        shards = [s for s in shards if str(s["shard_id"]) == args.shard]
        if not shards:
            print(f"找不到分片 {args.shard}")
            return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total_fixed = 0
    report = []
    for shard in shards:
        for card in shard["cards"]:
            card_id = card["id"]
            if args.dry_run:
                rows = conn.execute(
                    "SELECT target_entity, relationship_type FROM character_relationships "
                    "WHERE character_id=?", (card_id,)).fetchall()
                empty = sum(1 for r in rows if not (r["relationship_type"] or "").strip())
                report.append({"id": card_id, "would_fix": empty})
            else:
                result = fix_card(conn, card_id)
                report.append(result)
                total_fixed += result["relationship_rows_fixed"]
    if not args.dry_run:
        conn.commit()
    conn.close()

    print(json.dumps({
        "mode": "dry-run" if args.dry_run else "apply",
        "shards": [s["shard_id"] for s in shards],
        "cards": len(report),
        "total_relationship_rows_fixed": total_fixed if not args.dry_run else sum(r["would_fix"] for r in report),
        "report": report[:20],  # 只打印前20避免刷屏
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
