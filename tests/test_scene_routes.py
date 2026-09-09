# -*- coding: utf-8 -*-
"""开局定位 / 作品库 / 准备状态路由：只覆盖服务层契约，不做真实模型调用。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from core import server


def _make_book(root: Path) -> Path:
    """三章微型书：切章索引 + 章节文件 + 已玩标记，供定位与作品库路由使用。"""
    book = root / "books" / "demo"
    (book / "chapters").mkdir(parents=True)
    (book / "chapter_index.json").write_text(json.dumps({"book_id": "demo"}), encoding="utf-8")
    texts = ["Alice enters room 1. Bob waits.", "Alice meets Bob in room 2. Bob leaves room 2.",
             "Bob rests in room 3."]
    for i, text in enumerate(texts, 1):
        (book / "chapters" / f"{i:04d}.txt").write_text(text, encoding="utf-8")
    from core.services.book_prepare_service import prepare_book
    prepare_book(book)
    return book


class SceneRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(_tempdir()))
        self.book = _make_book(self.tmp)
        self.client = TestClient(server.app)
        patcher = mock.patch.object(server, "_resolve_book_dir", lambda bid: self.tmp / "books" / bid)
        self.enterContext(patcher)

    def test_locate_exact_and_select_timepoints(self):
        located = self.client.post("/api/books/demo/locate",
                                   json={"query": "Bob leaves room 2"}).json()
        candidates = located["candidates"]
        self.assertTrue(candidates and candidates[0]["match"] == "exact")
        top = candidates[0]
        selected = self.client.post("/api/books/demo/locate/select",
                                    json={"candidate": top, "timepoint": "after"}).json()
        self.assertEqual(selected["evidence"]["excerpt"], "Bob leaves room 2")
        # after 时点截止在第 2 章事件末尾；before 截止在事件开始处
        self.assertEqual(selected["knowledge_cutoff"]["chapter_no"], 2)
        before = self.client.post("/api/books/demo/locate/select",
                                  json={"candidate": top, "timepoint": "before"}).json()
        self.assertEqual(before["knowledge_cutoff"]["offset"], top["start"])
        self.assertIsInstance(before["initial_facts"], list)

    def test_semantic_requires_api_key(self):
        response = self.client.post("/api/books/demo/locate",
                                    json={"query": "离别", "semantic": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("API Key", response.json()["detail"])

    def test_locate_evidence_display_capped_with_total(self):
        # 长块证据：响应必须给出显式截断上限（excerpt_display）与总数
        # （excerpt_total）；excerpt 本体保持原文精确子串供 select 校验。
        from core.services.scene_locator_service import EXCERPT_DISPLAY_LIMIT
        long_book = self.tmp / "books" / "longdemo"
        (long_book / "chapters").mkdir(parents=True)
        (long_book / "chapter_index.json").write_text(
            json.dumps({"book_id": "longdemo"}), encoding="utf-8")
        long_text = ("渡口的老船夫数着浪头。" * 40) + "少年背着剑抵达渡口。" + ("浪头一个接一个。" * 10)
        (long_book / "chapters" / "0001.txt").write_text(long_text, encoding="utf-8")
        from core.services.book_prepare_service import prepare_book
        prepare_book(long_book)
        exact_query = long_text[:200]
        located = self.client.post("/api/books/longdemo/locate",
                                   json={"query": exact_query}).json()
        top = located["candidates"][0]
        self.assertEqual(top["match"], "exact")
        self.assertEqual(top["excerpt_total"], 200)
        self.assertEqual(top["excerpt_display"],
                         exact_query[:EXCERPT_DISPLAY_LIMIT] + "…")

    def test_select_rejects_incomplete_candidate_identity(self):
        # 旧前端只回传 4 字段子集：必须显式指出证据身份不完整，
        # 而不是误导性的 "scene evidence is stale"。
        located = self.client.post("/api/books/demo/locate",
                                   json={"query": "Bob leaves room 2"}).json()
        top = located["candidates"][0]
        partial = {"chapter_no": top["chapter_no"], "start": top["start"],
                   "end": top["end"], "during_offset": top.get("during_offset")}
        response = self.client.post("/api/books/demo/locate/select",
                                    json={"candidate": partial, "timepoint": "after"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("不完整", response.json()["detail"])

    def test_select_response_contract_fields(self):
        located = self.client.post("/api/books/demo/locate",
                                   json={"query": "Bob leaves room 2"}).json()
        top = located["candidates"][0]
        selected = self.client.post("/api/books/demo/locate/select",
                                    json={"candidate": top, "timepoint": "after"}).json()
        for key in ("id", "timepoint", "evidence", "candidate",
                    "temporal_precision", "knowledge_cutoff",
                    "initial_facts", "initialization_policy"):
            self.assertIn(key, selected)
        # 前端旧类型声明的 book_id/selected 并非服务端契约，锚定真实形状
        self.assertNotIn("selected", selected)
        self.assertNotIn("book_id", selected)

    def test_locate_semantic_honors_snake_case_credentials(self):
        def fake_model(prompt):
            payload = json.loads(prompt.rsplit("\n", 1)[-1])
            text = payload["text"]
            needle = "Bob leaves room 2"
            start = text.find(needle)
            if start < 0:
                return json.dumps({"scenes": []})
            return json.dumps({"scenes": [{
                "start": start, "end": start + len(needle),
                "during_offset": start + len(needle) // 2,
                "excerpt": needle, "reason": "退场事件证据"}]}, ensure_ascii=False)
        with mock.patch.object(server, "_request_model_callable",
                               return_value=fake_model):
            response = self.client.post(
                "/api/books/demo/locate",
                json={"query": "谁离开了房间", "semantic": True, "api_key": "占位Key"})
        self.assertEqual(response.status_code, 200)
        candidates = response.json()["candidates"]
        self.assertTrue(candidates)
        top = candidates[0]
        self.assertEqual(top["match"], "semantic")
        self.assertIn("during_offset", top)
        self.assertIn("excerpt_total", top)
        self.assertIn("excerpt_display", top)

    def test_preparation_status_is_readonly(self):
        result = self.client.get("/api/books/demo/preparation",
                                 params={"mode": "window"}).json()
        self.assertIn("ready", result)

    def test_playable_library_requires_played_marker(self):
        empty = self.client.get("/api/library/playable").json()
        self.assertEqual(empty["books"], [])
        self.assertTrue(self.book.exists())


def _tempdir():
    import tempfile
    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
