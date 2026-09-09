# -*- coding: utf-8 -*-
"""T09：定位确认结果接入开局初始化（Q08 简版时点一致性）。

select 路由的确认结果（时点/知识截止/截点前事实）必须随 start 提交进入
会话：start_params 持久化、knowledge_cutoff/scene_timepoint 写入、截点前
人物观察写入统一角色状态。事实不信任客户端——按 knowledge_cutoff 坐标
重新过滤，未来知识绝不进入开局状态。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.app import _apply_scene_selection, _selection_target_chapter
from core.services.scene_locator_service import locate_scene, select_scene
from core.server import StartRequest


def _tempdir():
    import tempfile
    return tempfile.TemporaryDirectory()


def _make_book(root: Path) -> Path:
    book = root / "books" / "demo"
    (book / "chapters").mkdir(parents=True)
    (book / "chapter_index.json").write_text(json.dumps({"book_id": "demo"}), encoding="utf-8")
    texts = ["Alice enters room 1. Bob waits.",
             "Alice meets Bob in room 2. Bob leaves room 2.",
             "Bob rests in room 3."]
    for i, text in enumerate(texts, 1):
        (book / "chapters" / f"{i:04d}.txt").write_text(text, encoding="utf-8")
    from core.services.book_prepare_service import prepare_book
    prepare_book(book)
    return book


class StartSceneSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(_tempdir()))
        self.book = _make_book(self.tmp)

    def _selection(self, timepoint="after"):
        candidates = locate_scene(self.book, "Bob leaves room 2")
        self.assertTrue(candidates)
        selection = select_scene(self.book, candidates[0], timepoint)
        cutoff = selection["knowledge_cutoff"]
        # 构造带实体观察的事实载荷：截点前(ch1 Alice/Bob)、同章截点后(future)、
        # 截点后章节(ch3)、非人物(place) 各一条——模拟就绪准备的 initial_facts。
        selection["initial_facts"] = [
            {"mention_id": "f1", "name": "Alice", "kind": "character",
             "excerpt": "Alice enters room 1", "chapter_no": 1, "end": 18},
            {"mention_id": "f2", "name": "Bob", "kind": "character",
             "excerpt": "Bob waits", "chapter_no": 1, "end": 33},
            {"mention_id": "f3", "name": "Bob", "kind": "character",
             "excerpt": "同章截点后的未来事实", "chapter_no": cutoff["chapter_no"],
             "end": cutoff["offset"] + 5},
            {"mention_id": "f4", "name": "Bob", "kind": "character",
             "excerpt": "Bob rests in room 3", "chapter_no": 3, "end": 99},
            {"mention_id": "f5", "name": "room 2", "kind": "place",
             "excerpt": "room 2", "chapter_no": 1, "end": 20},
        ]
        return selection

    def test_selection_writes_cutoff_timepoint_and_precutoff_facts(self):
        selection = self._selection("after")
        state = {"start_params": {}, "character_states": {}}
        _apply_scene_selection(state, selection)

        self.assertEqual(state["knowledge_cutoff"], selection["knowledge_cutoff"])
        self.assertEqual(state["scene_timepoint"], "after")
        persisted = state["start_params"]["scene_selection"]
        self.assertEqual(persisted["id"], selection["id"])
        self.assertEqual(persisted["knowledge_cutoff"], selection["knowledge_cutoff"])
        self.assertEqual(persisted["timepoint"], "after")

        # 截点前人物事实进入统一角色状态；每名角色一条 source_fact 断言。
        char_states = state["character_states"]
        self.assertIn("Alice", char_states)
        self.assertIn("Bob", char_states)
        self.assertNotIn("room 2", char_states)
        alice_assertions = char_states["Alice"]["assertions"]
        self.assertEqual(len(alice_assertions), 1)
        row = alice_assertions[0]
        self.assertEqual(row["key"], "source_fact")
        self.assertEqual(row["value"], "Alice enters room 1")
        provenance = row["provenance"][0]
        self.assertEqual(provenance["source"], "scene_selection")

        # Q08 简版：未来事实（同章截点后 + 截点后章节）绝不进入开局状态。
        bob_values = [a["value"] for a in char_states["Bob"]["assertions"]]
        self.assertEqual(bob_values, ["Bob waits"])
        all_values = [a["value"] for name in char_states for a in char_states[name]["assertions"]]
        self.assertNotIn("同章截点后的未来事实", all_values)
        self.assertNotIn("Bob rests in room 3", all_values)

    def test_invalid_selection_is_noop(self):
        for bad in (None, {}, {"knowledge_cutoff": None},
                    {"knowledge_cutoff": {"chapter_no": "x", "offset": 1}},
                    "not-a-dict"):
            state = {"start_params": {}, "character_states": {}}
            _apply_scene_selection(state, bad)
            self.assertEqual(state, {"start_params": {}, "character_states": {}}, msg=repr(bad))

    def test_selection_chapter_drives_target(self):
        selection = self._selection()
        self.assertEqual(_selection_target_chapter(selection, 1),
                         selection["evidence"]["chapter_no"])
        self.assertEqual(_selection_target_chapter(None, 7), 7)
        self.assertEqual(_selection_target_chapter({"evidence": {}}, 7), 7)
        self.assertEqual(
            _selection_target_chapter({"evidence": {"chapter_no": "bad"}}, 7), 7)

    def test_start_request_accepts_scene_selection(self):
        request = StartRequest(scene_selection={"id": "abc", "timepoint": "after"})
        self.assertEqual(request.scene_selection["id"], "abc")
        self.assertIsNone(StartRequest().scene_selection)


if __name__ == "__main__":
    unittest.main()
