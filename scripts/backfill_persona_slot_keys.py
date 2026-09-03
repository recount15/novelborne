# -*- coding: utf-8 -*-
"""给 persona 导入的 59 个角色卡补四维 slot_keys。

规则：
- 主角栏：根据角色背景关键词推断类型（谨慎/热血/谋略/混沌/义士/苟道…）
- 伴侣栏：["通用"]
- 伙伴栏：["盟友"]
- 宿敌栏：["对手"]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "runtime" / "fate_engine.db"

# 关键词 → 主角栏类型
KEYWORD_MAP = [
    (["谨慎", "苟", "低调", "底牌", "风险", "保命", "活着"], "苟道型"),
    (["热血", "硬刚", "正面", "猛", "战斗", "无敌", "霸"], "热血型"),
    (["谋略", "权谋", "布局", "算计", "借力", "智", "策略", "理性", "逻辑"], "谋略型"),
    (["混沌", "乐子", "随性", "趣味", "疯狂", "神经"], "混沌型"),
    (["义", "道义", "守护", "保护", "答应", "守信", "规矩"], "义士型"),
    (["野心", "称霸", "帝王", "皇", "王"], "霸主型"),
    (["重生", "穿越", "未来", "经验"], "重生型"),
    (["搞笑", "吐槽", "嘴炮", "话痨"], "吐槽型"),
    (["修炼", "修仙", "长生", "永生"], "求道型"),
    (["凡人", "普通", "小人物"], "凡人型"),
]


def infer_protagonist_type(background: str) -> str:
    bg = background.lower()
    for keywords, label in KEYWORD_MAP:
        if any(k in bg for k in keywords):
            return label
    return "主角型"


SLOT_MAP = {
    "主角栏": lambda bg: [infer_protagonist_type(bg)],
    "伴侣栏": lambda bg: ["通用"],
    "伙伴栏": lambda bg: ["盟友"],
    "宿敌栏": lambda bg: ["对手"],
}


def main() -> None:
    conn = sqlite3.connect(str(DB))
    c = conn.cursor()

    c.execute("SELECT id, background FROM characters WHERE id LIKE 'persona-%'")
    rows = c.fetchall()
    print(f"找到 {len(rows)} 个 persona 角色卡")

    updated = 0
    for card_id, background in rows:
        bg = background or ""
        # 删除旧 slot
        c.execute("DELETE FROM character_slots WHERE character_id = ?", (card_id,))
        # 插入四维
        for slot_name, fn in SLOT_MAP.items():
            for slot_type in fn(bg):
                c.execute(
                    "INSERT OR IGNORE INTO character_slots (character_id, slot_name, slot_type) VALUES (?, ?, ?)",
                    (card_id, slot_name, slot_type),
                )
        updated += 1

    conn.commit()
    conn.close()
    print(f"更新 {updated} 个角色卡的四维 slot_keys")


if __name__ == "__main__":
    main()
