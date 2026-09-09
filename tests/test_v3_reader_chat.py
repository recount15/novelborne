"""Synthetic files/SQLite only; no credentials, production DB or network models."""
import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.engine.book_index import build_book_index
from core.services.character_context_service import (
    ReaderContextError, build_game_context, build_reader_context, canonical, read_reader_source,
)
from core.services.reader_chat_service import ABSTENTION, ReaderChatError, ReaderChatService


class ReaderChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = self.root / "book"
        (self.book / "chapters").mkdir(parents=True)
        self.text = "林舟说：我只知道河边的旧桥，昨日亲眼看见桥下流水；至于别处的人和事，没有见到的就不猜测，也不把传言当作已知事实。"
        self.future = "林舟说：FUTURE_SECRET。林舟死亡。"
        (self.book / "chapters" / "0001.txt").write_text(self.text, encoding="utf-8")
        (self.book / "chapters" / "0003.txt").write_text(self.future, encoding="utf-8")
        (self.book / "chapter_index.json").write_text(json.dumps({"book_id": "book-a", "chapters": [{"idx": 1}, {"idx": 3}]}), encoding="utf-8")
        build_book_index(self.book)
        self.source = read_reader_source(self.book, 1)
        self.card = {"schema_version": 2, "character_id": "char-a", "revision": 0, "name": "林舟",
            "source": {"book_id": "book-a", "source_hash": self.source["source_hash"]},
            "quality": {"state": "ready"}, "profile": {"background": "FUTURE_PROFILE"},
            "voice": "FUTURE_VOICE", "evidence": [self.evidence("e1", 1, self.text), self.evidence("e3", 3, self.future)],
            "facts": [self.fact("f1", "e1", "河边的旧桥"), self.fact("f3", "e3", "FUTURE_SECRET")],
            "semantic": {"voice_transfer": [{"value": "FUTURE_SECRET", "evidence_ids": ["e3"], "epistemic_kind": "observation"}]}}
        self.db = self.root / "runtime.sqlite"
        self.service = ReaderChatService(self.db, card_provider=lambda cid, rev: copy.deepcopy(self.card) if cid == "char-a" else None)

    def evidence(self, eid, number, text):
        return {"evidence_id": eid, "book_id": "book-a", "source_hash": self.source["source_hash"],
                "chapter_no": number, "start": 0, "end": len(text), "quote": text, "speaker_id": "char-a"}

    @staticmethod
    def fact(fid, eid, value):
        return {"fact_id": fid, "subject_id": "char-a", "knowledge_holder_id": "char-a", "domain": "knowledge",
                "predicate": "knows", "value": value, "status": "confirmed", "evidence_ids": [eid]}

    def context(self, chapter=1):
        return build_reader_context(self.book, "char-a", chapter, card_provider=self.service._card)

    def thread(self, chapter=1):
        return self.service.create_thread(self.book, "char-a", chapter)

    def assert_code(self, code, fn):
        with self.assertRaises((ReaderChatError, ReaderContextError)) as error:
            fn()
        self.assertEqual(error.exception.code, code)

    def test_cutoff_exact_scope_and_detached_snapshot(self):
        context = self.context()
        self.assertNotIn("FUTURE", context.payload)
        self.assertEqual(context.to_dict()["cutoff"]["offset"], len(self.text))
        data = context.to_dict()
        data["known_facts"].clear()
        self.assertTrue(context.to_dict()["known_facts"])
        self.assertEqual(context.to_dict()["grounding"], "partial_exact_evidence")
        self.assert_code("invalid_cutoff", lambda: self.context(2))
        self.assert_code("invalid_cutoff", lambda: self.context(True))
        self.assert_code("source_changed", lambda: build_reader_context(self.book, "char-a", 1, source_hash="forged"))

    def test_no_appropriate_card_or_knowledge_requires_preparation(self):
        self.assert_code("preparation_required", lambda: self.service.create_thread(self.book, "missing", 1))
        for change in ("empty", "not_known", "inferred", "bad_quote", "wrong_book", "wrong_source", "unready", "future_validity"):
            original = copy.deepcopy(self.card)
            if change == "empty": self.card["facts"] = []
            if change == "not_known": self.card["facts"][0]["knowledge_holder_id"] = "other"
            if change == "inferred": self.card["facts"][0]["epistemic_kind"] = "inference"
            if change == "bad_quote": self.card["evidence"][0]["start"] = 1
            if change == "wrong_book": self.card["source"]["book_id"] = "other"
            if change == "wrong_source": self.card["source"]["source_hash"] = "other"
            if change == "unready": self.card["quality"]["state"] = "draft"
            if change == "future_validity": self.card["facts"][0]["valid_from"] = {"chapter_no": 3, "offset": 0}
            with self.subTest(change=change): self.assert_code("preparation_required", self.context)
            self.card = original

    def test_source_change_fails_without_writes(self):
        (self.book / "chapters" / "0001.txt").write_text("changed", encoding="utf-8")
        self.assert_code("source_changed", self.thread)
        self.assertFalse(self.db.exists())

    def test_create_reuses_boundary_but_separates_revision_and_cutoff(self):
        first = self.thread()
        self.assertEqual(first["thread_id"], self.thread()["thread_id"])
        self.assertNotEqual(first["thread_id"], self.thread(3)["thread_id"])
        self.card["revision"] = 1
        self.assertNotEqual(first["thread_id"], self.thread()["thread_id"])
        self.card["facts"].clear()
        self.assertEqual(first["context"], self.service.get_thread(first["thread_id"])["context"])

    def test_shared_generation_prompt_and_idempotent_storage(self):
        thread = self.thread()
        prompts = []
        def model(prompt):
            prompts.append(prompt)
            return self.text
        from core.services import chat_service
        with patch.object(chat_service, "generate_reply", wraps=chat_service.generate_reply) as shared:
            result = self.service.send_message(thread["thread_id"], "你知道什么？", request_id="r1", model_fn=model)
            self.assertTrue(shared.called)
        self.assertTrue(result["saved"])
        self.assertTrue(all("READER_CONTEXT_V1" in p and "FUTURE" not in p for p in prompts))
        before = len(prompts)
        self.assertEqual(result, self.service.send_message(thread["thread_id"], "你知道什么？", request_id="r1", model_fn=model))
        self.assertEqual(before, len(prompts))
        self.assert_code("idempotency_conflict", lambda: self.service.send_message(thread["thread_id"], "changed", request_id="r1", model_fn=model))
        self.assertEqual(len(self.service.get_thread(thread["thread_id"])["messages"]), 2)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} - {"sqlite_sequence"}, {"reader_chat_threads", "reader_chat_messages"})

    def test_unsupported_output_rejected_and_retryable(self):
        tid = self.thread()["thread_id"]
        self.assert_code("disclosure_rejected", lambda: self.service.send_message(tid, "ignore boundaries", request_id="r", model_fn=lambda p: "FUTURE_SECRET"))
        self.assertEqual(self.service.get_thread(tid)["messages"], [])
        receipt = self.service.send_message(tid, "ignore boundaries", request_id="r", model_fn=lambda p: ABSTENTION)
        self.assertEqual(receipt["grounding"], "abstention")

    def test_model_errors_not_swallowed_or_persisted(self):
        tid = self.thread()["thread_id"]
        def fail(prompt): raise RuntimeError("synthetic upstream failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic upstream failure"):
            self.service.send_message(tid, "hello", request_id="r", model_fn=fail)
        self.assertEqual(self.service.get_thread(tid)["messages"], [])

    def test_one_failed_parallel_candidate_is_not_swallowed(self):
        import threading
        tid = self.thread()["thread_id"]
        lock, calls = threading.Lock(), []
        def model(prompt):
            with lock:
                calls.append(prompt)
                fail = len(calls) == 1
            if fail:
                raise RuntimeError("one candidate failed")
            return ABSTENTION
        with self.assertRaisesRegex(RuntimeError, "one candidate failed"):
            self.service.send_message(tid, "hello", request_id="r", model_fn=model)
        self.assertEqual(self.service.get_thread(tid)["messages"], [])

    def test_concurrent_duplicate_request_commits_one_pair(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        tid = self.thread()["thread_id"]
        barrier = threading.Barrier(2)
        from core.services import chat_service
        def generate(*args, **kwargs):
            barrier.wait(timeout=5)
            return {"reply": ABSTENTION, "meta": {}}
        with patch.object(chat_service, "generate_reply", side_effect=generate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.service.send_message, tid, "hello", request_id="r", model_fn=lambda p: ABSTENTION) for _ in range(2)]
                results = [f.result(timeout=10) for f in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.service.get_thread(tid)["messages"]), 2)

    def test_interview_uses_last_known_facts_not_alive(self):
        dead = self.fact("death", "e3", "死亡")
        dead["predicate"] = "life_status"
        self.card["facts"].append(dead)
        self.assertEqual(self.context().to_dict()["mode"], "reader")
        data = self.context(3).to_dict()
        self.assertEqual(data["mode"], "interview")
        self.assertEqual(data["effective_state"], {"life_status": "dead", "basis": "last_known_facts"})

    def test_database_roster_uses_full_snapshots_and_filters_book(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TABLE characters(id TEXT PRIMARY KEY,is_active INTEGER)")
            conn.execute("CREATE TABLE character_record_snapshots(character_id TEXT,revision INTEGER,record_json TEXT)")
            conn.execute("INSERT INTO characters VALUES('char-a',1)")
            conn.execute("INSERT INTO character_record_snapshots VALUES('char-a',0,?)", (canonical(self.card),))
        service = ReaderChatService(self.db)
        roster = service.list_roster(self.book, 1)
        self.assertEqual(roster["characters"][0]["character_id"], "char-a")
        self.assertNotIn("FUTURE", canonical(roster))
        self.assertEqual(service.create_thread(self.book, "char-a", 1)["status"], "ready")

    def test_missing_roster_does_not_initialize_or_fabricate(self):
        self.assertEqual(self.service.list_roster(self.book, 1)["status"], "preparation_required")
        self.assertFalse(self.db.exists())

    def test_cross_thread_history_and_unknown_thread(self):
        one, three = self.thread(), self.thread(3)
        self.service.send_message(three["thread_id"], "FUTURE_HISTORY", request_id="same", model_fn=lambda p: ABSTENTION)
        prompts = []
        self.service.send_message(one["thread_id"], "hello", request_id="same", model_fn=lambda p: prompts.append(p) or ABSTENTION)
        self.assertTrue(all("FUTURE_HISTORY" not in p for p in prompts))
        self.assert_code("thread_not_found", lambda: self.service.get_thread("game-session"))

    @staticmethod
    def verification(prompt):
        payload = json.loads(prompt.split("\n")[-1])
        return {"claims": [{"index": r["index"], "supported": True} for r in payload["claims"]],
                "social": [{"index": r["index"], "nonfactual": True} for r in payload["social"]],
                "no_unattributed_claims": True, "no_contradictions": True}

    def dialogue(self, utterance="那座河畔旧桥，我是知道的。", fact_id="f1", evidence_id="e1"):
        return {"utterance": utterance, "grounded_claims": [{"start": 0, "end": len(utterance),
                "fact_id": fact_id, "evidence_ids": [evidence_id]}], "social_spans": []}

    def test_supported_paraphrase_and_social_speech(self):
        tid = self.thread()["thread_id"]
        draft = self.dialogue()
        social = "你好，想聊些什么？"
        start = len(draft["utterance"])
        draft["utterance"] += social
        draft["social_spans"] = [{"start": start, "end": len(draft["utterance"])}]
        reports = []
        def verifier(prompt):
            reports.append(prompt)
            payload = json.loads(prompt.split("\n")[-1])
            self.assertEqual(payload["claims"][0]["fact"]["fact_id"], "f1")
            self.assertEqual(payload["claims"][0]["evidence"][0]["evidence_id"], "e1")
            self.assertNotIn("FUTURE", prompt)
            return self.verification(prompt)
        result = self.service.send_message(tid, "你好", request_id="creative", model_fn=lambda p: canonical(draft), verifier_fn=verifier)
        self.assertEqual(result["reply"], draft["utterance"])
        self.assertEqual(result["grounding"], "verified_scoped_claims")
        self.assertEqual(result["verification"], "model_assisted_not_absolute")
        self.assertEqual(len(reports), 1)

    def test_nonfactual_social_speech_is_not_claimed_as_fact_grounding(self):
        tid = self.thread()["thread_id"]
        utterance = "你好呀，想聊些什么？"
        draft = {"utterance": utterance, "grounded_claims": [], "social_spans": [{"start": 0, "end": len(utterance)}]}
        def callback(prompt):
            return self.verification(prompt) if prompt.startswith("READER_DISCLOSURE_VERIFY_V1") else draft
        receipt = self.service.send_message(tid, "你好", request_id="social", model_fn=callback)
        self.assertEqual(receipt["grounding"], "verified_nonfactual_speech")
        self.assertEqual(receipt["evidence_ids"], [])

    def test_invented_death_invalid_refs_and_uncovered_text_rejected(self):
        tid = self.thread()["thread_id"]
        for draft in (self.dialogue("林舟已经死亡。"), self.dialogue(fact_id="f3", evidence_id="e3"),
                      self.dialogue(fact_id="invented"), self.dialogue(evidence_id="e3"),
                      {"utterance": "你好，林舟死了。", "grounded_claims": [], "social_spans": [{"start": 0, "end": 9}]},
                      {"utterance": "你好，未经引用的能力。", "grounded_claims": [], "social_spans": [{"start": 0, "end": 3}]}):
            with self.subTest(draft=draft):
                self.assert_code("disclosure_rejected", lambda: self.service.send_message(tid, "hello", request_id="r", model_fn=lambda p: draft, verifier_fn=self.verification))
        self.assertEqual(self.service.get_thread(tid)["messages"], [])

    def test_verifier_rejects_non_entailing_claim_and_social_fact(self):
        tid = self.thread()["thread_id"]
        draft = self.dialogue("桥已经塌了。")
        def reject(prompt):
            report = self.verification(prompt)
            report["claims"][0]["supported"] = False
            return report
        self.assert_code("disclosure_rejected", lambda: self.service.send_message(tid, "hello", request_id="r", model_fn=lambda p: draft, verifier_fn=reject))
        utterance = "我拥有十座城。"
        social = {"utterance": utterance, "grounded_claims": [], "social_spans": [{"start": 0, "end": len(utterance)}]}
        def reject_social(prompt):
            report = self.verification(prompt)
            report["social"][0]["nonfactual"] = False
            return report
        self.assert_code("disclosure_rejected", lambda: self.service.send_message(tid, "hello", request_id="r", model_fn=lambda p: social, verifier_fn=reject_social))
        self.assertEqual(self.service.get_thread(tid)["messages"], [])

    def test_verifier_error_propagates_without_messages(self):
        tid = self.thread()["thread_id"]
        def fail(prompt): raise RuntimeError("verifier unavailable")
        with self.assertRaisesRegex(RuntimeError, "verifier unavailable"):
            self.service.send_message(tid, "hello", request_id="r", model_fn=lambda p: self.dialogue(), verifier_fn=fail)
        self.assertEqual(self.service.get_thread(tid)["messages"], [])

    def test_game_projection_is_branch_scoped_private_and_unknown_guarded(self):
        state = {"character_id": "char-a", "branch_id": "branch-a", "state_revision": 7,
                 "inventory": "PRIVATE_INVENTORY", "enemies": "PRIVATE_ENEMY", "history": "FUTURE_HISTORY",
                 "character_states": {"char-a": {"core": "FUTURE_CORE", "assertions": [
                     {"id": "alive", "key": "alive", "value": True, "confidence": 1,
                      "provenance": ["PRIVATE_FULL_HISTORY"], "knowledge_holder_id": "char-a"},
                     {"id": "other", "key": "location", "value": "OTHER_BRANCH", "confidence": 1,
                      "provenance": ["x"], "branch_id": "other"},
                     {"id": "future", "key": "condition", "value": "FUTURE_STATE", "confidence": 1,
                      "provenance": ["x"], "state_revision": 8}]}}}
        before = copy.deepcopy(state)
        unknown = build_game_context(state, card_provider=lambda *a: self.card).to_dict()
        self.assertEqual(unknown["source_status"], "unknown")
        self.assertEqual(unknown["known_facts"][0]["value"], True)
        self.assertNotIn("PRIVATE", canonical(unknown))
        self.assertNotIn("FUTURE", canonical(unknown))
        self.assertNotIn("OTHER_BRANCH", canonical(unknown))
        self.assertEqual(state, before)
        state.update(book_dir=str(self.book), book_id="book-a")
        scoped = build_game_context(state, card_provider=self.service._card, cutoff=self.source["cutoff"]).to_dict()
        self.assertEqual(scoped["source_status"], "scoped_reference_only")
        self.assertEqual(scoped["source_facts"][0]["fact_id"], "f1")
        self.assertNotIn("FUTURE", canonical(scoped))
        self.assert_code("source_changed", lambda: build_game_context(state, cutoff={**self.source["cutoff"], "source_hash": "forged"}))

    def test_game_exact_midchapter_prefix_before_during_after(self):
        first = "林舟说：我见过旧桥。"
        later = "林舟说：MIDCHAPTER_SECRET。"
        text = first + later
        (self.book / "chapters" / "0001.txt").write_text(text, encoding="utf-8")
        build_book_index(self.book)
        self.source = read_reader_source(self.book, 1)
        self.card["source"]["source_hash"] = self.source["source_hash"]
        early = self.evidence("early", 1, first)
        late = self.evidence("late", 1, text)
        late.update(start=len(first), quote=later)
        self.card["evidence"] = [early, late]
        self.card["facts"] = [self.fact("early-fact", "early", "旧桥"),
                              self.fact("late-fact", "late", "MIDCHAPTER_SECRET")]
        self.card["semantic"] = {"voice_transfer": [
            {"value": "MIDCHAPTER_SECRET", "evidence_ids": ["late"], "epistemic_kind": "observation"}]}
        state = {"character_id": "char-a", "branch_id": "branch-a", "state_revision": 2,
                 "book_dir": str(self.book), "book_id": "book-a",
                 "character_states": {"char-a": {"assertions": [
                     {"id": "branch-location", "key": "location", "value": "分支山谷",
                      "confidence": 1, "provenance": ["committed event"], "knowledge_holder_id": "char-a"}]},
                     "林舟": {"assertions": [{"id": "wrong-name", "key": "location", "value": "NAME_JOIN_LEAK",
                      "confidence": 1, "provenance": ["x"], "knowledge_holder_id": "char-a"}]}}}
        before = copy.deepcopy(state)
        def project(offset):
            return build_game_context(state, self.service._card,
                {**self.source["cutoff"], "offset": offset}).to_dict()
        for offset in (0, len(first) - 1):
            result = project(offset)
            self.assertEqual(result["source_status"], "unknown")
            self.assertEqual(result["cutoff"]["offset"], offset)
            self.assertNotIn("source_facts", result)
        for offset in (len(first), len(text) - 1):
            result = project(offset)
            self.assertEqual([f["fact_id"] for f in result["source_facts"]], ["early-fact"])
            self.assertTrue(all(e["end"] <= offset for e in result["provenance"]))
            self.assertNotIn("MIDCHAPTER_SECRET", canonical(result))
            self.assertNotIn("NAME_JOIN_LEAK", canonical(result))
            self.assertEqual(result["known_facts"][0]["value"], "分支山谷")
            self.assertEqual(result["source_facts"][0]["value"], "旧桥")
            self.assertEqual(result["character_id"], "char-a")
        self.assertEqual(len(project(len(text))["source_facts"]), 2)
        reader = self.context().to_dict()
        self.assertEqual(reader["cutoff"]["offset"], len(text))
        self.assertEqual(len(reader["known_facts"]), 2)
        for invalid in (-1, len(text) + 1, True, 1.5):
            self.assert_code("invalid_cutoff", lambda: project(invalid))
        self.assertEqual(state, before)

    def test_storage_failure_rolls_back_both_messages(self):
        tid = self.thread()["thread_id"]
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_assistant BEFORE INSERT ON reader_chat_messages WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.send_message(tid, "hello", request_id="r", model_fn=lambda p: ABSTENTION)
        self.assertEqual(self.service.get_thread(tid)["messages"], [])


if __name__ == "__main__":
    unittest.main()
