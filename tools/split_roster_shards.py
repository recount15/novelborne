"""把 data/roster/final_roster.json 的待蒸馏候选切成 N 个分片，供蒸馏波并发使用。

- 存量 builtin 卡（origin=builtin）跳过：已在角色池内，无需重蒸。
- 分片均衡按剩余候选轮转分配；enhanced 均匀散布。
- 输出 data/roster/shards/shard-<nn>.json + 索引 manifest.json

用法：python tools/split_roster_shards.py [--shards 48]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "data" / "roster" / "final_roster.json"
OUT = ROOT / "assets" / "data" / "roster" / "shards"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", type=int, default=48)
    ap.add_argument("--slots", type=str, default="",
                    help="逗号分隔的槽位过滤（如 主角栏,主线栏,宿敌栏）；留空=全部")
    ap.add_argument("--prefix", type=str, default="",
                    help="分片文件名前缀，避免覆盖既有波次的分片")
    args = ap.parse_args()

    data = json.loads(SRC.read_text(encoding="utf-8"))
    wanted = {s.strip() for s in args.slots.split(",") if s.strip()}
    todo = [c for c in data["cards"]
            if c.get("origin") != "builtin" and (not wanted or slot_of(c) in wanted)]
    todo.sort(key=lambda c: (SLOT_ORDER.get(slot_of(c), 9), c["name"]))
    shards = [[] for _ in range(args.shards)]
    for i, c in enumerate(todo):  # 轮转保证均衡与 enhanced 散布
        shards[i % args.shards].append(c)

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for idx, shard in enumerate(shards, start=1):
        path = OUT / f"{args.prefix}shard-{idx:02d}.json"
        path.write_text(json.dumps({"shard": idx, "total": len(todo), "cards": shard},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        manifest.append({"shard": idx, "file": path.name, "count": len(shard),
                         "enhanced": sum(1 for c in shard if c.get("enhanced"))})
    (OUT / f"{args.prefix}manifest.json").write_text(json.dumps({"total_candidates": len(todo),
                                                   "shards": manifest},
                                                  ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"待蒸馏 {len(todo)} 张 → {args.shards} 分片（均 {len(todo)/args.shards:.1f} 张/片），已写入 {OUT}")
    return 0


SLOT_ORDER = {"主角栏": 0, "主线栏": 1, "伙伴栏": 2, "宿敌栏": 3}


def slot_of(card: dict) -> str:
    return {"主角": "主角栏", "女主": "主线栏", "男主": "主线栏",
            "配角": "伙伴栏", "反派": "宿敌栏"}.get(card.get("original_position", ""), "伙伴栏")


if __name__ == "__main__":
    raise SystemExit(main())
