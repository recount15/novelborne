# -*- coding: utf-8 -*-
"""并发上下文隔离（D07）：禁止跨线程共享同一 Context 并发 run。

三处历史缺陷：app.py 判定双卷、app.py 结算波次、agent_refill 槽位并行，
均为“单快照多任务并发 .run”。同一 Context 对象并发进入会 RuntimeError，
错误被 run_parallel 吞进 JobResult.error，作业静默失败。
"""
from __future__ import annotations

import contextvars
import threading
import time
import unittest

from core.engine import parallel, token_accounting
from core.engine.agent_refill import run_refill_loop


class SharedContextHazardTest(unittest.TestCase):
    def test_shared_context_concurrent_run_raises(self):
        """直接证明：同一 Context 对象被两线程同时 run 必然报错。"""
        ctx_run = contextvars.copy_context()
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def work():
            barrier.wait(timeout=5)
            time.sleep(0.05)
            return 1

        def runner():
            try:
                ctx_run.run(work)
            except RuntimeError as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=runner) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertTrue(errors, "并发进入同一 Context 未被拒绝，无法证明隔离必要性")


class ContextSnapshotHelperTest(unittest.TestCase):
    """with_context_snapshot：逐任务独立快照，usage 计量经共享 dict 不丢。"""

    def test_concurrent_jobs_isolated_and_usage_accounted(self):
        token_accounting.init_turn_usage()
        seen_threads = set()

        def job(n: int):
            def _run():
                seen_threads.add(threading.get_ident())
                token_accounting.record_usage(10 * n, 5, "segments")
                time.sleep(0.03)
                return n * 2
            return _run

        # 连续 3 轮（Q05：并发测试重复运行无交叉失败）
        for _round in range(3):
            jobs = [parallel.with_context_snapshot(job(n)) for n in range(1, 5)]
            results = parallel.run_parallel(jobs, parallel.PRIORITY_TURN)
            values = [r.value for r in results]
            self.assertEqual(sorted(v for v in values if v is not None), [2, 4, 6, 8])
            for r in results:
                self.assertIsNone(r.error)
        usage = token_accounting.get_turn_usage()
        # 3 轮 × (10+20+30+40) prompt + 3 轮 × 4×5 completion
        self.assertEqual(usage["prompt_tokens"], 3 * 100)
        self.assertEqual(usage["completion_tokens"], 3 * 20)
        self.assertGreaterEqual(len(seen_threads), 1)


class AgentRefillParallelTest(unittest.TestCase):
    def test_parallel_slots_all_answered(self):
        """三槽并行（真实线程池）：每槽独立快照，全部作业成功而非静默失败。"""
        token_accounting.init_turn_usage()

        def fake_model(prompt: str) -> str:
            time.sleep(0.05)  # 保证作业时间窗重叠
            token_accounting.record_usage(7, 3, "segments")
            return "答案"

        def grade(contract, answer):
            return [] if str(answer) == "答案" else ["答案为空"]

        def refill_prompt(contract, errors):
            return "请补全"

        contracts = [{"slot": f"slot{i}", "question": f"问{i}"} for i in range(3)]
        for _round in range(3):
            result = run_refill_loop(
                contracts, ["", "", ""],
                grade=grade, refill_prompt=refill_prompt,
                model=fake_model, attempts=2)
            answers = result["answers"]
            self.assertEqual(len(answers), 3)
            for answer in answers:
                self.assertEqual(answer, "答案")
            for row in result["per_slot"]:
                self.assertFalse(row.get("fallback"), f"槽位 {row.get('index')} 走了兜底")
        usage = token_accounting.get_turn_usage()
        self.assertGreaterEqual(usage["segments"], 3 * 3 * 10)


if __name__ == "__main__":
    unittest.main()
