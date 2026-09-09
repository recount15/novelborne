import unittest
from core.engine.plot_threading import prepare, generation_brief
from core.engine.sequence_feedback import new_feedback, append_feedback, recent_feedback, digest
from core.engine.sequence_review import review_turn
from core.engine.story_graph import check_events
from core.engine.story_skills import SkillContext, thread_planning_skill, choice_precondition_skill

class LongSequenceSelfImprovementTests(unittest.TestCase):
    def test_feedback_accumulates_and_retrieves(self):
        rows = []
        for n in range(1, 11):
            rows = append_feedback(rows, new_feedback(round=n, turn_id=f"round-{n}", summary=f"事实 {n}", unresolved=[{"objective": f"目标 {n}"}], next_turn_directives=[f"修复 {n}"]))
        self.assertEqual(len(rows), 10)
        recent = recent_feedback(rows, 11, 5)
        self.assertEqual([x["round"] for x in recent], [10, 9, 8, 7, 6])
        self.assertIn("修复 10", digest(recent)["directives"])

    def test_plot_brief_contains_feedback_and_order(self):
        state = {"round": 3, "current_chapter": 1, "sequence_feedback": [new_feedback(round=2, turn_id="round-2", next_turn_directives=["保持地点连续"])], "story_graph": {"events": [{"event_id": "a", "status": "resolved", "round": 2}, {"event_id": "b", "prerequisites": ["a"], "status": "planned", "round": 3}]}, "state_memory": {"scene": {"name": "城门"}, "knowledge": {"known": ["a"]}}}
        thread = prepare(state, user_input="调查城门")
        brief = generation_brief(thread)
        self.assertEqual(brief["round"], 3)
        self.assertTrue(any("调查城门" in x for x in brief["must_progress"]))
        self.assertTrue(thread.source_hash)

    def test_review_is_non_blocking(self):
        row = review_turn(state={"round": 4, "current_chapter": 1, "objectives": []}, narrative="推进", options=[{"key":"A"}], events=[{"event_id":"b", "prerequisites":["a"]}])
        self.assertTrue(row["degraded"])
        self.assertEqual(row["feedback_status"], "degraded")

    def test_graph_has_repair_plan_but_play_allowed(self):
        result = check_events([{"event_id":"b", "prerequisites":["a"]}])
        self.assertFalse(result["ok"])
        self.assertTrue(result["ok_for_play"])
        self.assertTrue(result["repair_plan"])

    def test_skills_return_structured_results(self):
        ctx = SkillContext(thread_map={"events": [], "unresolved": []}, brief={"must_progress":["主线"], "must_avoid":["跳跃"]})
        self.assertEqual(thread_planning_skill(ctx).name, "thread_planning")
        result = choice_precondition_skill(ctx, [{"text":"调查", "patch_valid":True}])
        self.assertEqual(result.proposal["accepted"][0]["text"], "调查")

if __name__ == "__main__":
    unittest.main()
