# -*- coding: utf-8 -*-
"""C09 摘要、导出与旧档迁移回归（红测试先行）。

验收（计划 §C09 / §3.4）：
- 授权跨摘要不丢失：接手包携带生效愿望授权，保真检查强制其存在；
- 猜测不变真：未生效/来源不明指令归入不确定区，摘要不得写成已确认事实；
- 导出不添加新大设定、风格失败保留忠实稿或明确失败、输出不反写状态；
- 旧档迁移只标 unknown_legacy，绝不洗成原著；原始旧档文件不被批量覆盖；
- 存读档愿望与任务往返保真；
- 章节切分按段落边界可追溯（重放定位）。
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.engine import (context_compressor, directives, fact_contract,
                         novel_exporter, persistence, story_ledger)

WISH_RAW = "我希望北墙的裂痕后面真的有一条暗渠"
WISH_FACT = "北墙裂痕后有暗渠"


def wish_state(status: str = "active") -> dict:
    """构造一条带完整授权记录的愿望账本（纯机制层，零网络）。"""
    state: dict = {"round": 10, "history": [], "ledger": {}}
    entry, _ = directives.parse_registration(
        {"fact_norm": WISH_FACT, "scope": "world", "affected": ["北墙"], "conflicts": []},
        allowed=())
    record = fact_contract.WishAuthorizationRecord(
        authorization_id="auth-1", request_id="req-1",
        raw_text=WISH_RAW, authorized_origin="wish", status=status,
        accepted_interpretation=WISH_FACT if status in ("active", "consumed") else "",
        target_ids=("北墙",))
    directives.register(state, entry, kind="wish", raw=WISH_RAW,
                        authorization=record.to_row_value())
    return state


def legacy_directive_state() -> dict:
    """无回执旧指令行：unknown_legacy，不是授权。"""
    state: dict = {"round": 10, "history": [], "ledger": {}}
    entry, _ = directives.parse_registration(
        {"fact_norm": "旧档疑有暗中监视的组织", "scope": "world",
         "affected": ["北墙"], "conflicts": []}, allowed=())
    directives.register(state, entry, kind="wish")
    return state


# ---------------------------------------------------------------- 摘要传播

class HandoffProvenanceTests(unittest.TestCase):
    """传播来源引用：授权与不确定属性必须跨摘要保留。"""

    def test_build_handoff_carries_active_wish_authorizations(self):
        handoff = context_compressor.build_handoff(wish_state("consumed"))
        auth = handoff.get("wish_authorizations") or []
        self.assertTrue(auth, "生效授权必须进入接手包，否则摘要后愿望丢失")
        self.assertEqual(auth[0]["raw_text"], WISH_RAW)
        self.assertEqual(auth[0]["interpretation"], WISH_FACT)
        self.assertEqual(auth[0]["authorization_id"], "auth-1")

    def test_draft_wish_not_carried_as_authorized(self):
        handoff = context_compressor.build_handoff(wish_state("draft"))
        self.assertEqual(handoff.get("wish_authorizations") or [],
                         [], "未生效愿望不得冒充已生效授权")
        uncertain = json.dumps(handoff.get("uncertain_directives") or [], ensure_ascii=False)
        self.assertIn(WISH_FACT, uncertain, "未生效愿望必须归入不确定区")

    def test_legacy_directive_is_uncertain_not_authorized(self):
        handoff = context_compressor.build_handoff(legacy_directive_state())
        self.assertEqual(handoff.get("wish_authorizations") or [], [])
        self.assertTrue(handoff.get("uncertain_directives"),
                        "来源不明旧指令必须保持 unknown_legacy 属性进入接手包")

    def test_handoff_prompt_states_wish_and_uncertainty_semantics(self):
        prompt = context_compressor.handoff_prompt(context_compressor.build_handoff(wish_state()))
        self.assertIn(WISH_RAW, prompt, "接手包 JSON 必须携带授权原话")
        self.assertIn("玩家授权", prompt)
        self.assertIn("不得写成已确认事实", prompt)

    def test_handoff_for_save_keeps_authorizations(self):
        saved = context_compressor.handoff_for_save(wish_state("consumed"))
        self.assertTrue(saved.get("wish_authorizations"),
                        "轻量接手包随存档走，授权不得在存档侧丢失")

    def test_fidelity_check_requires_active_authorization_text(self):
        state = wish_state("consumed")
        handoff = context_compressor.build_handoff(state)
        lost = [{"role": "assistant", "content": "[接手摘要] 主角继续赶路。"}]
        check = context_compressor.fidelity_check(state, lost, handoff)
        self.assertFalse(check["ok"])
        self.assertIn(WISH_FACT, check["missing"])
        kept = [{"role": "assistant", "content": "[接手摘要] 玩家授权：" + WISH_FACT}]
        self.assertTrue(context_compressor.fidelity_check(state, kept, handoff)["ok"])

    def test_compressed_summary_never_becomes_committed_narrative(self):
        rows = story_ledger.from_history([
            {"role": "assistant", "content": "旧正文。"},
            {"role": "assistant", "content": "[接手摘要] 压缩摘要不算叙事事件。"}])
        self.assertEqual(len(rows), 1, "接手摘要不得被迁移为叙事回合")
        source, kind, meta = story_ledger.narrative_source({
            "history": [{"role": "assistant", "content": "[接手摘要] 任意摘要"}]})
        self.assertEqual(source, [])
        self.assertEqual(kind, "history_fallback")
        self.assertTrue(meta["migration_uncertain"])
        self.assertFalse(meta["complete"])


# ---------------------------------------------------------------- 导出边界

class ExportFidelityTests(unittest.TestCase):
    """忠实导出与风格加工边界：失败保留忠实稿或明确失败，不模板凑数。"""

    @staticmethod
    def _chapter(text: str = "主角推门而入，屋内空无一人。"):
        return [{"idx": 1, "title": "第1章", "text": text}]

    def test_empty_style_output_falls_back_to_previous_draft(self):
        def model(prompt: str) -> str:
            if "文字风格编辑" in prompt:
                return ""  # style 阶段返回空文本：该阶段失败
            return "稿。" + prompt[-300:]
        records = list(novel_exporter.iter_pipeline(
            self._chapter(), model=model, style="webnovel"))
        style_rec = next(r for r in records if r["stage"] == "style")
        self.assertIsNone(style_rec["output"], "空输出不得被当作有效阶段稿")
        self.assertEqual(style_rec.get("fallback"), "empty_output")
        final = novel_exporter.assemble(records)
        self.assertIn("主角推门而入", final,
                      "风格阶段失败必须保留前一稿（忠实版本），不得从空稿续写")

    def test_all_stages_empty_raises_explicit_failure(self):
        records = list(novel_exporter.iter_pipeline(
            self._chapter(), model=lambda prompt: "", style="faithful"))
        with self.assertRaises(ValueError):
            novel_exporter.assemble(records)

    def test_export_novel_manifest_carries_truthful_provenance(self):
        state = {"round": 2, "history": [
            {"role": "assistant", "content": "第一段旧正文。"},
            {"role": "assistant", "content": "第二段旧正文。"}]}
        result = novel_exporter.export_novel(state, model=lambda p: "导出稿。")
        source = result["manifest"]["source"]
        self.assertEqual(source.get("source_kind"), "history_fallback")
        self.assertTrue(source.get("migration_uncertain"), "旧档导出必须如实标注迁移不确定")
        self.assertFalse(source.get("complete"))

    def test_plan_chapters_offsets_tile_original_paragraphs(self):
        paras = ["第%d段%s" % (n, "甲乙丙丁戊己庚辛" * 2) for n in range(1, 7)]
        text = "\n \n \n".join(paras)
        chapters = novel_exporter.plan_chapters(text, target_chars=40)
        self.assertEqual(len(chapters), 3)
        for chapter in chapters:
            self.assertGreaterEqual(chapter["start_char"], 0)
            self.assertLessEqual(chapter["end_char"], len(text))
        for para in paras:
            hits = [c for c in chapters
                    if para in text[c["start_char"]:c["end_char"]]]
            self.assertEqual(len(hits), 1,
                             "段落 %r 必须完整落在唯一章节内（重放追溯）" % para[:6])
        # 同输入切分必须确定一致。
        again = novel_exporter.plan_chapters(text, target_chars=40)
        self.assertEqual([c["text"] for c in again], [c["text"] for c in chapters])

    def test_export_pipeline_never_mutates_source_state(self):
        state = {"round": 1, "history": [], "story_ledger": [{
            "turn_id": "t-1", "round": 1, "chapter": 1, "chapter_round": 1,
            "narrative": "第一章正文。", "options": [], "committed": True}]}
        before = copy.deepcopy(state)
        novel_exporter.export_novel(state, model=lambda p: "导出稿。")
        self.assertEqual(state, before, "导出是只读加工，不得反写任何状态")

    def test_plot_and_style_prompts_forbid_new_major_facts(self):
        self.assertIn("不新增原文没有的重大事实",
                      novel_exporter.build_plot_prompt("正文"))
        self.assertIn("不改变情节走向与人物关系",
                      novel_exporter.build_style_prompt("正文"))


class RunExportTruthTests(unittest.TestCase):
    """导出编排：来源连续性真值必须如实进入清单，不得被洗掉。"""

    @staticmethod
    def _ledger_state() -> dict:
        return {"round": 1, "history": [], "story_ledger": [{
            "turn_id": "t-1", "round": 1, "chapter": 1, "chapter_round": 1,
            "narrative": "第一章正文。", "options": [], "committed": True}]}

    def test_manifest_propagates_continuity_truth(self):
        from core import server
        state = self._ledger_state()
        before = copy.deepcopy(state)
        with patch.object(server.fe, "make_client", lambda *a, **k: object()), \
                patch.object(server, "distill_model", lambda *a, **k: "导出正文。"):
            out = server._run_export(state, "webnovel",
                                     {"provider": "deepseek", "model": "m"})
        self.assertEqual(state, before, "导出失败与否都不得改变对局状态")
        source = out["manifest"]["source"]
        self.assertEqual(source["source_kind"], "story_ledger")
        self.assertTrue(source["complete"], "已验证完整的账本导出不得在清单里谎报未验证")
        self.assertEqual(source["expected_round"], 1)
        self.assertFalse(source["migration_uncertain"])
        self.assertIn("full_text", out)


# ---------------------------------------------------------------- 旧档迁移

class LegacyMigrationTests(unittest.TestCase):
    """旧档只标 unknown_legacy，绝不洗成原著；原始旧档不被批量覆盖。"""

    def test_legacy_history_rows_are_unknown_legacy_and_never_canon(self):
        rows = story_ledger.from_history([{"role": "assistant", "content": "旧正文。"}])
        view = fact_contract.classify_story_ledger_row(rows[0])
        self.assertEqual(view.kind, "unknown_legacy")
        with self.assertRaises(fact_contract.ContractError):
            fact_contract.upgrade_kind("unknown_legacy", "canon")

    def test_wish_and_quest_round_trip_through_save(self):
        state = wish_state("active")
        state.update({
            "system": "rules", "mode": "强化模式", "save_stage": "opening",
            "quest": {"kind": "寻物", "status": "accepted",
                      "settlement_id": "settle-1", "deadline_round": 9},
        })
        with TemporaryDirectory() as tmp:
            persistence.save_state(state, root=tmp, session_id="s9")
            restored = persistence.load_state_strict("latest", root=tmp, session_id="s9")
        self.assertIsNotNone(restored)
        rows = directives.active_directives(restored)
        self.assertEqual(len(rows), 1, "读档后愿望不得丢失")
        view = fact_contract.classify_directive_row(rows[0])
        self.assertEqual(view.kind, "authorized", "读档后生效愿望不得降级或失效")
        record = fact_contract.wish_authorization_of(rows[0])
        self.assertEqual(record.authorization_id, "auth-1")
        self.assertEqual(record.raw_text, WISH_RAW)
        self.assertEqual(restored["quest"]["settlement_id"], "settle-1")
        self.assertEqual(restored["quest"]["deadline_round"], 9)

    def test_legacy_save_import_never_rewrites_original_files(self):
        payload = {
            "schema": "fate-engine-save", "version": 2,
            "state": {"system": "rules", "mode": "基础模式",
                      "options": [{"key": k, "text": "行动" + k} for k in "ABCDEF"]},
            "history": [{"role": "assistant", "content": "旧档正文。"}],
        }
        with TemporaryDirectory() as tmp:
            saves = Path(tmp) / "saves"
            saves.mkdir()
            target = saves / "old-save.json"
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            before_bytes = target.read_bytes()
            first = persistence.list_saves(root=tmp)
            second = persistence.list_saves(root=tmp)
            self.assertEqual([s["save_id"] for s in first if s["save_id"] == "old-save"],
                             ["old-save"])
            self.assertEqual(len([s for s in second if s["save_id"] == "old-save"]), 1,
                             "重复扫描不得重复导入旧档")
            self.assertEqual(target.read_bytes(), before_bytes,
                             "原始旧档文件必须保持原样，不被批量覆盖")
            restored = persistence.load_state_strict("old-save", root=tmp)
            self.assertIsNotNone(restored)
            self.assertEqual(restored["history"][0]["content"], "旧档正文。")


if __name__ == "__main__":
    unittest.main()
