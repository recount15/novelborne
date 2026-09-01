# -*- coding: utf-8 -*-
"""存读档蒸馏产物保真回归：读档不重蒸、不显示"蒸馏中"、产物目录命中一致。

覆盖三处修复：
1. server._normalize_restored_state：distill_key 回填 + opening_distill 终态落定；
2. opening_service.peek_progress：过期 running 残留不外泄（防"永久蒸馏中"）；
3. app._start_background_distillation：state.distill_key 优先（读档后锚点目录一致）。
"""
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.services import opening_service


class NormalizeRestoredStateTests(unittest.TestCase):
    """读档归一化：蒸馏产物保真，绝不显示"蒸馏中"，绝不触发重蒸。"""

    def _normalize(self, restored):
        from core.server import _normalize_restored_state
        return _normalize_restored_state(restored, "sess-test")

    def test_backfills_distill_key_from_chapter_index(self):
        restored = self._normalize({"chapter_index": {"book_id": "b1"}, "system": "s"})
        self.assertTrue(str(restored.get("distill_key") or "").endswith(os.path.join("books", "b1")))

    def test_marks_done_from_disk_anchors(self):
        with TemporaryDirectory() as tmp:
            anchors = Path(tmp) / "anchors"
            anchors.mkdir()
            (anchors / "0001.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
            restored = self._normalize({
                "mode": "强化模式", "system": "s",
                "distill_key": str(Path(tmp)),
            })
            od = restored["opening_distill"]
            self.assertEqual(od["status"], "done")
            self.assertTrue(od["ok"])
            self.assertEqual(od.get("anchors_on_disk"), 1)

    def test_enhanced_without_products_gets_terminal_marker(self):
        with TemporaryDirectory() as tmp:
            restored = self._normalize({
                "mode": "强化模式", "system": "s", "distill_key": str(Path(tmp)),
            })
            od = restored["opening_distill"]
            self.assertEqual(od["status"], "done")  # 绝不落回 running
            self.assertFalse(od["ok"])

    def test_keeps_existing_done_marker(self):
        original = {"status": "done", "stage": "开局蒸馏完成", "ok": True, "timings": {"total": 1}}
        restored = self._normalize({
            "mode": "强化模式", "system": "s", "opening_distill": dict(original),
        })
        self.assertEqual(restored["opening_distill"], original)

    def test_plot_summary_in_save_survives_restore(self):
        restored = self._normalize({
            "mode": "强化模式", "system": "s",
            "distill": {"plot_summary": {"主线": "……"}},
        })
        od = restored["opening_distill"]
        self.assertEqual(od["status"], "done")
        self.assertTrue(od["plot_ready"])


class PeekProgressStalenessTests(unittest.TestCase):
    """进度注册表保鲜：中断蒸馏的 running 残留不得污染后续读档会话。"""

    KEY = "Z:\\不存在目录-仅作注册表键"

    def tearDown(self):
        opening_service._PROGRESS_REGISTRY.pop(self.KEY, None)

    def test_fresh_running_entry_returned(self):
        opening_service._PROGRESS_REGISTRY[self.KEY] = {
            "status": "running", "stage": "x", "at": time.time()}
        self.assertEqual(opening_service.peek_progress(self.KEY)["status"], "running")

    def test_stale_running_entry_dropped(self):
        opening_service._PROGRESS_REGISTRY[self.KEY] = {
            "status": "running", "stage": "x", "at": time.time() - 1000}
        self.assertIsNone(opening_service.peek_progress(self.KEY))
        self.assertNotIn(self.KEY, opening_service._PROGRESS_REGISTRY)

    def test_done_entry_without_timestamp_still_returned(self):
        opening_service._PROGRESS_REGISTRY[self.KEY] = {"status": "done", "stage": "完成"}
        self.assertEqual(opening_service.peek_progress(self.KEY)["status"], "done")

    def test_write_progress_stamps_time(self):
        state = {}
        opening_service._write_progress(state, {"stage": "wave1_tick", "done": 1, "total": 2},
                                        self.KEY)
        self.assertLessEqual(time.time() - state["opening_distill"]["at"], 5)
        self.assertLessEqual(
            time.time() - opening_service._PROGRESS_REGISTRY[self.KEY]["at"], 5)


class StartDistillationKeyPreferenceTests(unittest.TestCase):
    """读档后锚点目录命中：state.distill_key 优先于 chapter_index 推导。"""

    def test_prefers_state_distill_key_over_book_id(self):
        from core.app import _start_background_distillation
        from core.services import registries
        with TemporaryDirectory() as tmp:
            restored = Path(tmp) / "restored_book"
            other = Path(tmp) / "books" / "other_id"
            for base in (restored, other):
                (base / "chapters").mkdir(parents=True)
                (base / "anchors").mkdir(parents=True)
                for n in range(1, 8):
                    (base / "chapters" / ("%04d.txt" % n)).write_text("第%d章" % n, encoding="utf-8")
                    # 存档目录前 7 章锚点齐备：窗口内无缺失章，模型函数不应被调用。
                    (restored / "anchors" / ("%04d.json" % n)).write_text(
                        json.dumps({"title": "t%d" % n}), encoding="utf-8")
            state = {
                "distill_enabled": True,
                "distill_key": str(restored),
                "chapter_index": {"book_id": "other_id"},
                "current_chapter": 1,
                "total_chapters": 7,
                "distill": {"plot_summary": {"主线": "保留"}},
            }
            key = os.path.abspath(str(restored))
            try:
                _start_background_distillation(state, client=None, model="m")
                self.assertIn(key, registries.distillers)
                self.assertEqual(state["distill_key"], str(restored))
                # 保留式：蒸馏启动不得覆盖既有 plot_summary。
                self.assertEqual(state["distill"]["plot_summary"], {"主线": "保留"})
            finally:
                distiller = registries.distillers.pop(key, None)
                if distiller is not None:
                    distiller.stop(join=False)


if __name__ == "__main__":
    unittest.main()
