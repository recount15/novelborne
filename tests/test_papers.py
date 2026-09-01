# -*- coding: utf-8 -*-
"""试卷库机制测试：core/engine/papers.py + assets/papers（docs/REFACTOR_PLAN.md §2）。

只读真实资产：全部针对默认试卷库的断言不改写 assets/；构造非法样本时
深拷贝真实记录、写入临时目录后经 ``load_papers(临时目录)`` 校验，
测试结束自动清理，不触碰任何真实文件。
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.engine import papers

_TOP_KEY_ORDER = [
    "tier", "family", "stage", "label", "target_chars", "tolerance",
    "basic_mode", "agent_required", "agent_recommended", "slots", "segments", "options",
]


def _load_record(tier: int = 1, stage: str = "setup") -> dict:
    """从真实资产读一份基准记录（返回独立 dict，供测试安全篡改）。"""
    family = papers.family_for_tier(tier)
    path = papers.PAPER_DIR / f"{family}_l{tier}_{stage}.json"
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class PaperDirectoryTestCase(unittest.TestCase):
    """源码与 PyInstaller frozen 环境的数据目录定位。"""

    def test_source_directory_is_assets_papers(self):
        self.assertEqual(papers.paper_dir(), papers.PAPER_DIR)
        self.assertEqual(papers.paper_dir().name, "papers")
        self.assertEqual(papers.paper_dir().parent.name, "assets")

    def test_frozen_directory_keeps_assets_layer(self):
        fake_meipass = Path(r"C:\fake\bundle\_internal")
        with mock.patch.object(papers.sys, "frozen", True, create=True), \
                mock.patch.object(papers.sys, "_MEIPASS", str(fake_meipass), create=True):
            self.assertEqual(papers.paper_dir(), fake_meipass / "assets" / "papers")


class PaperLibraryTestCase(unittest.TestCase):
    """全部 18 份试卷加载通过严格校验，且资产文件形态合规。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.library = papers.load_papers()

    def test_all_18_papers_load(self):
        self.assertEqual(len(self.library), 18)
        self.assertEqual(set(self.library), papers.EXPECTED_KEYS)

    def test_key_matches_family_tier_stage(self):
        for key, paper in self.library.items():
            self.assertEqual(key, f"{paper.family}_l{paper.tier}_{paper.stage}")
            self.assertEqual(key, paper.key)

    def test_every_stage_tier_combination_present(self):
        for tier in range(1, 7):
            for stage in papers.STAGES:
                self.assertIn(papers.get_paper(tier, stage).key, self.library)

    def test_assets_are_utf8_json_with_consistent_key_order(self):
        paths = sorted(papers.PAPER_DIR.glob("*.json"))
        self.assertEqual(len(paths), 18)
        for path in paths:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"{path.name} 不得带 BOM")
            text = raw.decode("utf-8")
            self.assertTrue(text.endswith("\n"), f"{path.name} 必须以换行收尾")
            pairs = json.loads(text, object_pairs_hook=list)
            self.assertEqual([name for name, _ in pairs], _TOP_KEY_ORDER, path.name)
            for segment in dict(pairs)["segments"]:
                self.assertEqual([name for name, _ in segment],
                                 ["role", "window", "slots", "notes"], path.name)

    def test_target_chars_ladder(self):
        for tier, target in papers.TIER_TARGET_CHARS.items():
            for stage in papers.STAGES:
                self.assertEqual(papers.get_paper(tier, stage).target_chars, target)

    def test_derived_bounds_and_family(self):
        for paper in self.library.values():
            expected_min = round(paper.target_chars * (1 - paper.tolerance))
            expected_max = round(paper.target_chars * (1 + paper.tolerance))
            self.assertEqual(paper.min_chars, expected_min)
            self.assertEqual(paper.max_chars, expected_max)
            self.assertEqual(paper.is_small, paper.tier <= 3)

    def test_mode_flags_by_tier(self):
        for paper in self.library.values():
            self.assertEqual(paper.basic_mode, paper.tier <= 2)
            self.assertEqual(paper.agent_required, paper.tier == 6)
            self.assertEqual(paper.agent_recommended, paper.tier == 5)


class TierGateTestCase(unittest.TestCase):
    """档位门禁：普通限 1–2、6 档强制 agent、5 档 recommended。"""

    def test_normal_mode_limited_to_tiers_1_2(self):
        self.assertEqual(papers.available_tiers("普通", agent_enabled=False), [1, 2])
        self.assertEqual(papers.available_tiers("普通", agent_enabled=True), [1, 2])
        self.assertEqual(papers.available_tiers("", agent_enabled=False), [1, 2])

    def test_enhanced_mode_all_six_tiers(self):
        full = [1, 2, 3, 4, 5, 6]
        self.assertEqual(papers.available_tiers("强化", agent_enabled=False), full)
        self.assertEqual(papers.available_tiers("强化模式", agent_enabled=True), full)

    def test_normal_mode_rejects_tier_3_and_above(self):
        for tier in (3, 4, 5, 6):
            ok, reason = papers.validate_selection(tier, "普通", agent_enabled=True)
            self.assertFalse(ok, tier)
            self.assertIn("普通模式", reason)
            self.assertIn("强化", reason)

    def test_normal_mode_accepts_tiers_1_2(self):
        for tier in (1, 2):
            ok, reason = papers.validate_selection(tier, "普通", agent_enabled=False)
            self.assertTrue(ok, tier)
            self.assertEqual(reason, "")

    def test_tier_6_requires_agent(self):
        ok, reason = papers.validate_selection(6, "强化", agent_enabled=False)
        self.assertFalse(ok)
        self.assertIn("史诗丰度需开启类 agent 批改重填", reason)
        ok, _ = papers.validate_selection(6, "强化", agent_enabled=True)
        self.assertTrue(ok)

    def test_tier_5_agent_recommended_but_not_required(self):
        ok, reason = papers.validate_selection(5, "强化", agent_enabled=False)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        paper = papers.get_paper(5, "setup")
        self.assertTrue(paper.agent_recommended)
        self.assertFalse(paper.agent_required)
        self.assertTrue(papers.get_paper(6, "setup").agent_required)

    def test_enhanced_tiers_3_to_5_usable_without_agent(self):
        for tier in (3, 4, 5):
            ok, _ = papers.validate_selection(tier, "强化", agent_enabled=False)
            self.assertTrue(ok, tier)

    def test_invalid_tier_rejected(self):
        for tier in (0, 7, -1, None, "x"):
            ok, reason = papers.validate_selection(tier, "强化", agent_enabled=True)
            self.assertFalse(ok, tier)
            self.assertTrue(reason)


class SlotConsistencyTestCase(unittest.TestCase):
    """双族槽位一致性：1–3 档 slots 相同、4–6 档相同、大卷=小卷全集+4 扩展槽。"""

    def test_small_tiers_share_identical_slots(self):
        for stage in papers.STAGES:
            base = papers.get_paper(1, stage).slots
            for tier in (2, 3):
                self.assertEqual(papers.get_paper(tier, stage).slots, base,
                                 f"tier {tier} / {stage}")

    def test_large_tiers_share_identical_slots(self):
        for stage in papers.STAGES:
            base = papers.get_paper(4, stage).slots
            for tier in (5, 6):
                self.assertEqual(papers.get_paper(tier, stage).slots, base,
                                 f"tier {tier} / {stage}")

    def test_large_family_extends_small_with_four_slots(self):
        for stage in papers.STAGES:
            small = set(papers.get_paper(3, stage).slots)
            large = set(papers.get_paper(4, stage).slots)
            self.assertEqual(large - small, set(papers.LARGE_EXTRA_SLOTS))
            self.assertEqual(len(small), 8)
            self.assertEqual(len(large), 12)

    def test_anchor_slot_follows_stage(self):
        for stage in papers.STAGES:
            for tier in range(1, 7):
                slots = papers.get_paper(tier, stage).slots
                self.assertIn(f"anchor_{stage}", slots, (tier, stage))
                for other in papers.STAGES:
                    if other != stage:
                        self.assertNotIn(f"anchor_{other}", slots, (tier, stage))

    def test_all_slots_registered(self):
        for paper in papers.load_papers().values():
            for slot in paper.slots:
                self.assertIn(slot, papers.SLOT_TYPES)
                self.assertTrue(papers.SLOT_TYPES[slot]["desc"].strip())
        self.assertEqual(len(papers.SLOT_TYPES), 14)
        for slot in papers.LARGE_EXTRA_SLOTS:
            self.assertEqual(papers.SLOT_TYPES[slot]["family"], "large")

    def test_every_slot_assigned_to_a_segment(self):
        for paper in papers.load_papers().values():
            assigned = {slot for segment in paper.segments for slot in segment.slots}
            self.assertEqual(assigned, set(paper.slots), paper.key)


class SegmentWindowTestCase(unittest.TestCase):
    """段数与段窗口求和达标（target×(1±tolerance+0.05)）。"""

    def test_segment_counts_by_tier(self):
        for tier, count in papers.EXPECTED_SEGMENT_COUNTS.items():
            for stage in papers.STAGES:
                self.assertEqual(papers.get_paper(tier, stage).segment_count, count,
                                 f"tier {tier} / {stage}")

    def test_window_sums_within_tolerance(self):
        for paper in papers.load_papers().values():
            low_sum, high_sum = paper.window_sum
            slack = paper.tolerance + papers.WINDOW_SUM_SLACK
            self.assertGreaterEqual(low_sum, round(paper.target_chars * (1 - slack)),
                                    paper.key)
            self.assertLessEqual(high_sum, round(paper.target_chars * (1 + slack)),
                                 paper.key)
            mid = (low_sum + high_sum) / 2
            self.assertLessEqual(abs(mid - paper.target_chars),
                                 paper.target_chars * slack + 1e-6, paper.key)

    def test_segment_windows_well_formed(self):
        for paper in papers.load_papers().values():
            for segment in paper.segments:
                low, high = segment.window
                self.assertGreater(low, 0)
                self.assertLessEqual(low, high)

    def test_climax_last_segment_carries_convergence_slot(self):
        for tier in range(1, 7):
            last = papers.get_paper(tier, "climax").segments[-1]
            self.assertIn("anchor_climax", last.slots, f"tier {tier}")

    def test_free_anchor_segment_notes_reference_only(self):
        for tier in range(1, 7):
            paper = papers.get_paper(tier, "free")
            carriers = [segment for segment in paper.segments
                        if "anchor_free" in segment.slots]
            self.assertTrue(carriers, f"tier {tier}")
            self.assertTrue(any("仅供参考，不强制收束" in segment.notes
                                for segment in carriers), f"tier {tier}")


class GetPaperTestCase(unittest.TestCase):
    """get_paper 的档位→族映射与入参校验。"""

    def test_family_mapping_1_to_3_small(self):
        for tier in (1, 2, 3):
            for stage in papers.STAGES:
                paper = papers.get_paper(tier, stage)
                self.assertEqual(paper.family, "small")
                self.assertTrue(paper.is_small)
                self.assertEqual(paper.tier, tier)
                self.assertEqual(paper.stage, stage)

    def test_family_mapping_4_to_6_large(self):
        for tier in (4, 5, 6):
            for stage in papers.STAGES:
                paper = papers.get_paper(tier, stage)
                self.assertEqual(paper.family, "large")
                self.assertFalse(paper.is_small)

    def test_invalid_tier_raises(self):
        for tier in (0, 7, None, "3x"):
            with self.assertRaises(ValueError):
                papers.get_paper(tier, "setup")

    def test_invalid_stage_raises(self):
        with self.assertRaises(ValueError):
            papers.get_paper(1, "midgame")

    def test_default_stage_is_setup(self):
        self.assertEqual(papers.get_paper(2).stage, "setup")


class MapLegacyRichnessTestCase(unittest.TestCase):
    """旧 300–1000 丰富度 → 1–6 档的就近映射边界。"""

    def test_lower_band_maps_to_tier_1(self):
        for value in (300, 400, 450, 499, 500):
            self.assertEqual(papers.map_legacy_richness(value), 1, value)

    def test_middle_band_maps_to_tier_2(self):
        for value in (501, 550, 600, 650, 675):
            self.assertEqual(papers.map_legacy_richness(value), 2, value)

    def test_upper_band_maps_to_tier_3(self):
        for value in (676, 700, 820, 900, 1000):
            self.assertEqual(papers.map_legacy_richness(value), 3, value)

    def test_out_of_range_values_clamped(self):
        # >1000 是旧刻度之外的概念值，防御性钳制为 3 档（小卷族顶格）。
        self.assertEqual(papers.map_legacy_richness(1200), 3)
        self.assertEqual(papers.map_legacy_richness(0), 3)
        self.assertEqual(papers.map_legacy_richness(-50), 3)

    def test_invalid_input_uses_legacy_default(self):
        for value in (None, "abc", "", object()):
            # 旧默认 700 → 新默认 3 档（计划 §2：3 标准（默认））。
            self.assertEqual(papers.map_legacy_richness(value), 3, repr(value))

    def test_legacy_mapping_stays_in_small_family(self):
        for value in range(300, 1001, 25):
            self.assertLessEqual(papers.map_legacy_richness(value), 3, value)


class StageForTestCase(unittest.TestCase):
    """stage_for 三态：shattered→free、预算尾声→climax、其余 setup。"""

    def test_shattered_wins_even_at_budget_end(self):
        self.assertEqual(papers.stage_for(1, 5, shattered=True), "free")
        self.assertEqual(papers.stage_for(9, 5, shattered=True), "free")

    def test_budget_end_maps_to_climax(self):
        self.assertEqual(papers.stage_for(5, 5, shattered=False), "climax")
        self.assertEqual(papers.stage_for(6, 5, shattered=False), "climax")

    def test_before_budget_end_maps_to_setup(self):
        self.assertEqual(papers.stage_for(1, 5, shattered=False), "setup")
        self.assertEqual(papers.stage_for(4, 5, shattered=False), "setup")

    def test_first_round_of_single_budget_is_climax(self):
        self.assertEqual(papers.stage_for(1, 1, shattered=False), "climax")

    def test_degenerate_inputs_align_with_anchor_gate(self):
        # 对齐 core.app._anchor_gate_ok：无法解析的输入按 1/1 归一 → 视为预算尾声。
        self.assertEqual(papers.stage_for(None, None, shattered=False), "climax")
        self.assertEqual(papers.stage_for("x", "y", shattered=False), "climax")
        self.assertEqual(papers.stage_for(None, None, shattered=True), "free")


class StrictValidationTestCase(unittest.TestCase):
    """加载期严格校验：篡改样本写入临时目录，任何一份不合法抛 ValueError（带文件名）。"""

    def _load_invalid(self, mutate, tier=1, stage="setup", filename=None):
        record = _load_record(tier, stage)
        record = copy.deepcopy(record)
        mutate(record)
        name = filename or f"{record.get('family')}_l{record.get('tier')}_{record.get('stage')}.json"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            papers.load_papers.cache_clear()
            self.addCleanup(papers.load_papers.cache_clear)
            with self.assertRaises(ValueError) as ctx:
                papers.load_papers(Path(tmp))
        return str(ctx.exception)

    def test_missing_field_rejected(self):
        def mutate(record):
            del record["label"]
        message = self._load_invalid(mutate)
        self.assertIn("small_l1_setup.json", message)
        self.assertIn("label", message)

    def test_family_tier_mismatch_rejected(self):
        def mutate(record):
            record["tier"], record["family"] = 4, "small"
        message = self._load_invalid(mutate, tier=1, stage="setup",
                                     filename="small_l4_setup.json")
        self.assertIn("应属 large", message)

    def test_invalid_stage_rejected(self):
        def mutate(record):
            record["stage"] = "midgame"
        message = self._load_invalid(mutate, filename="small_l1_midgame.json")
        self.assertIn("stage", message)

    def test_unregistered_slot_rejected(self):
        def mutate(record):
            record["slots"].append("time_travel")
        message = self._load_invalid(mutate)
        self.assertIn("未注册槽位", message)

    def test_missing_small_slot_rejected(self):
        def mutate(record):
            record["slots"].remove("ripple_cost")
            for segment in record["segments"]:
                segment["slots"] = [s for s in segment["slots"] if s != "ripple_cost"]
        message = self._load_invalid(mutate)
        self.assertIn("ripple_cost", message)

    def test_window_sum_out_of_range_rejected(self):
        def mutate(record):
            for segment in record["segments"]:
                segment["window"] = [segment["window"][0] * 2, segment["window"][1] * 2]
        message = self._load_invalid(mutate, tier=3, stage="setup")
        self.assertIn("上界之和", message)

    def test_wrong_segment_count_rejected(self):
        def mutate(record):
            record["segments"] = record["segments"][:1]
        message = self._load_invalid(mutate, tier=3, stage="setup")
        self.assertIn("段数应为 3", message)

    def test_option_count_rejected(self):
        def mutate(record):
            record["options"]["count"] = 5
        message = self._load_invalid(mutate)
        self.assertIn("options.count 必须为 6", message)

    def test_factor_split_sum_rejected(self):
        def mutate(record):
            record["options"]["factor_split"] = {"golden_finger": 3, "persona": 2}
        message = self._load_invalid(mutate)
        self.assertIn("factor_split 合计必须为 6", message)

    def test_basic_mode_flag_on_tier_3_rejected(self):
        def mutate(record):
            record["basic_mode"] = True
        message = self._load_invalid(mutate, tier=3, stage="setup")
        self.assertIn("basic_mode", message)

    def test_agent_required_flag_on_tier_5_rejected(self):
        def mutate(record):
            record["agent_required"] = True
        message = self._load_invalid(mutate, tier=5, stage="setup")
        self.assertIn("agent_required", message)

    def test_wrong_target_chars_rejected(self):
        def mutate(record):
            record["target_chars"] = 500
        message = self._load_invalid(mutate, tier=2, stage="setup")
        self.assertIn("target_chars 应为 650", message)

    def test_climax_without_last_segment_anchor_rejected(self):
        def mutate(record):
            record["segments"][0]["slots"].append("anchor_climax")
            record["segments"][-1]["slots"] = [
                s for s in record["segments"][-1]["slots"] if s != "anchor_climax"]
        message = self._load_invalid(mutate, tier=2, stage="climax")
        self.assertIn("收束槽", message)

    def test_free_without_reference_note_rejected(self):
        def mutate(record):
            for segment in record["segments"]:
                segment["notes"] = "普通合约提示，无参考声明。"
        message = self._load_invalid(mutate, tier=1, stage="free")
        self.assertIn("仅供参考，不强制收束", message)

    def test_filename_key_mismatch_rejected(self):
        def mutate(record):
            return None
        message = self._load_invalid(mutate, tier=1, stage="setup", filename="oops.json")
        self.assertIn("文件名必须与试卷键一致", message)

    def test_incomplete_library_rejected(self):
        record = _load_record(1, "setup")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small_l1_setup.json"
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            papers.load_papers.cache_clear()
            self.addCleanup(papers.load_papers.cache_clear)
            with self.assertRaises(ValueError) as ctx:
                papers.load_papers(Path(tmp))
        self.assertIn("试卷库不完整", str(ctx.exception))

    def test_missing_directory_rejected(self):
        papers.load_papers.cache_clear()
        self.addCleanup(papers.load_papers.cache_clear)
        with self.assertRaises(ValueError) as ctx:
            papers.load_papers(Path("Z:/definitely/not/here"))
        self.assertIn("试卷库目录不存在", str(ctx.exception))


class SlotRegistryTestCase(unittest.TestCase):
    """槽位注册表与工具函数的一致性。"""

    def test_expected_slots_matches_assets(self):
        for stage in papers.STAGES:
            self.assertEqual(tuple(papers.get_paper(1, stage).slots),
                             papers.expected_slots("small", stage))
            self.assertEqual(tuple(papers.get_paper(4, stage).slots),
                             papers.expected_slots("large", stage))

    def test_anchor_slot_for_all_stages(self):
        for stage in papers.STAGES:
            self.assertEqual(papers.anchor_slot_for(stage), f"anchor_{stage}")

    def test_family_for_tier_boundaries(self):
        self.assertEqual(papers.family_for_tier(3), "small")
        self.assertEqual(papers.family_for_tier(4), "large")


if __name__ == "__main__":
    unittest.main()
