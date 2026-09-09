"""Offline regression tests for durable state and isolated generation."""
import copy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from core.engine import persistence
from core.engine.turn_transaction import TurnTransaction
from core.services import chat_service


def opening():
    return {'system': 'rules', 'save_stage': 'opening', 'round': 0,
            'character_states': {'one': {'body': {'gender': 'female'}}}}


class DurableStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def test_db_failure_never_writes_mirror_or_revision(self):
        state = opening()
        with patch.object(persistence, '_db_save', side_effect=sqlite3.OperationalError('fault')), patch.object(persistence, '_atomic_json') as mirror:
            with self.assertRaises(sqlite3.OperationalError):
                persistence.save_state(state, root=self.root, session_id='s')
            mirror.assert_not_called()
        self.assertNotIn('revision', state)

    def test_mirror_failure_is_retryable_after_durable_commit(self):
        state = opening()
        with patch.object(persistence, '_atomic_json', side_effect=OSError('fault')):
            persistence.save_state(state, root=self.root, session_id='s', request_id='r', expected_revision=0)
        self.assertEqual(state['revision'], 1)
        conn = persistence._db_connect(self.root)
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM saves').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT delivered FROM save_outbox').fetchone()[0], 0)
        finally:
            conn.close()
        self.assertEqual(persistence.flush_outbox(self.root), 1)

    def test_request_retry_and_stale_revision(self):
        state = opening()
        persistence.save_state(state, root=self.root, session_id='s', request_id='r', expected_revision=0)
        persistence.save_state(state, root=self.root, session_id='s', request_id='r', expected_revision=0)
        self.assertEqual(state['revision'], 1)
        with self.assertRaises(persistence.RevisionConflict):
            persistence.save_state(state, root=self.root, session_id='s', request_id='r2', expected_revision=0)
        changed = copy.deepcopy(state)
        changed['round'] = 2
        with self.assertRaises(persistence.RevisionConflict):
            persistence.save_state(changed, root=self.root, session_id='s', request_id='r')

    def test_transaction_commits_only_after_sqlite(self):
        original = opening()
        tx = TurnTransaction(original)
        tx.advance('PLANNED')
        tx.candidate['character_states']['one']['body']['gender'] = 'male'
        tx.advance('GENERATED')
        tx.advance('VALIDATED')
        with self.assertRaises(ValueError):
            tx.advance('COMMITTED')
        tx.commit(root=self.root, session_id='s', request_id='r', target=original)
        self.assertEqual(tx.status, 'COMMITTED')
        self.assertEqual(original['revision'], 1)
        with self.assertRaises(ValueError):
            tx.rollback()

    def test_failed_commit_keeps_original(self):
        original = opening()
        before = copy.deepcopy(original)
        tx = TurnTransaction(original)
        tx.advance('PLANNED'); tx.advance('GENERATED'); tx.advance('VALIDATED')
        with patch.object(persistence, '_db_save', side_effect=sqlite3.OperationalError('fault')):
            with self.assertRaises(sqlite3.OperationalError):
                tx.commit(root=self.root, session_id='s', request_id='r', target=original)
        self.assertEqual(original, before)
        self.assertEqual(tx.status, 'FAILED')


class ChatStateTests(unittest.TestCase):
    def test_explicit_empty_scene_has_no_fallback_characters(self):
        self.assertEqual(chat_service.get_roster({'active_members': [], 'companions': [{'name': 'absent'}]}), [])

    def test_chat_uses_assistant_content_not_last_user_message(self):
        state = {'active_members': [{'name': 'NPC'}], 'history': [
            {'role': 'assistant', 'content': 'SCENE_FACT'}, {'role': 'user', 'content': 'USER_ACTION'}]}
        prompts = []
        def model(prompt):
            prompts.append(prompt)
            return '平静地说：' + '我记得这里的景色，也愿意与你聊聊。' * 4
        chat_service.generate_reply('NPC', 'hello', state, model_fn=model, attempts=0)
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all('SCENE_FACT' in prompt for prompt in prompts))


if __name__ == '__main__':
    unittest.main()
