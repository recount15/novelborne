# -*- coding: utf-8 -*-
"""anchor_distiller 蒸馏池化单测（重构 M0）。

覆盖：多 worker 并行蒸馏、章级互斥去重（distill_now 与 worker 不双调模型）、
rescue_now 摘录兜底、失败退避不阻塞其他章节。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import anchor_distiller as ad  # noqa: E402

_CHAPTER = (
    "第一章 北岭矿镇。矿镇的清晨总是先从钟声开始。"
    "沈青梧背着行囊穿过南门，护城的旧军拦住了他，交出了过所才放行。"
    "钟楼的影子压在长街上，因此他的脚步声显得格外清晰。"
    "巷口的铁匠看了他一眼，随后低头继续打铁，仿佛早已见过太多外乡人。"
    "他在茶棚坐下，问出第一句话：矿监府如今是谁当家？"
    "茶博士的手一抖，热水洒在桌面上，导致整条街的目光都转了过来。"
)

_CHAPTER_RE = re.compile(r"chapter=(\d+)")


def _make_book(directory: Path, count: int, empty_chapters=()):
    chapters = directory / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    for number in range(1, count + 1):
        text = "" if number in empty_chapters else _CHAPTER * 3
        (chapters / ("%04d.txt" % number)).write_text(text, encoding="utf-8")
    return chapters


class FakeModel:
    """按 prompt 里的 chapter=N 生成合法锚点；可指定章号抛错。"""

    def __init__(self, fail_chapters=(), delay=0.0):
        self.calls = []
        self.lock = threading.Lock()
        self.fail_chapters = set(fail_chapters)
        self.delay = delay

    def __call__(self, prompt):
        with self.lock:
            self.calls.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        match = _CHAPTER_RE.search(prompt)
        number = int(match.group(1)) if match else 0
        if number in self.fail_chapters:
            raise ValueError("模拟模型失败 chapter=%d" % number)
        text = _CHAPTER * 3
        return json.dumps({
            "chapter": number,
            "title": "第%s章 矿镇" % number,
            "summary": "沈青梧抵达北岭矿镇，因询问矿监府而引起全街注意。",
            "events": ["沈青梧入城被拦查过所", "茶棚询问矿监府当家人"],
            "characters": ["沈青梧", "茶博士"],
            "world": "北岭矿镇以钟声计时，护城旧军盘查严格。",
            "foreshadowing": ["矿监府的人事变动隐藏内情"],
            "quotes": ["矿镇的清晨总是先从钟声开始。", "茶博士的手一抖，热水洒在桌面上，导致整条街的目光都转了过来。"],
            "ripple": "外乡人入城的传闻开始在长街扩散。",
        }, ensure_ascii=False)


def _wait_until(condition, timeout=20.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


class TestDistillerPool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.book = Path(self._tmp.name)
        add = self.addCleanup
        add(self._tmp.cleanup)

    def test_parallel_pool_distills_all_chapters(self):
        _make_book(self.book, count=5)
        model = FakeModel(delay=0.05)
        distiller = ad.AnchorDistiller(self.book, model, workers=3)
        self.addCleanup(distiller.stop)
        added = distiller.enqueue(1, lookahead=4, total=5)
        self.assertEqual(sorted(added), [1, 2, 3, 4, 5])
        distiller.start()
        ok = _wait_until(
            lambda: all((self.book / "anchors" / ("%04d.json" % n)).is_file()
                        for n in range(1, 6)))
        self.assertTrue(ok, "5 章未在时限内全部落盘: %s" % distiller.status())
        status = distiller.status()
        for number in range(1, 6):
            self.assertEqual(status[number]["status"], "done")
        # 每章恰好一次模型调用（章级互斥 + 落盘去重）。
        self.assertEqual(len(model.calls), 5)

    def test_distill_now_dedupes_with_pool(self):
        _make_book(self.book, count=3)
        model = FakeModel(delay=0.15)
        distiller = ad.AnchorDistiller(self.book, model, workers=2)
        self.addCleanup(distiller.stop)
        distiller.enqueue(1, lookahead=2, total=3)
        distiller.start()
        distiller.distill_now(1)  # 与 worker 并发同一章
        ok = _wait_until(
            lambda: all((self.book / "anchors" / ("%04d.json" % n)).is_file()
                        for n in range(1, 4)))
        self.assertTrue(ok)
        chapter1_calls = [p for p in model.calls if "chapter=1" in p]
        self.assertEqual(len(chapter1_calls), 1)

    def test_rescue_now_fallback_on_model_failure(self):
        _make_book(self.book, count=2)
        model = FakeModel(fail_chapters={1})
        distiller = ad.AnchorDistiller(self.book, model, workers=1)
        self.addCleanup(distiller.stop)
        anchor = distiller.rescue_now(1)
        self.assertEqual(anchor["chapter"], 1)
        target = self.book / "anchors" / "0001.json"
        self.assertTrue(target.is_file())
        text = (self.book / "chapters" / "0001.txt").read_text(encoding="utf-8")
        validated = ad.validate_anchor(anchor, text, 1)
        self.assertEqual(validated["chapter"], 1)
        entry = distiller.status()[1]
        self.assertEqual(entry["status"], "done")
        self.assertEqual(entry.get("origin"), "fallback")

    def test_backoff_does_not_block_other_chapters(self):
        # 第 2 章正文过短：模型失败 + 摘录兜底也无句可用 → 走延迟重入队。
        _make_book(self.book, count=4, empty_chapters={2})
        (self.book / "chapters" / "0002.txt").write_text("空。", encoding="utf-8")
        model = FakeModel(fail_chapters={2})
        distiller = ad.AnchorDistiller(self.book, model, workers=2)
        self.addCleanup(distiller.stop)
        distiller.enqueue(1, lookahead=3, total=4)
        distiller.start()
        others_done = _wait_until(
            lambda: all((self.book / "anchors" / ("%04d.json" % n)).is_file()
                        for n in (1, 3, 4)),
            timeout=15)
        self.assertTrue(others_done, "退避不应阻塞其他章节: %s" % distiller.status())
        entry = distiller.status().get(2, {})
        self.assertEqual(entry.get("status"), "failed")
        self.assertIn("自动重试", entry.get("error", ""))

    def test_force_refine_overwrites_fallback(self):
        _make_book(self.book, count=1)
        state = {"fail": True}
        model = FakeModel()

        def switchable(prompt):
            if state["fail"]:
                raise ValueError("先失败")
            return model(prompt)

        distiller = ad.AnchorDistiller(self.book, switchable, workers=1)
        self.addCleanup(distiller.stop)
        # 池未启动时先救援：模型失败 → 摘录兜底落盘（origin=fallback）+ force 精化入队。
        distiller.rescue_now(1)
        entry = distiller.status().get(1, {})
        self.assertEqual(entry.get("origin"), "fallback")
        state["fail"] = False
        distiller.start()
        def _refined() -> bool:
            entry = distiller.status().get(1, {})
            return entry.get("status") == "done" and "origin" not in entry
        ok = _wait_until(_refined, timeout=8)
        # force 精化应重新蒸馏并成功（status 无 origin/note）。
        self.assertTrue(ok, "force 精化未完成: %s" % distiller.status())
        data = json.loads((self.book / "anchors" / "0001.json").read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "第1章 矿镇")


if __name__ == "__main__":
    unittest.main()
