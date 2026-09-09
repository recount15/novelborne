"""choice_agent 接入 options_service 的模式行为测试。

验证 STORY_CHOICE_AGENT_MODE 三档行为：
- legacy：输出不变、无元数据；
- shadow：输出不变、记录影子元数据；
- agent：应用过滤与多样性选择，仅当保持 6 条时生效。
前台 A-F 契约在所有模式下不变。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.services import options_service


def fake_model(_prompt: str) -> str:
    """结构化模型替身：6 条含一条未来知识标注。"""
    import json
    return json.dumps({"options": [
        {"text": "沿潮沟摸向观星台（后果：衣袖被礁石划破）", "factor": "金手指"},
        {"text": "去听雨楼打听玉符下落（后果：欠下一笔人情）", "factor": "金手指"},
        {"text": "返回铁衣堡向堡主复命（后果：暴露行踪）", "factor": "金手指"},
        {"text": "在坊市低价收购灵砂（后果：引来摊主警觉）", "factor": "金手指"},
        {"text": "质问坊市司主簿登记簿去向（后果：被记入黑册）", "factor": "性格"},
        {"text": "独坐码头看潮起潮落（后果：想起师门旧事）", "factor": "性格"},
    ]}, ensure_ascii=False)


class ChoiceAgentIntegrationTests(unittest.TestCase):
    def _generate(self):
        return options_service.generate_options(
            client=None, model="fake", provider="deepseek",
            action="测试", model_fn=fake_model)

    def test_legacy_mode_untouched(self):
        with patch.dict("os.environ", {"STORY_CHOICE_AGENT_MODE": "legacy"}):
            result = self._generate()
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(result["options"]), 6)
        self.assertNotIn("choice_agent", result["meta"])

    def test_shadow_mode_records_metadata_only(self):
        with patch.dict("os.environ", {"STORY_CHOICE_AGENT_MODE": "shadow"}):
            result = self._generate()
        self.assertEqual(len(result["options"]), 6)
        shadow = result["meta"].get("choice_agent") or {}
        self.assertEqual(shadow.get("mode"), "shadow")
        self.assertFalse(shadow.get("applied"))
        self.assertEqual(len(shadow.get("kept_keys") or []), 6)

    def test_agent_mode_applies_diversity(self):
        with patch.dict("os.environ", {"STORY_CHOICE_AGENT_MODE": "agent"}):
            result = self._generate()
        options = result["options"]
        self.assertEqual(len(options), 6)
        shadow = result["meta"].get("choice_agent") or {}
        self.assertEqual(shadow.get("mode"), "agent")
        self.assertTrue(shadow.get("applied"))
        keys = [o["key"] for o in options]
        self.assertEqual(keys, ["A", "B", "C", "D", "E", "F"])

    def test_future_knowledge_filtered_in_agent_mode(self):
        """requires_future_knowledge 候选在 agent 模式触发定向重试并被替换。"""
        import json
        bad = [{"text": "直接使用三天后才会获得的玉符", "factor": "金手指",
                "requires_future_knowledge": True},
               {"text": "去听雨楼打听玉符下落", "factor": "金手指"},
               {"text": "返回铁衣堡向堡主复命", "factor": "金手指"},
               {"text": "在坊市低价收购灵砂", "factor": "金手指"},
               {"text": "质问坊市司主簿登记簿去向", "factor": "性格"},
               {"text": "独坐码头看潮起潮落", "factor": "性格"}]
        # 真正的修复必须同时改文本并清除未来知识标志；只换文案不清标志的
        # 候选仍会被 agent 剔除，宁可空榜也不得 fail-open 放行。
        fixed = [dict(item, text="用祖传铜钱向当铺换玉符", requires_future_knowledge=False)
                 if item["text"].startswith("直接使用") else dict(item) for item in bad]

        def fixing_model(prompt: str) -> str:
            payload = fixed if "必须替换为当前可执行的行动" in prompt else bad
            return json.dumps({"options": payload}, ensure_ascii=False)

        with patch.dict("os.environ", {"STORY_CHOICE_AGENT_MODE": "agent"}):
            result = options_service.generate_options(
                client=None, model="fake", provider="deepseek",
                action="测试", model_fn=fixing_model)
        texts = " ".join(o["text"] for o in result["options"])
        self.assertNotIn("直接使用三天后才会获得的玉符", texts)
        self.assertIn("用祖传铜钱向当铺换玉符", texts)
        shadow = result["meta"].get("choice_agent") or {}
        self.assertEqual(shadow.get("mode"), "agent")

    def test_agent_mode_never_breaks_six_contract(self):
        """去重后不足 6 条时不应用过滤（保持前台恰好 6 条契约）。"""
        import json
        dup_model = lambda p: json.dumps({"options": [  # noqa: E731
            {"text": "同一个行动重复一遍", "factor": "金手指"},
            {"text": "同一个行动重复一遍", "factor": "金手指"},
            {"text": "同一个行动重复一遍", "factor": "金手指"},
            {"text": "去听雨楼打听玉符下落", "factor": "金手指"},
            {"text": "返回铁衣堡向堡主复命", "factor": "性格"},
            {"text": "在坊市低价收购灵砂", "factor": "性格"},
        ]}, ensure_ascii=False)
        with patch.dict("os.environ", {"STORY_CHOICE_AGENT_MODE": "agent"}):
            result = options_service.generate_options(
                client=None, model="fake", provider="deepseek",
                action="测试", model_fn=dup_model)
        shadow = result["meta"].get("choice_agent") or {}
        # 重复文本使 kept < 6：不应用，交由既有批改/清洗链处理
        self.assertFalse(shadow.get("applied"))

    def test_invalid_mode_env_falls_back_to_legacy(self):
        with patch.dict("os.environ", {"STORY_CHOICE_AGENT_MODE": "bogus"}):
            self.assertEqual(options_service._choice_agent.mode(), "legacy")
            result = self._generate()
        self.assertNotIn("choice_agent", result["meta"])


if __name__ == "__main__":
    unittest.main()
