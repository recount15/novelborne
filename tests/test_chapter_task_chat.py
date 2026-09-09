import unittest
from core.engine.chapter_arc import build_chapter_arc, chapter_generation_brief, advance_chapter_arc
from core.engine.task_director import rank_tasks
from core.engine.task_acceptance import evaluate
from core.engine.story_skills.character_chat import disclosure_profile, chat_skill
from core.engine.story_skills.contract import SkillContext

class ChapterTaskChatTests(unittest.TestCase):
    def setUp(self):
        self.state = {"round": 2, "current_chapter": 1, "chapter_index": {"chapters": [{"idx": 1, "title": "第一回 样章"}]}, "objectives": [{"title": "救援", "status": "active"}], "state_memory": {"location": {"name": "城门"}, "scene": {"name": "城门"}, "knowledge": {"known": []}}, "sequence_feedback": []}

    def test_chapter_arc_and_progress(self):
        plan = build_chapter_arc(self.state, {})
        self.assertEqual(plan["chapter"], 1)
        brief = chapter_generation_brief(plan, {"must_progress": ["救援"]})
        self.assertTrue(brief["internal"])
        advanced = advance_chapter_arc(plan, {"round": 2})
        self.assertEqual(advanced["progress"]["turns"], plan["progress"]["turns"] + 1)

    def test_task_ranking_prefers_feasible_current_task(self):
        ranked = rank_tasks([{"id": "far", "chapter": 9, "feasibility": 0.1}, {"id": "near", "chapter": 1, "location": "城门", "feasibility": 1.0}], self.state, {"title": "第一回"})
        self.assertEqual(ranked[0]["id"], "near")
        self.assertGreaterEqual(ranked[0]["relevance_score"], ranked[1]["relevance_score"])

    def test_flexible_acceptance_partial_then_complete(self):
        task = {"hard_facts": ["找到", "救援"], "soft_signals": ["询问"]}
        partial = evaluate(task, action="询问", narrative="打听消息")
        complete = evaluate(task, action="找到并完成救援", narrative="")
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(complete["status"], "complete")

    def test_chat_disclosure_tracks_affection_and_personality(self):
        high = disclosure_profile({"name": "伙伴", "favorability": 0.9, "trust": 0.9, "personality": ["坦率"]})
        low = disclosure_profile({"name": "宿敌", "favorability": 0.0, "trust": 0.0, "personality": ["多疑"]})
        self.assertEqual(high["level"], "high")
        self.assertEqual(low["level"], "low")
        result = chat_skill(SkillContext(thread_map={}, brief={"must_progress": ["救援"]}), {"name": "宿敌", "favorability": 0.0, "trust": 0.0, "withheld_facts": ["真实目的"], "personality": ["多疑"]}, message="你想做什么？")
        self.assertEqual(result.proposal["target"], "宿敌")
        self.assertTrue(result.proposal["deception_claims"])

if __name__ == "__main__":
    unittest.main()
