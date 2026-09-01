# -*- coding: utf-8 -*-
"""engine.turn_grader 空级批改器单测（重构 §3 门禁与批改）。

覆盖：段空通过/字数窗口/缺必含词/未点名/缺交互 marker/雷区词/锚点
（证据词缺失、缺动作词、否定窗）、选项空（数量/编号/长度/因素/preview/
来源标注/相似去重）、角色空（名册/证据子串/走向/摘要长度/合格分拣）、
整体格式检验五类残留 + 干净文本通过、format_gate dict 形态、
build_refill_prompt 错误词回填。自包含：不联网、不读真实资产。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import turn_grader  # noqa: E402

# 无任何 marker/雷区词的中性填充句（只用来把字数垫进窗口）
FILLER = "檐角的风铃在雨里轻轻晃动，远处的更鼓声穿过雨幕。"


def _pad(text: str, minimum: int) -> str:
    while len(text) < minimum:
        text += FILLER
    return text


def _contract(**overrides) -> turn_grader.SegmentContract:
    base = dict(index=2, role="铺垫段", window=(80, 200),
                must_include=("玄铁令",), must_mention=("沈青",),
                forbidden=("系统提示",))
    base.update(overrides)
    return turn_grader.SegmentContract(**base)


def _seg_text() -> str:
    return _pad("雨夜渡口，沈青接过玄铁令，看向舱内昏睡的少年。", 100)


def _anchor_contract(**overrides) -> turn_grader.SegmentContract:
    base = dict(index=3, role="收束段", window=(30, 200),
                must_include=(), must_mention=(), forbidden=(),
                anchor_terms=("北门失守",), anchor_require_climax=True)
    base.update(overrides)
    return turn_grader.SegmentContract(**base)


GOOD_OPTIONS = [
    {"key": "A", "text": "趁夜色摸向渡口，查扣那批玄铁令", "factor": "金手指", "preview": "惊动巡夜的官差"},
    {"key": "B", "text": "以瞬身术先一步登船控制舵手", "factor": "金手指", "preview": "冷却两回合，错过下一战"},
    {"key": "C", "text": "用听风耳辨别岸上的埋伏位置", "factor": "金手指", "preview": "提前避开弩阵"},
    {"key": "D", "text": "将计就计，放走信使引出主使", "factor": "金手指", "preview": "幕后人警觉，线索可能断掉"},
    {"key": "E", "text": "先把沈青的伤势稳住再谈行动", "factor": "性格", "preview": "延误半个时辰，渡船先行"},
    {"key": "F", "text": "按江湖规矩与对方当面对质", "factor": "性格", "preview": "谈判破裂即开战"},
]


def _options(**mutations) -> list[dict]:
    items = [dict(item) for item in GOOD_OPTIONS]
    for position, field, value in mutations.get("set", ()):
        items[position][field] = value
    if "drop" in mutations:
        for position in sorted(mutations["drop"], reverse=True):
            items.pop(position)
    return items


class TestSegmentContractDataclass(unittest.TestCase):
    def test_normalizes_sequences_and_window(self):
        contract = turn_grader.SegmentContract(
            1, "铺垫段", [120, 100], must_include=["玄铁令", "  "],
            must_mention=["沈青"], forbidden=[], anchor_terms=["北门失守"])
        self.assertEqual(contract.window, (100, 120))
        self.assertEqual(contract.must_include, ("玄铁令",))
        self.assertEqual(contract.must_mention, ("沈青",))
        self.assertEqual(contract.forbidden, ())
        self.assertEqual(contract.anchor_terms, ("北门失守",))


class TestGradeSegment(unittest.TestCase):
    def test_pass(self):
        result = turn_grader.grade_segment(_contract(), _seg_text())
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_window_short(self):
        result = turn_grader.grade_segment(_contract(), "太短了。")
        self.assertFalse(result.ok)
        self.assertTrue(any("字数" in error and "80–200" in error for error in result.errors))

    def test_window_over(self):
        result = turn_grader.grade_segment(_contract(), _seg_text() + FILLER * 6)
        self.assertFalse(result.ok)
        self.assertTrue(any("字数" in error for error in result.errors))

    def test_missing_must_include_word(self):
        text = _pad("雨夜渡口，有人接过半块残令，看向舱内昏睡的少年。", 100)
        result = turn_grader.grade_segment(_contract(must_mention=()), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("玄铁令" in error and "必含词" in error for error in result.errors))

    def test_no_name_mentioned(self):
        text = _pad("雨夜渡口，有人接过玄铁令，看向舱内昏睡的少年。", 100)
        result = turn_grader.grade_segment(_contract(), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("未点名" in error and "沈青" in error for error in result.errors))
        # 交互 marker（接过）已存在，不应重复报错
        self.assertFalse(any("回应" in error for error in result.errors))

    def test_no_interaction_marker(self):
        text = _pad("沈青立在檐下，玄铁令贴身收好，雨水顺着帽檐滑落。", 100)
        result = turn_grader.grade_segment(_contract(), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("回应" in error and "动作" in error for error in result.errors))

    def test_forbidden_word(self):
        result = turn_grader.grade_segment(_contract(), _seg_text() + "（系统提示：通过）")
        self.assertFalse(result.ok)
        self.assertTrue(any("雷区" in error and "系统提示" in error for error in result.errors))

    def test_anchor_climax_pass(self):
        text = "三更时分，他率死士撞开北门门闩，守军因此阵脚大乱，防线落定，北门失守已成不可更改的事实。"
        result = turn_grader.grade_segment(_anchor_contract(), text)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_anchor_term_missing(self):
        text = "他率死士撞开侧门，防线因此落定，大雨浇灭了火把。" + FILLER
        result = turn_grader.grade_segment(_anchor_contract(), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("锚点证据词" in error and "北门失守" in error for error in result.errors))

    def test_anchor_action_marker_missing(self):
        text = "北门失守的消息传来，城中人心因此落定，各方随后散去。" + FILLER
        result = turn_grader.grade_segment(_anchor_contract(), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("动作词" in error for error in result.errors))
        self.assertFalse(any("结果词" in error for error in result.errors))
        self.assertFalse(any("因果" in error for error in result.errors))

    def test_anchor_negation_window_conflicted(self):
        text = "他早已率死士撞开侧门，防线因此落定；但斥候来报，城中并未让北门失守发生，守军旗鼓未动。"
        result = turn_grader.grade_segment(_anchor_contract(), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("否定" in error and "北门失守" in error for error in result.errors))

    def test_anchor_later_occurrence_negation_also_detected(self):
        # 首次出现合法，第二次出现被否定：不能只检查 find(term) 首现。
        text = ("北门失守已由死士撞开并因此落定；斥候随后回报，"
                "其实并未让北门失守发生，守军仍在原地。")
        result = turn_grader.grade_segment(_anchor_contract(), text)
        self.assertFalse(result.ok)
        self.assertTrue(any("否定" in error for error in result.errors))

    def test_anchor_required_but_no_terms(self):
        result = turn_grader.grade_segment(
            _anchor_contract(anchor_terms=()), "他撞开侧门，防线因此落定，守军溃退。")
        self.assertFalse(result.ok)
        self.assertTrue(any("未提供锚点证据词" in error for error in result.errors))

    def test_anchor_check_off_for_non_climax_segment(self):
        # anchor_require_climax=False：锚点词不命中也不报错（铺垫段只做基础四查）
        text = "他率死士撞开侧门，防线因此落定，大雨浇灭了火把。" + FILLER
        result = turn_grader.grade_segment(
            _anchor_contract(anchor_require_climax=False), text)
        self.assertTrue(result.ok)


class TestGradeOptions(unittest.TestCase):
    def test_pass(self):
        result = turn_grader.grade_options(GOOD_OPTIONS)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_wrong_count(self):
        result = turn_grader.grade_options(_options(drop=[5]))
        self.assertFalse(result.ok)
        self.assertTrue(any("恰好 6 条" in error for error in result.errors))

    def test_invalid_key(self):
        result = turn_grader.grade_options(_options(set=[(5, "key", "G")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("A–F" in error for error in result.errors))

    def test_duplicate_key(self):
        result = turn_grader.grade_options(_options(set=[(5, "key", "A")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("重复" in error and "A" in error for error in result.errors))

    def test_text_too_short(self):
        result = turn_grader.grade_options(_options(set=[(0, "text", "去码头")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("8–60" in error for error in result.errors))

    def test_bad_factor(self):
        result = turn_grader.grade_options(_options(set=[(2, "factor", "运气")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("金手指、性格、剧情" in error for error in result.errors))

    def test_bad_factor_distribution(self):
        result = turn_grader.grade_options(_options(set=[(5, "factor", "剧情")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("4 条金手指 + 2 条性格" in error for error in result.errors))

    def test_empty_preview(self):
        result = turn_grader.grade_options(_options(set=[(3, "preview", "")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("后果预告" in error for error in result.errors))

    def test_preview_too_long(self):
        result = turn_grader.grade_options(_options(set=[(3, "preview", "甲" * 61)]))
        self.assertFalse(result.ok)
        self.assertTrue(any("超长" in error and "60" in error for error in result.errors))

    def test_source_label_in_text(self):
        result = turn_grader.grade_options(
            _options(set=[(0, "text", "趁夜色摸向渡口查探（金手指）")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("来源标注" in error and "（金手指）" in error for error in result.errors))

    def test_similar_texts_rejected(self):
        result = turn_grader.grade_options(_options(
            set=[(3, "text", "用听风耳辨别岸上的埋伏方位")]))
        self.assertFalse(result.ok)
        self.assertTrue(any("相似" in error for error in result.errors))

    def test_non_mapping_item(self):
        items = _options()
        items[4] = "坏条目"
        result = turn_grader.grade_options(items)
        self.assertFalse(result.ok)
        self.assertTrue(any("对象" in error for error in result.errors))


class TestGradeCharacterPatch(unittest.TestCase):
    NARRATIVE = "雨夜渡口，沈青接过玄铁令，看向舱内昏睡的少年，两人短暂交谈后各自戒备。"
    ROSTER = ("沈青", "阿岚")

    def _patch(self, **overrides):
        base = dict(name="沈青", evidence="沈青接过玄铁令",
                    relationship_delta="升温", summary="因玄铁令托付而生出信任")
        base.update(overrides)
        return base

    def test_pass(self):
        valid, rejected = turn_grader.grade_character_patch(
            [self._patch()], self.ROSTER, self.NARRATIVE)
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["name"], "沈青")
        self.assertEqual(rejected, [])

    def test_unknown_name(self):
        valid, rejected = turn_grader.grade_character_patch(
            [self._patch(name="林秋")], self.ROSTER, self.NARRATIVE)
        self.assertEqual(valid, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("名册", rejected[0][1])

    def test_evidence_not_substring(self):
        valid, rejected = turn_grader.grade_character_patch(
            [self._patch(evidence="沈青一把夺过令牌")], self.ROSTER, self.NARRATIVE)
        self.assertEqual(valid, [])
        self.assertIn("子串", rejected[0][1])

    def test_bad_relationship_delta(self):
        valid, rejected = turn_grader.grade_character_patch(
            [self._patch(relationship_delta="下降")], self.ROSTER, self.NARRATIVE)
        self.assertEqual(valid, [])
        self.assertIn("关系走向", rejected[0][1])

    def test_empty_summary(self):
        valid, rejected = turn_grader.grade_character_patch(
            [self._patch(summary="")], self.ROSTER, self.NARRATIVE)
        self.assertEqual(valid, [])
        self.assertIn("摘要", rejected[0][1])

    def test_summary_too_long(self):
        valid, rejected = turn_grader.grade_character_patch(
            [self._patch(summary="信" * 61)], self.ROSTER, self.NARRATIVE)
        self.assertEqual(valid, [])
        self.assertIn("超长", rejected[0][1])

    def test_mixed_split(self):
        patches = [self._patch(),
                   self._patch(name="林秋"),
                   self._patch(name="阿岚", evidence="看向舱内昏睡的少年",
                               relationship_delta="稳定", summary="仍在观望")]
        valid, rejected = turn_grader.grade_character_patch(patches, self.ROSTER, self.NARRATIVE)
        self.assertEqual([item["name"] for item in valid], ["沈青", "阿岚"])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0][0]["name"], "林秋")


CLEAN_TEXT = (
    "雨夜渡口，沈青接过玄铁令，看向舱内昏睡的少年。\n\n"
    "檐角的风铃在雨里轻轻晃动，远处的更鼓声穿过雨幕。\n\n"
    "他忽然明白，这一夜的买卖远不止一枚令牌。"
)


class TestFormatGate(unittest.TestCase):
    def test_clean_text_passes(self):
        check = turn_grader.grade_format_whole(CLEAN_TEXT)
        self.assertTrue(check.ok)
        self.assertEqual(check.errors, [])

    def test_code_fence_detected(self):
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n```json\n{\"a\": 1}\n```")
        self.assertFalse(check.ok)
        self.assertTrue(any("围栏" in error for error in check.errors))

    def test_single_line_json_parsed_detected(self):
        line = json.dumps({"anchor": "x" * 70, "round": 3}, ensure_ascii=False)
        self.assertGreaterEqual(len(line), 80)
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n" + line)
        self.assertFalse(check.ok)
        self.assertTrue(any("JSON" in error for error in check.errors))

    def test_single_line_json_keypattern_detected(self):
        line = '{"anchor": "' + "x" * 70 + '" "round": 3'  # 无法 json.loads，但含多个 "键":
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n" + line)
        self.assertFalse(check.ok)
        self.assertTrue(any("JSON" in error for error in check.errors))

    def test_quote_started_json_key_line_detected(self):
        # 思考型模型偶发只泄漏 JSON 对象的一行（没有外层 {），也应被格式门拦截。
        line = '"genre": "' + "x" * 90 + '"'
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n" + line)
        self.assertFalse(check.ok)
        self.assertTrue(any("JSON" in error for error in check.errors))

    def test_short_json_line_not_flagged(self):
        check = turn_grader.grade_format_whole(CLEAN_TEXT + '\n\n{"a": 1}')
        self.assertTrue(check.ok)

    def test_system_bracket_line_detected(self):
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n【系统校验】本回合锚点已收束")
        self.assertFalse(check.ok)
        self.assertTrue(any("系统自检" in error for error in check.errors))

    def test_log_residue_detected(self):
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n<<<LOG>>>第3回合｜玩家:无<<<END>>>")
        self.assertFalse(check.ok)
        self.assertTrue(any("<<<LOG>>>" in error for error in check.errors))

    def test_archive_residue_detected(self):
        check = turn_grader.grade_format_whole(CLEAN_TEXT + "\n\n<<<ARCHIVE>>>既成事实摘要<<<END>>>")
        self.assertFalse(check.ok)
        self.assertTrue(any("<<<ARCHIVE>>>" in error for error in check.errors))

    def test_option_block_in_narrative_detected(self):
        mixed = CLEAN_TEXT + "\nA. 摸向渡口查探船期\nB. 以瞬身术先行登船\nC. 稳住伤势再行动\nD. 与对方当面对质"
        check = turn_grader.grade_format_whole(mixed)
        self.assertFalse(check.ok)
        self.assertTrue(any("选项块" in error for error in check.errors))

    def test_option_block_with_blank_lines_detected(self):
        mixed = CLEAN_TEXT + "\nA. 摸向渡口查探船期\n\nB. 以瞬身术先行登船\n\nC. 稳住伤势再行动\n\nD. 与对方当面对质"
        check = turn_grader.grade_format_whole(mixed)
        self.assertFalse(check.ok)
        self.assertTrue(any("选项块" in error for error in check.errors))

    def test_three_option_lines_below_threshold_pass(self):
        mixed = CLEAN_TEXT + "\nA. 摸向渡口查探船期\nB. 以瞬身术先行登船\nC. 稳住伤势再行动"
        check = turn_grader.grade_format_whole(mixed)
        self.assertTrue(check.ok)

    def test_empty_text_rejected(self):
        check = turn_grader.grade_format_whole("   ")
        self.assertFalse(check.ok)
        self.assertEqual(check.errors, ["正文为空"])

    def test_format_gate_dict_shape(self):
        gate = turn_grader.format_gate(CLEAN_TEXT)
        self.assertEqual(gate, {"valid": True, "issues": []})
        bad = turn_grader.format_gate(CLEAN_TEXT + "\n\n```py\npass\n```")
        self.assertFalse(bad["valid"])
        self.assertTrue(bad["issues"])
        self.assertTrue(all(isinstance(item, str) for item in bad["issues"]))


class TestBuildRefillPrompt(unittest.TestCase):
    def test_contains_errors_and_requirements(self):
        contract = _contract(must_include=("玄铁令", "渡口"),
                             anchor_terms=("北门失守",), anchor_require_climax=True)
        errors = ["第2段（铺垫段）字数 43 字，不在要求窗口 80–200 字内",
                  "第2段（铺垫段）缺少必含词：「玄铁令」"]
        prompt = turn_grader.build_refill_prompt(contract, errors)
        for error in errors:
            self.assertIn(error, prompt)
        self.assertIn("只重写本段", prompt)
        self.assertIn("80–200", prompt)
        self.assertIn("玄铁令", prompt)
        self.assertIn("渡口", prompt)
        self.assertIn("沈青", prompt)
        self.assertIn("系统提示", prompt)
        self.assertIn("北门失守", prompt)
        self.assertIn("否定表述", prompt)

    def test_mapping_contract_variant(self):
        prompt = turn_grader.build_refill_prompt(
            {"空类型": "选项空", "数量": "恰好 6 条"}, ["错误一：数量不足"])
        self.assertIn("只重写本空", prompt)
        self.assertIn("错误一：数量不足", prompt)
        self.assertIn("数量：恰好 6 条", prompt)


if __name__ == "__main__":
    unittest.main()
