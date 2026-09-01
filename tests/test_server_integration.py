"""Integration tests for new backend services in server.py."""
import unittest
from unittest.mock import MagicMock, patch

from core.api.operations import OperationJournal
from core.services import character_state_service
from core.services import golden_finger_service
from core.services import pre_game_service
from core.services import structured_question_service


class TestOperationJournal(unittest.TestCase):
    def test_create_and_append(self):
        journal = OperationJournal()
        op = journal.start(client_request_id="test-123")
        self.assertEqual(op.status, "ack")
        self.assertGreaterEqual(op.seq, 0)  # Seq starts at 0 or 1
        
        # Append progress
        event = journal.progress(op.operation_id, {"phase": "running"})
        self.assertEqual(event.type, "progress")
        self.assertEqual(event.data["phase"], "running")
        
        # Complete
        done_event = journal.done(op.operation_id, {"result": "success"})
        self.assertEqual(done_event.type, "done")
        
        # Verify terminal state
        final_op = journal.status(op.operation_id)
        self.assertEqual(final_op.status, "done")

    def test_idempotency(self):
        journal = OperationJournal()
        op1 = journal.start(client_request_id="test-456")
        op2 = journal.start(client_request_id="test-456")
        self.assertEqual(op1.operation_id, op2.operation_id)


class TestCharacterStateService(unittest.TestCase):
    def test_blank_state(self):
        state = character_state_service.blank_character_state()
        self.assertEqual(state["schema"], "character-state")
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["assertions"], [])

    def test_add_evidence(self):
        state = character_state_service.blank_character_state()
        updated = character_state_service.add_evidence(
            state, "mood", "happy", confidence=0.8, round_no=1
        )
        self.assertEqual(len(updated["assertions"]), 1)
        self.assertEqual(updated["assertions"][0]["key"], "mood")
        self.assertEqual(updated["assertions"][0]["value"], "happy")
        self.assertEqual(updated["assertions"][0]["confidence"], 0.8)

    def test_decay_assertions(self):
        state = character_state_service.blank_character_state()
        state = character_state_service.add_evidence(
            state, "mood", "happy", confidence=1.0
        )
        decayed = character_state_service.decay_assertions(state, steps=1)
        self.assertLess(
            decayed["assertions"][0]["confidence"],
            state["assertions"][0]["confidence"]
        )


class TestPreGameService(unittest.TestCase):
    def test_state_machine(self):
        svc = pre_game_service.PreGameService()
        self.assertEqual(svc.stage, "book_selected")
        
        # Select book
        state = svc.select_book({"title": "测试书籍"})
        self.assertEqual(state["stage"], "preparing_script")
        
        # Prepare script
        package = {
            "script": "这是开局脚本",
            "title": "测试",
            "world_evidence": ["危险世界"],
            "stage_evidence": ["战斗开始"],
            "player_evidence": ["新手玩家"]
        }
        state = svc.prepare_script(package)
        self.assertEqual(state["stage"], "script_ready")
        self.assertIn("prepared_script", state)
        
        # Derive difficulty
        state = svc.derive_difficulty()
        self.assertEqual(state["stage"], "difficulty_ready")
        self.assertIn("difficulty", state)

    def test_prepare_state_helper(self):
        book = {"title": "测试"}
        package = {"script": "脚本内容", "world": ["世界观"]}
        state = pre_game_service.prepare_state(book, package)
        self.assertEqual(state["stage"], "difficulty_ready")


class TestGoldenFingerService(unittest.TestCase):
    def test_deterministic_budget(self):
        script = {"script": "a" * 500}
        budget = golden_finger_service.deterministic_budget(script, 4)
        self.assertIn("target", budget)
        self.assertIn("minimum", budget)
        self.assertIn("maximum", budget)
        self.assertGreater(budget["target"], budget["minimum"])
        self.assertLess(budget["target"], budget["maximum"])

    def test_validate_spec(self):
        spec = {
            "name": "测试",
            "effect": "测试效果",
            "scope": "自身",
            "cost": "精神负荷",
            "cooldown": "每日一次",
            "limits": "不得抹除既成事实；不得越过世界上限；必须可验证",
            "difficulty": "D4"
        }
        result = golden_finger_service.validate_spec(spec, 4)
        self.assertIn("ok", result)
        self.assertIn("issues", result)

    def test_fallback_spec(self):
        spec = golden_finger_service.fallback_spec(4, "现代都市", "普通人")
        self.assertIn("name", spec)
        self.assertIn("effect", spec)


class TestStructuredQuestionService(unittest.TestCase):
    def test_make_question(self):
        q = structured_question_service.make_question(
            "pre_game_setup", "test_q", "测试问题？",
            answer_type="single_choice",
            choices=[{"id": "a", "label": "选项A"}, {"id": "b", "label": "选项B"}]
        )
        self.assertEqual(q["id"], "pre_game_setup.test_q")
        self.assertEqual(q["prompt"], "测试问题？")
        self.assertEqual(len(q["choices"]), 2)

    def test_normalize_answer(self):
        question = {
            "answer_type": "single_choice",
            "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
        }
        normalized = structured_question_service.normalize_answer(question, "A")
        self.assertEqual(normalized, "a")

    def test_validate_answer(self):
        question = {
            "answer_type": "text",
            "required": True,
            "validation": {"min_length": 5}
        }
        errors = structured_question_service.validate_answer(question, "hi")
        self.assertIn("文本过短", errors)
        
        errors = structured_question_service.validate_answer(question, "hello world")
        self.assertEqual(errors, [])

    def test_service_answer(self):
        svc = structured_question_service.StructuredQuestionService()
        question = structured_question_service.make_question(
            "pre_game_setup", "test", "问题？",
            answer_type="text"
        )
        result = svc.answer(question, "答案")
        self.assertEqual(result["answer"], "答案")
        self.assertTrue(result["valid"])


class TestServerIntegration(unittest.TestCase):
    """Test server.py integration points."""
    
    def test_operation_journal_initialized(self):
        """Verify operation_journal is created on server startup."""
        from core import server
        self.assertIsNotNone(server.operation_journal)
        self.assertIsInstance(server.operation_journal, OperationJournal)

    def test_question_service_initialized(self):
        """Verify question_service is created on server startup."""
        from core import server
        self.assertIsNotNone(server.question_service)
        self.assertIsInstance(
            server.question_service,
            structured_question_service.StructuredQuestionService
        )

    def test_public_state_includes_new_fields(self):
        """Verify public_state exposes character_states and pre_game_state."""
        from core.api.contracts import public_state
        
        state = {
            "system": "test",
            "character_states": {
                "角色A": character_state_service.blank_character_state()
            },
            "pre_game_state": {
                "stage": "difficulty_ready",
                "difficulty": {"level": 4}
            }
        }
        
        public = public_state(state)
        self.assertIn("character_states", public)
        self.assertIn("pre_game_state", public)


if __name__ == "__main__":
    unittest.main()
