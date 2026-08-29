# -*- coding: utf-8 -*-
"""把 personas/standard 和 personas/enhanced 中的全部角色模型导入数据库。

每个 .md 文件解析为一张角色卡，写入 characters 表（source_type='builtin'）。
- standard 层 distill_level='normal'
- enhanced 层 distill_level='enhanced'
四维 slot_keys 全部兜底为 ['通用']，original_position='主角'。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 确保项目根在 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.engine.character_db import init_database, insert_characters_batch
from core.engine.catalog import CharacterCard

PERSONAS_STANDARD = ROOT / "personas" / "standard"
PERSONAS_ENHANCED = ROOT / "personas" / "enhanced"

SLOT_NAMES = ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏")


def parse_persona(filepath: Path, level: str) -> dict | None:
    """解析 persona .md 文件，提取角色信息。"""
    text = filepath.read_text(encoding="utf-8")

    # frontmatter description
    desc = ""
    m = re.search(r"description:\s*(.+)", text)
    if m:
        desc = m.group(1).strip()

    # 角色名：从 description 提取 "角色名（作品）..."
    name = ""
    work = ""
    m2 = re.match(r"([^\s（(]+)[（(]([^）)]+)", desc)
    if m2:
        name = m2.group(1).strip()
    else:
        m3 = re.search(r"^#\s+(.+?)思维模型", text, re.M)
        if m3:
            name = m3.group(1).strip()
        else:
            name = filepath.stem

    # 作品
    book_match = re.search(r"《([^》]+)》", desc or text[:500])
    if book_match:
        work = book_match.group(1)

    # 一句话定义
    one_line = ""
    m4 = re.search(r"一句话定义[：:]\s*\n*(.+?)(?:\n\n|\n##|\Z)", text, re.S)
    if m4:
        one_line = m4.group(1).strip().strip("*")[:300]
    elif desc:
        one_line = desc[:300]

    # 核心定义/公理
    core = ""
    m5 = re.search(r"核心定义[^\n]*\n+(.+?)(?:\n##|\Z)", text, re.S)
    if m5:
        core = m5.group(1).strip().strip("*")[:500]

    # 欲望（从一句话定义或核心定义推断）
    desire = one_line or core or "追求自身目标的最大化"

    # 恐惧（尝试提取）
    fear = ""
    m6 = re.search(r"恐惧[：:]\s*(.+?)(?:\n|$)", text)
    if m6:
        fear = m6.group(1).strip()[:200]
    if not fear:
        fear = "目标失败、失去控制"

    # 语言风格（尝试提取）
    voice = ""
    m7 = re.search(r"语言风格[^\n]*\n+(.+?)(?:\n##|\n#|\Z)", text, re.S)
    if m7:
        voice = m7.group(1).strip()[:300]
    if not voice:
        voice = "见思维模型文件"

    # 背景简介
    background = one_line or core or desc[:200] or f"{name}，出自《{work}》"

    # 性别推断（从名字和作品内容，这里默认 unknown，让用户在使用时修正）
    gender = "unknown"

    # archetype
    archetype = "主角" if level == "enhanced" else "主角"

    return {
        "id": f"persona-{filepath.stem}",
        "name": name,
        "role": "主角",
        "work": work,
        "archetype": archetype,
        "desire": desire,
        "fear": fear,
        "voice": voice,
        "background": background,
        "source": f"personas/{level}/{filepath.name}",
        "gender": gender,
        "original_position": "主角",
        "source_medium": "",
        "source_region": "",
        "distill_level": "enhanced" if level == "enhanced" else "normal",
        "slot_keys": {slot: ["通用"] for slot in SLOT_NAMES},
        "abilities": [],
        "knowledge_scope": [],
        "relationship_vector": {},
        "unacceptable_actions": [],
        "source_type": "builtin",
    }


def main() -> None:
    init_database()

    records: list[dict] = []
    for d, level in [(PERSONAS_STANDARD, "standard"), (PERSONAS_ENHANCED, "enhanced")]:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            rec = parse_persona(f, level)
            if rec and rec["name"]:
                records.append(rec)
                print(f"  {rec['name']} | {rec['work']} | {level}")

    print(f"\n共解析 {len(records)} 个角色模型，开始导入数据库...")

    cards = [CharacterCard.from_record(r) for r in records]
    insert_characters_batch(cards)
    print(f"导入完成：{len(cards)} 个角色卡已写入数据库")


if __name__ == "__main__":
    main()
