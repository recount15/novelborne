# -*- coding: utf-8 -*-
"""engine.turn_composer 组装器单测（重构 §1 Wave D 组装）。

覆盖：compose_display 拼装格式（段间空行/选项行格式/自由输入提示行/
附加段顺序/隐藏段绝不进入）、render_log_line 与既有 LOG 格式逐字一致
（写死期望串）、render_archive_message 文案对齐、split_for_history 与
compose 互逆。自包含：不联网、不读真实资产、不导入 fate_engine。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import turn_composer  # noqa: E402
from core.engine.turn_composer import FREE_INPUT_HINT  # noqa: E402

SEGMENTS = ["雨夜渡口，沈青接过玄铁令，看向舱内昏睡的少年。",
            "檐角的风铃在雨里轻轻晃动，远处的更鼓声穿过雨幕。"]
NARRATIVE = "\n\n".join(SEGMENTS)

OPTIONS = [
    {"key": "A", "text": "趁夜色摸向渡口，查扣那批玄铁令", "factor": "金手指", "preview": "惊动巡夜的官差"},
    {"key": "B", "text": "以瞬身术先一步登船控制舵手", "factor": "金手指", "preview": "冷却两回合，错过下一战"},
    {"key": "C", "text": "用听风耳辨别岸上的埋伏位置", "factor": "金手指", "preview": "提前避开弩阵"},
    {"key": "D", "text": "将计就计，放走信使引出主使", "factor": "金手指", "preview": "幕后人警觉，线索可能断掉"},
    {"key": "E", "text": "先把沈青的伤势稳住再谈行动", "factor": "性格", "preview": "延误半个时辰，渡船先行"},
    {"key": "F", "text": "按江湖规矩与对方当面对质", "factor": "剧情", "preview": "谈判破裂即开战"},
]


class TestComposeDisplay(unittest.TestCase):
    def test_narrative_joins_segments_with_blank_line(self):
        result = turn_composer.compose_display(SEGMENTS + [""], OPTIONS)
        self.assertEqual(result["narrative"], NARRATIVE)

    def test_options_block_line_format(self):
        result = turn_composer.compose_display(SEGMENTS, OPTIONS)
        lines = result["options_block"].split("\n")
        self.assertEqual(len(lines), 8)  # 6 条选项 + 空行 + 自由输入提示行
        self.assertEqual(lines[0], "A. 趁夜色摸向渡口，查扣那批玄铁令（后果：惊动巡夜的官差）")
        self.assertEqual(lines[5], "F. 按江湖规矩与对方当面对质（后果：谈判破裂即开战）")
        self.assertEqual(lines[6], "")
        self.assertEqual(lines[7], FREE_INPUT_HINT)
        pattern = re.compile(r"^[A-F]\. .+（后果：.+）$")
        for line in lines[:6]:
            self.assertRegex(line, pattern)

    def test_display_is_narrative_plus_options(self):
        result = turn_composer.compose_display(SEGMENTS, OPTIONS)
        self.assertEqual(result["display"], NARRATIVE + "\n\n" + result["options_block"])

    def test_extras_are_standalone_segments_before_options(self):
        eval_block = "【第十回合评价】节奏偏缓，建议下一周期提速。"
        digest = "【宿敌摘要】黑衣人现身北门。"
        result = turn_composer.compose_display(
            SEGMENTS, OPTIONS, eval_block=eval_block, nemesis_digest=digest)
        body = NARRATIVE + "\n\n" + eval_block + "\n\n" + digest
        self.assertEqual(result["narrative"], NARRATIVE)  # 附加段不并入正文
        self.assertEqual(result["display"], body + "\n\n" + result["options_block"])

    def test_option_without_preview(self):
        result = turn_composer.compose_display(
            SEGMENTS, [{"key": "A", "text": "原地戒备等待天亮"}])
        self.assertEqual(result["options_block"],
                         "A. 原地戒备等待天亮\n\n" + FREE_INPUT_HINT)

    def test_empty_options_yields_no_block(self):
        result = turn_composer.compose_display(SEGMENTS, [])
        self.assertEqual(result["options_block"], "")
        self.assertEqual(result["display"], NARRATIVE)

    def test_hidden_segments_never_enter_display(self):
        result = turn_composer.compose_display(
            SEGMENTS, OPTIONS, eval_block="【第十回合评价】节奏偏缓。")
        for mark in ("<<<LOG>>>", "<<<ARCHIVE>>>", "（系统存档："):
            self.assertNotIn(mark, result["display"])
            self.assertNotIn(mark, result["narrative"])

    def test_composed_narrative_passes_format_gate(self):
        from core.engine import turn_grader
        result = turn_composer.compose_display(SEGMENTS, OPTIONS)
        self.assertTrue(turn_grader.format_gate(result["narrative"])["valid"])


class TestRenderLogLine(unittest.TestCase):
    def test_exact_format(self):
        expected = ("<<<LOG>>>第7回合｜玩家:左臂轻伤，玉符耗尽｜金手指:瞬身（冷却2回合）"
                    "｜宿敌:黑衣人集结北门｜世界:官府开始排查渡口｜节拍:主线推进一成"
                    "｜进度:63<<<END>>>")
        line = turn_composer.render_log_line(
            7, "左臂轻伤，玉符耗尽", "瞬身（冷却2回合）", "黑衣人集结北门",
            "官府开始排查渡口", "主线推进一成", 63)
        self.assertEqual(line, expected)
        self.assertRegex(line, r"^<<<LOG>>>第\d+回合｜玩家:.+<<<END>>>$")

    def test_progress_clamped_to_0_100(self):
        self.assertIn("进度:100", turn_composer.render_log_line(1, "无", "无", "无", "无", "无", 120))
        self.assertIn("进度:0", turn_composer.render_log_line(1, "无", "无", "无", "无", "无", -5))

    def test_fields_flattened_to_single_line(self):
        line = turn_composer.render_log_line(2, "状态\n换行", "无", "无", "无", "无", 10)
        self.assertNotIn("\n", line)
        self.assertIn("玩家:状态 换行", line)


class TestRenderArchiveMessage(unittest.TestCase):
    def test_exact_wording(self):
        self.assertEqual(
            turn_composer.render_archive_message("既成事实摘要。"),
            "（系统存档：以下为截至目前既成事实的压缩存档，后续回合以此为事实基础，"
            "不得遗忘或篡改。）\n既成事实摘要。")


class TestSplitForHistory(unittest.TestCase):
    def test_inverse_of_compose_without_extras(self):
        composed = turn_composer.compose_display(SEGMENTS, OPTIONS)
        narrative, options_block = turn_composer.split_for_history(composed["display"])
        self.assertEqual(narrative, NARRATIVE)
        self.assertEqual(options_block, composed["options_block"])

    def test_inverse_of_compose_with_extras(self):
        eval_block = "【第十回合评价】节奏偏缓，建议下一周期提速。"
        digest = "【宿敌摘要】黑衣人现身北门。"
        composed = turn_composer.compose_display(
            SEGMENTS, OPTIONS, eval_block=eval_block, nemesis_digest=digest)
        narrative, options_block = turn_composer.split_for_history(composed["display"])
        self.assertEqual(narrative,
                         NARRATIVE + "\n\n" + eval_block + "\n\n" + digest)
        self.assertEqual(options_block, composed["options_block"])

    def test_text_without_options(self):
        narrative, options_block = turn_composer.split_for_history("只有正文，没有选项。")
        self.assertEqual(narrative, "只有正文，没有选项。")
        self.assertEqual(options_block, "")

    def test_options_block_only(self):
        block = turn_composer.compose_display([], OPTIONS)["options_block"]
        narrative, options_block = turn_composer.split_for_history(block)
        self.assertEqual(narrative, "")
        self.assertEqual(options_block, block)

    def test_round_trip_recomposes_display(self):
        composed = turn_composer.compose_display(SEGMENTS, OPTIONS)
        narrative, options_block = turn_composer.split_for_history(composed["display"])
        again = turn_composer.compose_display([narrative], OPTIONS)
        self.assertEqual(again["display"], composed["display"])
        self.assertEqual(again["options_block"], options_block)


if __name__ == "__main__":
    unittest.main()
