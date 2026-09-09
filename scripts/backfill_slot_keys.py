"""四维标签补齐迁移脚本。

对 data/character_pools.json 与 data/characters/ 目录（builtin/user/overrides）
下所有角色卡执行：

1. slot_keys 四个键（主角栏/伴侣栏/伙伴栏/宿敌栏）全部存在；
2. 历史键名 "主线栏" 改写为 "伴侣栏"；
3. 空缺维度填 ["通用"]，确保任何角色在四栏都可选。

用法：
    python scripts/backfill_slot_keys.py          # 执行写入
    python scripts/backfill_slot_keys.py --dry    # 只统计不写入
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOLS_PATH = ROOT / "assets" / "data" / "character_pools.json"
CHARACTERS_DIR = ROOT / "assets" / "data" / "characters"
SLOTS = ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏")
FALLBACK = "通用"


def normalize_slot_keys(raw: dict) -> tuple[dict, list[str]]:
    """归一单个卡的 slot_keys，返回 (新slot_keys, 变更说明列表)。"""
    changes: list[str] = []
    result: dict[str, list[str]] = {}
    for key, value in (raw or {}).items():
        # 历史键名统一
        new_key = "伴侣栏" if key == "主线栏" else key
        if new_key != key:
            changes.append(f"键名 {key} -> {new_key}")
        values = [str(v).strip() for v in (value or []) if str(v).strip()]
        result[new_key] = values
    for slot in SLOTS:
        if slot not in result:
            changes.append(f"补键 {slot}=[{FALLBACK}]")
            result[slot] = [FALLBACK]
        elif not result[slot]:
            changes.append(f"补值 {slot}=[{FALLBACK}]")
            result[slot] = [FALLBACK]
    # 保持固定顺序输出
    return {slot: result[slot] for slot in SLOTS}, changes


def iter_pools_cards():
    doc = json.loads(POOLS_PATH.read_text(encoding="utf-8"))
    for card in doc.get("characters", []):
        yield card
    # 写回时需要文档对象，这里通过闭包标志由调用方处理
    iter_pools_cards._doc = doc  # type: ignore[attr-defined]


def fix_file(path: Path, data: dict, *, dry: bool) -> int:
    """写回单个 JSON 文件。返回写入的卡数。"""
    if dry:
        return len(data.get("characters", []))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return len(data.get("characters", []))


def main() -> int:
    dry = "--dry" in sys.argv
    total = 0
    changed_cards = 0

    # 1) character_pools.json
    doc = json.loads(POOLS_PATH.read_text(encoding="utf-8"))
    for card in doc.get("characters", []):
        total += 1
        new_keys, changes = normalize_slot_keys(card.get("slot_keys") or {})
        if changes:
            changed_cards += 1
            print(f"[pools] {card.get('id')}: {'; '.join(changes)}")
        card["slot_keys"] = new_keys
    total_written = fix_file(POOLS_PATH, doc, dry=dry)
    print(f"character_pools.json: {total_written} cards {'(dry-run)' if dry else 'written'}")

    # 2) data/characters 目录树（一卡一文件 或 内嵌 characters 列表）
    if CHARACTERS_DIR.is_dir():
        for path in sorted(CHARACTERS_DIR.rglob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"[skip] {path.name}: {exc}")
                continue
            cards = data.get("characters") if isinstance(data, dict) else None
            if isinstance(cards, list):
                dirty = False
                for card in cards:
                    if not isinstance(card, dict):
                        continue
                    total += 1
                    new_keys, changes = normalize_slot_keys(card.get("slot_keys") or {})
                    if changes:
                        changed_cards += 1
                        dirty = True
                        print(f"[{path.name}] {card.get('id')}: {'; '.join(changes)}")
                    card["slot_keys"] = new_keys
                if dirty and not dry:
                    fix_file(path, data, dry=False)
            elif isinstance(data, dict) and ("slot_keys" in data or "name" in data):
                # 单卡文件
                total += 1
                new_keys, changes = normalize_slot_keys(data.get("slot_keys") or {})
                if changes:
                    changed_cards += 1
                    print(f"[{path.name}] {data.get('id')}: {'; '.join(changes)}")
                    data["slot_keys"] = new_keys
                    if not dry:
                        fix_file(path, data, dry=False)

    print(f"\n扫描 {total} 张卡，{changed_cards} 张有变更{'（dry-run 未写入）' if dry else '，已全部写入'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
