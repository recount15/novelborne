# -*- coding: utf-8 -*-
"""799卡规程校验脚本：把 CharacterCard 转 dict 后按 CharacterCreationProtocol 校验。

输出:
  outputs/protocol_recheck.json  全量校验结果（含每卡失败项与警告）
  outputs/cluster_fix_plan.json  集群修正计划（分片 + 每卡需修正的字段清单）
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engine import character_db  # noqa: E402
from core.engine.catalog import CharacterCard  # noqa: E402
from core.engine.character_creation_protocol import (  # noqa: E402
    validate_character_card,
)


def card_to_dict(card: CharacterCard) -> dict:
    """CharacterCard → 规程校验器期望的 Mapping 形态。"""
    return {
        "id": card.id,
        "name": card.name,
        "work": card.work,
        "role": card.role,
        "archetype": card.archetype,
        "desire": card.desire,
        "fear": card.fear,
        "voice": card.voice,
        "background": card.background,
        "abilities": list(card.abilities),
        "knowledge_scope": list(card.knowledge_scope),
        "unacceptable_actions": list(card.unacceptable_actions),
        "relationship_vector": {t: r for t, r in card.relationship_vector},
        "skill_ids": list(card.skill_ids),
        "gender": card.gender,
        "original_position": card.original_position,
        "source_medium": card.source_medium,
        "source_region": card.source_region,
    }


def main() -> None:
    chars = character_db.get_all_characters()
    print(f"DB 活跃角色总数: {len(chars)}")

    results = []
    for card in chars:
        data = card_to_dict(card)
        try:
            ok, errors, warnings = validate_character_card(data)
        except Exception as exc:  # 校验器本身异常也记录
            ok, errors, warnings = False, [f"校验器异常: {exc}"], []

        # 逐字段诊断（供集群修正定位）
        field_issues = []
        if not data["desire"]:
            field_issues.append("desire缺失")
        if not data["fear"]:
            field_issues.append("fear缺失")
        if not data["voice"]:
            field_issues.append("voice缺失")
        if not data["background"]:
            field_issues.append("background缺失")
        if not data["archetype"]:
            field_issues.append("archetype缺失")
        if not data["abilities"]:
            field_issues.append("abilities缺失")
        if not data["relationship_vector"]:
            field_issues.append("relationship_vector缺失")
        else:
            # 关系类型为空/过长过短
            bad_rel = [t for t, r in card.relationship_vector if not r or len(r) < 2]
            if bad_rel:
                field_issues.append(f"relationship_type不合格({len(bad_rel)}条)")
            if any(len(t) > 14 for t, _ in card.relationship_vector):
                field_issues.append("target_entity过长")
        if not data["unacceptable_actions"]:
            field_issues.append("unacceptable_actions缺失")
        if not data["knowledge_scope"]:
            field_issues.append("knowledge_scope缺失")

        results.append({
            "id": card.id,
            "name": card.name,
            "work": card.work,
            "role": card.role,
            "passed": bool(ok),
            "validation_errors": errors,
            "quality_warnings": warnings,
            "field_issues": field_issues,
        })

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"规程校验合格: {passed}/{total}  不合格: {total - passed}")

    # 错误分布
    err_counter = Counter()
    issue_counter = Counter()
    for r in results:
        for e in r["validation_errors"]:
            err_counter[e.split(":")[0]] += 1
        for i in r["field_issues"]:
            issue_counter[i.split("(")[0]] += 1
    print("\n校验错误分布:")
    for k, v in err_counter.most_common(12):
        print(f"  {k}: {v}卡")
    print("\n字段级问题分布:")
    for k, v in issue_counter.most_common(15):
        print(f"  {k}: {v}卡")

    out = Path("outputs/protocol_recheck.json")
    out.write_text(json.dumps({
        "checked_at": "2026-08-28",
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "error_distribution": dict(err_counter.most_common()),
        "field_issue_distribution": dict(issue_counter.most_common()),
        "results": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {out}")

    # ---- 集群修正计划：只包含不合格卡，按 field_issues 分片 ----
    failed = [r for r in results if not r["passed"]]
    if not failed:
        # 全部合格 → 写空计划，不留分片
        plan = {"generated_at": "2026-08-28", "total_failed": 0,
                "shard_count": 0,
                "protocol": "CharacterCreationProtocol.validate_character_card",
                "shards": []}
        plan_path = Path("outputs/cluster_fix_plan.json")
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n全部 {total} 张卡合格，已写入空修正计划 {plan_path}")
        return
    # 8片为主 + 留1片机动 → 9~10片每片约80卡
    shard_count = 10
    per_shard = (len(failed) + shard_count - 1) // shard_count
    shards = []
    for i in range(0, len(failed), per_shard):
        shard_cards = failed[i:i + per_shard]
        shards.append({
            "shard_id": len(shards) + 1,
            "card_count": len(shard_cards),
            "cards": [{"id": c["id"], "name": c["name"], "work": c["work"],
                       "field_issues": c["field_issues"],
                       "validation_errors": c["validation_errors"]} for c in shard_cards],
        })
    plan = {
        "generated_at": "2026-08-28",
        "total_failed": len(failed),
        "shard_count": len(shards),
        "protocol": "CharacterCreationProtocol.validate_character_card",
        "shards": shards,
    }
    plan_path = Path("outputs/cluster_fix_plan.json")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {plan_path}（{len(shards)} 片，每片约 {per_shard} 卡）")


if __name__ == "__main__":
    main()
