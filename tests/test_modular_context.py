from core.engine.modular_context import (
    SECTION_BUDGETS,
    TOTAL_BUDGET,
    build_modular_context,
    build_recent_digest,
)


def test_recent_digest_is_bounded_to_requested_turns():
    history = []
    for number in range(1, 13):
        history.extend([
            {"role": "user", "content": f"行动{number}"},
            {"role": "assistant", "content": f"结果{number}"},
        ])

    digest = build_recent_digest({"history": history}, rounds=3)

    assert "结果9" not in digest
    assert "行动10" in digest
    assert "结果12" in digest
    assert digest.count("叙事：") == 3


def test_modular_context_assembles_existing_state_and_inputs():
    state = {
        "work": "星海旧城",
        "system": "保持事实一致",
        "mode": "强化",
        "language_style": {"tone": "克制"},
        "pacing": "本回合加速",
        "anchor_timeline": {"current": {"title": "城门封锁", "summary": "守军出现"}},
        "active_members": [{
            "name": "林遥", "role": "伙伴", "character_card": {"goal": "护送证人"},
        }],
        "active_summaries": {"林遥": "不信任城主"},
        "state_memory": {"location": {"name": "北门"}, "abilities": {
            "golden_finger": {"name": "回响视界"},
        }},
        "last_ripple": {"level": "L2", "allowed": True},
        "quest": {"status": "active", "goal": "找到证人"},
        "convergence_state": {"effective": "较高"},
        "novel_excerpt": "原文风格样本句。",
        "history": [{"role": "assistant", "content": "上一回合结果"}],
    }

    context = build_modular_context(
        state, message="调查北门", system_prompt="本次系统要求",
        anchor_text="锚点原文：城门封锁", context_blocks="额外事实",
    )

    for expected in (
        "星海旧城", "本次系统要求", "克制", "本回合加速", "锚点原文：城门封锁",
        "林遥", "护送证人", "不信任城主", "北门", "L2", "找到证人",
        "较高", "回响视界", "上一回合结果", "原文风格样本句。", "调查北门",
        "额外事实",
    ):
        assert expected in context
    assert len(context) <= TOTAL_BUDGET


def test_missing_or_malformed_fields_are_safe_and_section_budgets_hold():
    context = build_modular_context({
        "state_memory": "not-a-mapping",
        "history": [None, "bad", {"role": "assistant", "content": "x" * 5000}],
        "active_members": "not-a-list",
        "anchor_timeline": "bad",
    })

    assert context
    assert len(context) <= TOTAL_BUDGET
    for title, budget in (
        ("作品与系统", SECTION_BUDGETS["work"]),
        ("最近回合摘要", SECTION_BUDGETS["recent"]),
    ):
        marker = f"【{title}】"
        if marker in context:
            start = context.index(marker)
            next_marker = context.find("【", start + len(marker))
            end = next_marker if next_marker >= 0 else len(context)
            assert end - start <= budget
