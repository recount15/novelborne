"""合并 data/roster_draft/*.json 与存量 character_pools.json → data/roster/final_roster.json

口径：docs/spec_character_db_expansion.md §3/§4
- (name, work) 去重；同位竞争时保留「slot 与 original_position 匹配」的条目
- 裸名重复打 name_collision_risk
- 运行期槽位由 original_position 派生：主角→主角栏 / 女主·男主→主线栏 / 配角→伙伴栏 / 反派→宿敌栏
- 产出覆盖面矩阵与目标达标表

用法：python tools/build_final_roster.py [--preview]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POOLS = ROOT / "assets" / "data" / "character_pools.json"
DRAFT_DIR = ROOT / "assets" / "data" / "roster_draft"
OUT = ROOT / "assets" / "data" / "roster" / "final_roster.json"

SLOT_OF_POSITION = {"主角": "主角栏", "女主": "主线栏", "男主": "主线栏",
                    "配角": "伙伴栏", "反派": "宿敌栏"}
TARGETS = {"主角": 30, "女主": 140, "配角": 320, "反派": 30}  # tagger 基数 × 倍数（主角15*2 女主28*5 配角40*8 反派6*5）

VALID_SLOT_KEYS = {
    "主角栏": {"逆袭成长型", "天命担当型", "反英雄型", "悲剧宿命型"},
    "伙伴栏": {"军师智囊", "守护护卫", "技术支援", "气氛担当", "道德锚点"},
    "主线栏": {"并肩作战型", "相互救赎型", "欢喜冤家型", "细水长流型", "事业共生型"},
    "宿敌栏": {"智斗博弈型", "武力压制型", "理念冲突型", "体制碾压型", "镜像宿命型",
             "人心腐蚀型", "天灾型"},
}


def _norm_gender(v: str) -> str:
    v = str(v or "").strip().lower()
    return {"m": "male", "f": "female", "男": "male", "女": "female"}.get(v, v or "unknown")


def _iter_draft(path: Path):
    """兼容两种草案结构：扁平 candidates 或 categories[].candidates。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = d.get("candidates")
    if rows is None:
        rows = []
        for cat in d.get("categories", []):
            for c in cat.get("candidates", []):
                c = dict(c)
                c.setdefault("key", cat.get("key", ""))
                c["new_category"] = bool(cat.get("new")) or c.get("new_category", False)
                rows.append(c)
    meta = {"file": path.name, "new_categories": d.get("new_categories", []),
            "merge_notes": d.get("merge_notes", [])}
    return rows, meta


def load_all():
    cards, notes = [], []
    # 存量 92
    pools = json.loads(POOLS.read_text(encoding="utf-8"))
    for ch in pools.get("characters", []):
        cards.append({
            "name": ch.get("name", ""), "work": ch.get("work", ""),
            "medium": ch.get("source_medium", ""), "region": ch.get("source_region", ""),
            "gender": _norm_gender(ch.get("gender", "unknown")),
            "original_position": ch.get("original_position", ""),
            "slot_keys": ch.get("slot_keys", {}) or {},
            "enhanced": False, "origin": "builtin", "id": ch.get("id", ""),
            "role": ch.get("role", ""),
        })
    # 草案
    for path in sorted(DRAFT_DIR.glob("*.json")):
        if path.name.startswith("build_"):
            continue
        rows, meta = _iter_draft(path)
        notes.append(meta)
        for r in rows:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            pos = str(r.get("original_position") or "").strip()
            slot = SLOT_OF_POSITION.get(pos, str(r.get("slot") or "").strip())
            key = str(r.get("key") or "").strip()
            cards.append({
                "name": str(r.get("name")).strip(), "work": str(r.get("work") or "").strip(),
                "medium": str(r.get("medium") or r.get("source_medium") or "").strip(),
                "region": str(r.get("region") or r.get("source_region") or "").strip(),
                "gender": _norm_gender(r.get("gender")),
                "original_position": pos,
                "slot_keys": {slot: [key]} if key else {},
                "enhanced": bool(r.get("enhanced")),
                "new_category": bool(r.get("new_category")),
                "origin": path.stem, "provenance_slot": str(r.get("slot") or "").strip(),
            })
    return cards, notes


def _same_work(a: str, b: str) -> bool:
    """出处等价：完全相同，或一方完整包含另一方（如「BLEACH」⊂「死神/BLEACH」）。
    用户口径：同名但不同出处 = 不同角色，不算同名。"""
    a, b = a.strip(), b.strip()
    return bool(a and b) and (a == b or a in b or b in a)


def dedupe(cards):
    """同名同出处去重（出处等价按 _same_work）；跨作品同名保留（不视为同名）。"""
    best: dict[tuple[str, str], dict] = {}
    for c in cards:
        k = c["name"]
        hit = next((key for key in best if key[0] == k and _same_work(key[1], c["work"])), None)
        if hit is None:
            best[(c["name"], c["work"])] = c
            continue
        cur, old = c, best[hit]

        def score(x):
            derived = SLOT_OF_POSITION.get(x["original_position"], "")
            return (1 if x.get("provenance_slot", x.get("origin") == "builtin") == derived else 0,
                    x["origin"] == "builtin")
        if score(cur) > score(old):
            best[hit] = cur
    out = list(best.values())
    for c in out:
        c.pop("name_collision_risk", None)  # 跨作品同名不标记（用户口径 2026-08-28）
    return out


def coverage(cards):
    by_pos = Counter(c["original_position"] for c in cards)
    by_slot_key = defaultdict(Counter)
    region_medium = defaultdict(Counter)
    gender_by_slot = defaultdict(Counter)
    for c in cards:
        derived = SLOT_OF_POSITION.get(c["original_position"], "")
        for slot, keys in (c.get("slot_keys") or {}).items():
            for k in keys:
                by_slot_key[slot][k] += 1
        if c["origin"] != "builtin":
            region_medium[c["region"] or "?"][c["medium"] or "?"] += 1
        gender_by_slot[derived][c["gender"]] += 1
    return by_pos, by_slot_key, region_medium, gender_by_slot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="仅打印，不写文件")
    args = ap.parse_args()

    cards, notes = load_all()
    cards = dedupe(cards)
    by_pos, by_slot_key, region_medium, gender_by_slot = coverage(cards)

    ok = True
    lines = [f"== final_roster 合并报告 ({datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}) == GUI预览={args.preview}"]
    for pos, target in TARGETS.items():
        n = by_pos.get(pos, 0)
        flag = "PASS" if n >= target else "SHORT"
        if n < target:
            ok = False
        lines.append(f"[{flag}] {pos}: {n} / 目标≥{target}")
    lines.append(f"合计: {len(cards)} 张卡（含存量 92）")
    lines.append("")
    lines.append("-- 槽位 key 分布 --")
    for slot in ("主角栏", "伙伴栏", "主线栏", "宿敌栏"):
        lines.append(f"{slot}: {dict(by_slot_key.get(slot, {}))}")
    lines.append("")
    lines.append("-- 新增部分 region×medium --")
    for region, mc in sorted(region_medium.items()):
        lines.append(f"{region}: {dict(mc)}")
    lines.append("")
    lines.append("-- 槽位 gender --")
    for slot, gc in sorted(gender_by_slot.items()):
        lines.append(f"{slot}: {dict(gc)}")
    risky = sorted(c["name"] for c in cards if c.get("name_collision_risk"))
    lines.append(f"裸名撞名风险（开局自动改名兜底）: {len(risky)} → {risky[:20]}")
    report = "\n".join(lines)
    print(report)

    if args.preview:
        print("\n[present] --preview 模式不写文件")
        return 0 if ok else 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
        "targets": TARGETS, "totals": dict(by_pos), "pass": ok,
        "merge_notes": notes,
        "coverage": {"slot_keys": {k: dict(v) for k, v in by_slot_key.items()},
                     "region_medium": {k: dict(v) for k, v in region_medium.items()},
                     "gender_by_slot": {k: dict(v) for k, v in gender_by_slot.items()}},
        "cards": cards,
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    print(f"\n[present] 已写入 {OUT}（{len(cards)} 卡）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
