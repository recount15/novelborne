# -*- coding: utf-8 -*-
"""engine.opening_distill 开局蒸馏流水线单测（重构 M1）。

FakeModel 按提示词关键字路由（预置 JSON / 坏 JSON / 抛错 / 动态回显），
书籍目录与作品库全部走临时目录注入——绝不动 assets/rules/work_library.md
与真实角色库。覆盖：并行波次产出、第 1 章两遍法、长章 map-reduce、
角色卡质量门（flat 丢弃/playable 入库）、防编造剔除、档案同名覆盖、
模型全线失败的降级路径（不抛错）。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import opening_distill  # noqa: E402

# ---------------------------------------------------------------- 测试书原文

CH1 = ("李青推开城门，风雪扑面而来。他握紧了手中的刀，望向长街尽头的灯火。"
       "白芷在城楼上点灯，影子被拉得很长。这是一个关于复仇与守护的故事的开端。")
CH1_QUOTE = "他握紧了手中的刀，望向长街尽头的灯火。"

CH2 = ("第二日的清晨，雪终于停了。白芷递来一封信，信上只有四个字：城主要见。"
       "李青沉默良久，把信收进了袖中。")

_LONG_PARA = ("北境的战线绵延千里，军旗在风里猎猎作响。斥候来报，敌军已过冰河。"
              "李青下令就地扎营，白芷清点了粮草。夜里他翻开旧地图，圈出三处隘口。")
CH3 = _LONG_PARA * 60  # > 3500 字，触发长章 map-reduce

CH4 = "尾声未至，长夜仍需点灯。城门的钥匙最终交到了谁的手上，无人知晓。"


def make_book_dir() -> Path:
    """临时书籍目录：chapters/0001..0004，第 3 章超长。"""
    root = Path(tempfile.mkdtemp(prefix="opening-book-"))
    chapters = root / "chapters"
    chapters.mkdir(parents=True)
    for number, text in ((1, CH1), (2, CH2), (3, CH3), (4, CH4)):
        (chapters / ("%04d.txt" % number)).write_text(text, encoding="utf-8")
    return root


def make_library_path() -> Path:
    """临时作品库文件（带第六章边界，供追加式 upsert 定位）。"""
    path = Path(tempfile.mkdtemp(prefix="opening-lib-")) / "work_library.md"
    path.write_text("# 作品库\n\n## 第六章 权限与扩展\n", encoding="utf-8")
    return path


def _sentence(text: str, min_len: int = 8) -> str:
    """从文本截一句完整中文句（≥min_len），保证逐字命中校验可通过。"""
    for piece in re.split(r"(?<=[。！？])", text):
        piece = piece.strip()
        if len(piece) >= min_len:
            return piece
    return text[:24]


# ---------------------------------------------------------------- FakeModel


class FakeModel:
    """可编程假模型：按提示词关键字返回预置 JSON / 坏 JSON / 抛错 / 动态回显。

    规则为 (关键字, payload) 列表，按顺序首个命中生效；payload 可以是
    字符串（原样返回）、Exception（抛出）或 callable(prompt)（动态构造）。
    """

    def __init__(self, rules=None, default=None):
        self.rules = list(rules or [])
        self.default = default
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for keyword, payload in self.rules:
            if keyword in prompt:
                if isinstance(payload, BaseException):
                    raise payload
                if callable(payload):
                    return payload(prompt)
                return payload
        if isinstance(self.default, BaseException):
            raise self.default
        return self.default if self.default is not None else "{}"

    def count(self, keyword: str) -> int:
        return sum(1 for prompt in self.prompts if keyword in prompt)


# ---------------------------------------------------------------- 预置回复


def plot_sample_reply(prompt: str) -> str:
    return json.dumps({
        "main_events": ["李青风雪夜入城", "城主递信召见", "北境战事将起"],
        "characters": ["李青", "白芷"],
        "tone": "冷峻苍茫的东方玄幻",
        "threads": ["灭门旧案", "城主之谜", "北境战事"],
    }, ensure_ascii=False)


def plot_merge_reply(prompt: str) -> str:
    return json.dumps({
        "genre": "东方玄幻（复仇流）",
        "premise": "废刀之刃李青重回边城，在城主召见与北境战事之间周旋，誓要查清当年灭门真相。",
        "major_threads": ["灭门旧案", "城主之谜", "北境战事"],
        "tone": "冷峻克制",
    }, ensure_ascii=False)


RICH_CARD = {
    "name": "李青", "gender": "male", "original_position": "主角",
    "archetype": "隐忍复仇者", "desire": "查清灭门真相并复仇", "fear": "再次失去珍视之人",
    "voice": "短句冷峻，少言", "voice_samples": ["风雪要来了。", "刀在人在。"],
    "background": "沈家灭门后隐居边城的旧刀客", "unacceptable_actions": ["伤害无辜", "背叛同伴"],
    "relationship_vector": {"白芷": "互相信任的盟友", "无名氏": "宿敌"},
    "slot_keys": {"主角栏": ["逆袭成长型"], "伴侣栏": ["通用"],
                  "伙伴栏": ["并肩作战型"], "宿敌栏": ["通用"]},
    "evidence_chapter": 1,
}
FLAT_CARD = {"name": "路人乙", "gender": "unknown", "original_position": "配角"}

#: 半成品卡：缺 desire/台词样本/行为禁区 → flat，但重填可救活。
SEMI_CARD = {
    "name": "李青", "gender": "male", "original_position": "主角",
    "fear": "再次失去珍视之人", "voice": "短句冷峻，少言",
    "background": "沈家灭门后隐居边城的旧刀客",
    "relationship_vector": {"白芷": "互相信任的盟友"},
    "slot_keys": {"主角栏": ["通用"], "伴侣栏": ["通用"],
                  "伙伴栏": ["通用"], "宿敌栏": ["通用"]},
    "evidence_chapter": 1,
}


def characters_reply(prompt: str) -> str:
    return json.dumps({"characters": [RICH_CARD, FLAT_CARD]}, ensure_ascii=False)


def flat_refill_reply(prompt: str) -> str:
    """重填一次仍是空洞卡 → 质量门应丢弃并记录。"""
    return json.dumps(FLAT_CARD, ensure_ascii=False)


def rich_refill_reply(prompt: str) -> str:
    """重填返回补全后的完整卡 → 质量门应救活并入库。"""
    return json.dumps(RICH_CARD, ensure_ascii=False)


def archive_reply(prompt: str) -> str:
    return json.dumps({
        "genre": "东方玄幻（复仇流）", "premise": "边城风雪中的复仇与守护。",
        "tier": "T5（系数 WS 9.5）", "language_style": "短句冷峻，意象密集。",
        "pacing": "事件密集，三章一大战。", "anchors": ["城主召见", "北境战起", "真相揭露"],
        "world_will": "旧案必须清算。", "golden_finger_fit": "刀意觉醒系。",
        "entry_point": "风雪夜入城。", "power_system": "刀意九品。",
        "factions": "城主府对北境军。", "timeline": "入城三日内。",
        "causal_rules": "杀戮必留涟漪。",
    }, ensure_ascii=False)


def anchor_pass_reply(prompt: str) -> str:
    """单章锚点蒸馏卷：按 prompt 里的 chapter=N 与原文动态产合法九字段。"""
    number = int(re.search(r"chapter=(\d+)", prompt).group(1))
    original = prompt.split("原文：", 1)[1]
    quote = _sentence(original)
    return json.dumps({
        "chapter": number, "title": "第%d章锚点" % number,
        "summary": _sentence(original), "events": ["主角入城", "城楼点灯"],
        "characters": ["李青", "白芷"], "world": "北境边城风雪连年。",
        "foreshadowing": ["城主的信"], "quotes": [quote], "ripple": "复仇之局初开。",
    }, ensure_ascii=False)


def block_reply(prompt: str) -> str:
    """长章切块蒸馏卷：引文逐字取自块原文。"""
    block_text = prompt.split("【块原文】", 1)[1]
    return json.dumps({
        "summary": "本块：北境战线推进与扎营。",
        "events": ["敌军过冰河", "李青下令扎营"], "characters": ["李青", "白芷"],
        "quotes": [_sentence(block_text)], "world": "北境战线绵延。",
        "foreshadowing": ["三处隘口"], "ripple": "战事升级。",
    }, ensure_ascii=False)


def anchor_merge_reply(prompt: str) -> str:
    """长章合并卷：引文逐字回显自块级结果的 quotes。"""
    section = prompt.split("【分块蒸馏结果】", 1)[1]
    picked = []
    for match in re.finditer(r'"quotes"\s*:\s*\[(.*?)\]', section, re.S):
        for quote in re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)):
            if quote:
                picked.append(quote)
    return json.dumps({
        "chapter": 3, "title": "北境扎营", "summary": "战线推进，沈嬛下令扎营备战。",
        "events": ["敌军过冰河", "李青扎营", "圈定隘口"], "characters": ["李青", "白芷"],
        "world": "北境战线绵延千里。", "foreshadowing": ["三处隘口"],
        "quotes": picked[:3] or [_sentence(section)], "ripple": "战事全面升级。",
    }, ensure_ascii=False)


def verify_reply(prompt: str) -> str:
    """首章核验卷：引文逐字取自章节原文，title 带核验标记。"""
    original = prompt.split("【章节原文】", 1)[1]
    return json.dumps({
        "chapter": 1, "title": "城门风雪（核验）",
        "summary": _sentence(original), "events": ["李青入城", "白芷点灯"],
        "characters": ["李青", "白芷"], "world": "北境边城风雪连年。",
        "foreshadowing": ["城主的信"], "quotes": [_sentence(original), CH1_QUOTE],
        "ripple": "复仇之局经核验。",
    }, ensure_ascii=False)


#: 全量成功路由表：关键字 → 回复（关键字取自各提示词文件的任务标记行；
#: 合并卷规则必须排在采样卷之前——合并卷提示词里也引用了「开局剧情采样」）。
FULL_RULES = [
    ("剧情采样合并", plot_merge_reply),
    ("开局剧情采样", plot_sample_reply),
    ("开局角色抽取", characters_reply),
    ("角色卡重填", flat_refill_reply),
    ("开局作品档案", archive_reply),
    ("单章锚点蒸馏", anchor_pass_reply),
    ("长章切块蒸馏", block_reply),
    ("长章锚点合并", anchor_merge_reply),
    ("首章锚点核验", verify_reply),
]


def capture_saver(bucket: list):
    """注入式入库通道：记录传入卡并回执 id/name（不触真实角色库）。"""
    def saver(cards):
        bucket.extend(cards)
        return [{"id": "user-test-%d" % index, "name": card.get("name")}
                for index, card in enumerate(cards)]
    return saver


# ---------------------------------------------------------------- 测试


class TestOpeningPipeline(unittest.TestCase):
    def setUp(self):
        self.book_dir = make_book_dir()
        self.library_path = make_library_path()
        self.saved_cards: list = []
        self.progress_events: list[dict] = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.book_dir.parent, ignore_errors=True)
        shutil.rmtree(self.library_path.parent, ignore_errors=True)

    def _run(self, rules=None, default=None, chapters_ahead=3):
        model = FakeModel(rules=rules, default=default)
        report = opening_distill.run_opening_pipeline(
            str(self.book_dir), "城门风雪", model,
            chapters_ahead=chapters_ahead,
            progress=self.progress_events.append,
            library_path=str(self.library_path),
            save_characters_fn=capture_saver(self.saved_cards))
        return report, model

    def _anchor_report(self, report, chapter):
        for entry in report["anchors"]:
            if entry.get("chapter") == chapter:
                return entry
        return {}

    # ---- 波次产出与两遍法 / 长章合并 ----

    def test_full_pipeline_waves_two_pass_and_map_reduce(self):
        report, model = self._run(FULL_RULES)
        # 并行波次产出：plot 三采样 + 合并卷都发生过。
        self.assertEqual(model.count("【本卷样本】"), 3)
        self.assertEqual(model.count("剧情采样合并"), 1)
        self.assertEqual(report["plot"]["genre"], "东方玄幻（复仇流）")
        self.assertEqual(len(report["plot"]["major_threads"]), 3)
        self.assertFalse(report["plot_degraded"])
        # 第 1 章两遍法：核验卷被调用且落盘 status=verified。
        self.assertEqual(model.count("首章锚点核验"), 1)
        first = self._anchor_report(report, 1)
        self.assertEqual(first.get("status"), "verified")
        self.assertEqual(first.get("origin"), "distilled")
        # 第 2 章普通路径。
        second = self._anchor_report(report, 2)
        self.assertEqual(second.get("status"), "done")
        self.assertEqual(second.get("origin"), "distilled")
        # 第 3 章长章 map-reduce：切块≥2、合并卷发生、status=merged。
        self.assertGreaterEqual(model.count("长章切块蒸馏"), 2)
        self.assertEqual(model.count("长章锚点合并"), 1)
        third = self._anchor_report(report, 3)
        self.assertEqual(third.get("status"), "merged")
        self.assertEqual(third.get("origin"), "distilled")
        # 落盘锚点九字段且引文逐字命中原文。
        anchor_dir = self.book_dir / "anchors"
        for number, text in ((1, CH1), (2, CH2), (3, CH3)):
            data = json.loads((anchor_dir / ("%04d.json" % number)).read_text(encoding="utf-8"))
            self.assertEqual(set(data), set(("chapter", "title", "summary", "events",
                                             "characters", "world", "foreshadowing",
                                             "quotes", "ripple")))
            for quote in data["quotes"]:
                self.assertIn(quote, text)
        first_data = json.loads((anchor_dir / "0001.json").read_text(encoding="utf-8"))
        self.assertEqual(first_data["title"], "城门风雪（核验）")  # 两遍法第二遍生效
        # 报告结构：timings 存在、errors 为空、可 JSON 化、进度回调发生。
        self.assertIn("total", report["timings"])
        for key in ("wave1", "wave2", "archive", "characters"):
            self.assertIn(key, report["timings"])
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["ok"])
        json.dumps(report, ensure_ascii=False)
        stages = [event["stage"] for event in self.progress_events]
        self.assertIn("start", stages)
        self.assertIn("done", stages)

    def test_character_quality_gate_and_antifabrication(self):
        report, model = self._run(FULL_RULES)
        by_name = {entry["name"]: entry for entry in report["characters"]}
        # playable 卡入库：质量档位达标且经注入通道保存。
        rich = by_name.get("李青")
        self.assertIsNotNone(rich)
        self.assertIn(rich["quality"]["level"], ("playable", "soulful"))
        self.assertTrue(rich["saved"])
        self.assertEqual(self.saved_cards[0]["name"], "李青")
        # flat 卡重填一次仍不过 → 丢弃并记录，不入库。
        flat = by_name.get("路人乙")
        self.assertIsNotNone(flat)
        self.assertFalse(flat["saved"])
        self.assertIn("质量门", flat["dropped_reason"])
        self.assertEqual(model.count("角色卡重填"), 1)
        self.assertEqual(len(self.saved_cards), 1)
        # 防编造：锚点人物名（白芷）保留、编造对象（无名氏）剔除。
        self.assertIn("白芷", self.saved_cards[0]["relationship_vector"])
        self.assertNotIn("无名氏", self.saved_cards[0]["relationship_vector"])
        self.assertIn("无名氏", rich["removed_relation_keys"])
        self.assertEqual(report["character_saved_count"], 1)

    def test_refill_can_save_semi_card(self):
        rules = [
            ("剧情采样合并", plot_merge_reply),
            ("开局剧情采样", plot_sample_reply),
            ("开局角色抽取", lambda p: json.dumps(
                {"characters": [SEMI_CARD]}, ensure_ascii=False)),
            ("角色卡重填", rich_refill_reply),
            ("开局作品档案", archive_reply),
            ("单章锚点蒸馏", anchor_pass_reply),
            ("长章切块蒸馏", block_reply),
            ("长章锚点合并", anchor_merge_reply),
            ("首章锚点核验", verify_reply),
        ]
        report, model = self._run(rules)
        by_name = {entry["name"]: entry for entry in report["characters"]}
        card = by_name.get("李青")
        self.assertIsNotNone(card)
        self.assertEqual(model.count("角色卡重填"), 1)  # flat 触发了一次定向重填
        self.assertIn(card["quality"]["level"], ("playable", "soulful"))
        self.assertTrue(card["saved"])
        self.assertEqual(report["character_saved_count"], 1)
        self.assertEqual(len(self.saved_cards), 1)

    def test_archive_upsert_same_title_overwrites(self):
        actions = []
        for _ in range(2):
            report, _ = self._run(FULL_RULES)
            actions.append((report["work_entry"]["work_id"],
                            report["work_entry"]["action"]))
        # 同名条目：编号不变，第二次整块替换。
        self.assertEqual(actions[0][1], "added")
        self.assertEqual(actions[1][1], "updated")
        self.assertEqual(actions[0][0], actions[1][0])
        text = self.library_path.read_text(encoding="utf-8")
        self.assertIn("### %s · 《城门风雪》" % actions[0][0], text)
        self.assertEqual(text.count("### %s ·" % actions[0][0]), 1)

    def test_bad_json_sample_degrades_gracefully(self):
        rules = [("【本卷样本】第3章", "这不是 JSON{{{"),
                 *FULL_RULES]
        report, _ = self._run(rules)
        # 中间采样卷坏 JSON：其余两卷可用，合并卷仍产出剧情。
        self.assertEqual(report["plot"]["genre"], "东方玄幻（复仇流）")
        self.assertFalse(report["plot_degraded"])
        self.assertEqual(len(report["plot_samples"]), 2)
        self.assertTrue(report["errors"])  # 坏卷失败已记录（中文）

    def test_model_total_failure_never_raises(self):
        report, model = self._run(default=RuntimeError("connection gone"))
        # 模型路径全失败：整体不抛错；第 1 章走原文摘录兜底放行（origin=fallback）。
        self.assertFalse(report["ok"] is None)
        first = self._anchor_report(report, 1)
        self.assertEqual(first.get("origin"), "fallback")
        first_data = json.loads(
            (self.book_dir / "anchors" / "0001.json").read_text(encoding="utf-8"))
        for quote in first_data["quotes"]:
            self.assertIn(quote, CH1)
        # 全线降级：无剧情、无档案、无角色入库，但 errors 全中文记录。
        self.assertIsNone(report["plot"])
        self.assertIsNone(report["work_entry"])
        self.assertEqual(report["characters"], [])
        self.assertTrue(report["errors"])
        for message in report["errors"]:
            self.assertTrue(message, "错误信息不得为空")
        self.assertGreater(model.count("单章锚点蒸馏"), 0)
        self.assertIn("total", report["timings"])

    def test_missing_chapters_returns_error_report(self):
        empty = Path(self.book_dir).parent / "empty-book"
        (empty / "chapters").mkdir(parents=True)
        report = opening_distill.run_opening_pipeline(
            str(empty), "空书", FakeModel(default="{}"),
            library_path=str(self.library_path),
            save_characters_fn=capture_saver([]))
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])
        self.assertIn("章节", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
