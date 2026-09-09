# -*- coding: utf-8 -*-
"""金手指完整规格链路（D02/D03/D04）：propose/confirm 契约与开局规格合并。

契约基准：前端 types.ts GoldenFingerProposal
  { status, attempt, remaining, spec }
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from core import server
from core.engine import golden_finger as gf_engine
from core.services import golden_finger_service


class ProposeRouteContractTest(unittest.TestCase):
    """D02：新校验分支必须产出完整规格且契约与前端一致；回落必须留痕。"""

    def setUp(self):
        self.client = TestClient(server.app)

    def _propose(self, text: str, difficulty: str = "D5 普通", attempt: int = 1):
        return self.client.post("/api/golden-fingers/propose", json={
            "text": text, "world": "修仙", "persona": "谨慎",
            "difficulty": difficulty, "attempt": attempt,
        })

    def test_new_branch_returns_full_spec_contract(self):
        resp = self._propose("能查阅方圆十里内动物记忆的耳饰")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "await_confirmation")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["remaining"], 2)
        spec = body["spec"]
        for field in ("name", "cost", "cooldown", "effect", "limits"):
            self.assertTrue(str(spec.get(field) or "").strip(), f"spec 缺 {field}")
        self.assertEqual(body["defaulted_fields"], ["composition", "cost", "cooldown"])
        self.assertNotIn("fallback_reason", body)

    def test_unobservable_text_wrapped_by_template(self):
        """缺可观察标记词的自然语言：用「信息」模板包装后仍可产出完整规格。"""
        resp = self._propose("能听懂方圆十里动物交谈的耳饰")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "await_confirmation")
        spec = body["spec"]
        self.assertIn("读取", spec["effect"])  # 模板包装出可观察动作
        self.assertIn("自定义·", spec["name"])
        self.assertNotIn("fallback_reason", body)

    def test_forbidden_word_falls_back_with_reason(self):
        resp = self._propose("获得无敌且无限的力量")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # 回落 legacy 也必须维持同一契约外壳
        self.assertEqual(body["status"], "await_confirmation")
        self.assertIn("fallback_reason", body)

    def test_legacy_proposal_passes_confirm_validation(self):
        """legacy 回落提案的 limits 已对齐锁定措辞，不得被 confirm 校验误杀。"""
        legacy = gf_engine.propose_custom("读心但每日一次", difficulty="D5")
        check = golden_finger_service.validate_spec(legacy["spec"], "D5")
        self.assertTrue(check["ok"], check["issues"])


class ConfirmRouteValidationTest(unittest.TestCase):
    """D04：confirm 成败由服务端校验决定，不信任客户端 status。"""

    def setUp(self):
        self.client = TestClient(server.app)

    def _propose(self):
        return self.client.post("/api/golden-fingers/propose", json={
            "text": "能听懂动物语言的耳饰", "world": "修仙", "persona": "谨慎",
            "difficulty": "D5 普通", "attempt": 1,
        }).json()

    def test_confirm_accepts_valid_proposal(self):
        proposal = self._propose()
        resp = self.client.post("/api/golden-fingers/confirm",
                                json={"proposal": proposal})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "confirmed")
        self.assertTrue(body.get("validated"))

    def test_confirm_rejects_tampered_status(self):
        proposal = self._propose()
        proposal["status"] = "confirmed"  # 客户端伪造已确认状态
        resp = self.client.post("/api/golden-fingers/confirm",
                                json={"proposal": proposal})
        self.assertEqual(resp.status_code, 400)

    def test_confirm_rejects_incomplete_spec(self):
        proposal = self._propose()
        proposal["spec"] = {"name": "残缺规格"}  # 无 cost/cooldown/limits
        resp = self.client.post("/api/golden-fingers/confirm",
                                json={"proposal": proposal})
        self.assertEqual(resp.status_code, 400)

    def test_confirm_rejects_invalid_spec_content(self):
        proposal = self._propose()
        proposal["spec"]["cost"] = "无"  # 违反质量门：无代价
        resp = self.client.post("/api/golden-fingers/confirm",
                                json={"proposal": proposal})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("代价", resp.json()["detail"])


class StartSpecShapeTest(unittest.TestCase):
    """D03 服务端门禁：开局规格不完整直接拒绝，不静默丢字段。"""

    def test_none_passes_through(self):
        self.assertIsNone(server._validated_gf_spec(None))

    def test_complete_spec_passes(self):
        spec = {"name": "观察回响", "cost": "精神负荷", "cooldown": "每日一次",
                "effect": "可验证因果", "limits": "不得抹除既成事实"}
        self.assertEqual(server._validated_gf_spec(spec), spec)

    def test_incomplete_spec_rejected_with_missing_fields(self):
        with self.assertRaises(server.HTTPException) as ctx:
            server._validated_gf_spec({"name": "残缺", "cost": ""})
        self.assertIn("cooldown", str(ctx.exception.detail))
        self.assertIn("effect", str(ctx.exception.detail))


class MergeSpecTest(unittest.TestCase):
    """D03 合并：推荐选择用完整规格覆盖残缺 spec；无/自定义不覆盖。"""

    def _decision(self):
        return gf_engine.resolve("世界规则适配·观察回响｜适合谨慎")

    def test_recommendation_gets_full_spec(self):
        full = {"name": "观察回响", "cost": "精神负荷", "cooldown": "每日一次",
                "effect": "因果回响", "limits": "锁定限制"}
        merged = __import__("core.app", fromlist=["_merge_gf_spec"])._merge_gf_spec(
            self._decision(), full, "世界规则适配·观察回响｜适合谨慎")
        self.assertEqual(merged["spec"], full)

    def test_custom_selection_not_overridden(self):
        full = {"name": "X", "cost": "c", "cooldown": "d"}
        decision = gf_engine.resolve("自定义（由系统正式化后确认）")
        merged = __import__("core.app", fromlist=["_merge_gf_spec"])._merge_gf_spec(
            decision, full, "自定义（由系统正式化后确认）")
        self.assertEqual(merged["spec"], decision["spec"])

    def test_none_selection_not_overridden(self):
        decision = gf_engine.resolve("无（凡人开局）")
        merged = __import__("core.app", fromlist=["_merge_gf_spec"])._merge_gf_spec(
            decision, {"name": "X"}, "无（凡人开局）")
        self.assertEqual(merged["spec"], decision["spec"])


if __name__ == "__main__":
    unittest.main()
