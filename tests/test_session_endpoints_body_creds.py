# -*- coding: utf-8 -*-
"""DEF-E-02 回归：回合外模型端点须接受请求体兜底凭据。

session.api_key 只在 /start 提交时驻留内存（密钥不落盘，安全设计）；实例重启后
从盘恢复的会话 api_key 为空。/ask、/quests/offer、/autoplay-choice 此前只读
session.api_key，重启后对已有会话永久 400。约定与 copilot 一致：
会话凭据优先，请求体兜底；请求体凭据只在内存使用，不得落盘。
"""
import json
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from core import server

# 哨兵密钥运行时生成：测试只断言「哪个键被使用/不被持久化」，不硬编码凭据。
BODY_KEY = "unittest-body-" + uuid.uuid4().hex
SESS_KEY = "unittest-session-" + uuid.uuid4().hex


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _StubClient:
    """OpenAI 兼容客户端桩：chat.completions.create 返回固定文本。"""

    def __init__(self, content="答案文本"):
        outer = self

        class _Completions:
            def create(self, **kwargs):
                return _Resp(outer._content)

        self.chat = type("Chat", (), {"completions": property(lambda s: _Completions())})()
        self._content = content


def _seed_session(api_key=""):
    ident = uuid.uuid4().hex
    session = server.sessions.create(ident)
    session.api_key = api_key
    # committed 契约：system + 完整有序 A–F 六选项（save_contract.is_usable_state）。
    options = [{"key": k, "text": f"行动{k}", "preview": "", "factor": "金手指",
                "factors": []} for k in "ABCDEF"]
    session.state = {
        "game_ready": True,
        "system": "命运引擎基础运行规则（测试桩）",
        "save_stage": "committed",
        "provider": "custom",
        "base_url": "http://127.0.0.1:9/v1",
        "model": "stub-model",
        "history": [],
        "round": 2,
        "options": options,
    }
    return ident, session


class AskBodyCredsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_ask_falls_back_to_body_key_after_restart(self):
        ident, _session = _seed_session(api_key="")
        captured = {}

        def fake_make_client(api_key, provider, base_url):
            captured["api_key"] = api_key
            return _StubClient("状态面板里的金钱是 24 元。")

        with mock.patch.object(server.fe, "make_client", side_effect=fake_make_client):
            resp = self.client.post(f"/api/sessions/{ident}/ask", json={
                "question": "状态面板里的金钱数值是多少？", "api_key": BODY_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("answer"), "状态面板里的金钱是 24 元。")
        self.assertEqual(captured.get("api_key"), BODY_KEY)

    def test_ask_prefers_session_key_over_body(self):
        ident, _session = _seed_session(api_key=SESS_KEY)
        captured = {}

        def fake_make_client(api_key, provider, base_url):
            captured["api_key"] = api_key
            return _StubClient("答案")

        with mock.patch.object(server.fe, "make_client", side_effect=fake_make_client):
            resp = self.client.post(f"/api/sessions/{ident}/ask", json={
                "question": "规则是什么？", "api_key": BODY_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(captured.get("api_key"), SESS_KEY)

    def test_ask_without_any_key_still_rejected(self):
        ident, _session = _seed_session(api_key="")
        resp = self.client.post(f"/api/sessions/{ident}/ask",
                                json={"question": "规则是什么？"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Missing credentials", resp.text)

    def test_body_key_never_persisted_to_state(self):
        ident, _session = _seed_session(api_key="")

        def fake_make_client(api_key, provider, base_url):
            return _StubClient("答案")

        with mock.patch.object(server.fe, "make_client", side_effect=fake_make_client):
            self.client.post(f"/api/sessions/{ident}/ask", json={
                "question": "规则是什么？", "api_key": BODY_KEY})
        state = self.client.get(f"/api/sessions/{ident}/state").json()
        self.assertNotIn(BODY_KEY, json.dumps(state, ensure_ascii=False, default=str))


class QuestOfferBodyCredsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.quest_json = json.dumps({
            "title": "查清旧楼来历",
            "requirements": ["找到德源当的旧账", "记下守楼人特征"],
            "goal": "弄清台球厅老楼三十年前的真相",
            "plot_hook": "当票上的名字被水渍泡开",
        }, ensure_ascii=False)

    def test_offer_falls_back_to_body_key_after_restart(self):
        ident, _session = _seed_session(api_key="")
        captured = {}

        def fake_make_client(api_key, provider, base_url):
            captured["api_key"] = api_key
            return _StubClient(self.quest_json)

        with mock.patch.object(server.fe, "make_client", side_effect=fake_make_client), \
                mock.patch.object(server, "distill_model", return_value=self.quest_json):
            resp = self.client.post(f"/api/sessions/{ident}/quests/offer", json={
                "kind": "short", "difficulty": 0.5, "api_key": BODY_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(captured.get("api_key"), BODY_KEY)


class AutoplayBodyCredsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_autoplay_falls_back_to_body_key_after_restart(self):
        ident, _session = _seed_session(api_key="")
        captured = {}
        choice_json = json.dumps({"choice": "A", "reason": "稳妥"}, ensure_ascii=False)

        def fake_make_client(api_key, provider, base_url):
            captured["api_key"] = api_key
            return _StubClient(choice_json)

        with mock.patch.object(server.fe, "make_client", side_effect=fake_make_client), \
                mock.patch.object(server, "distill_model", return_value=choice_json):
            resp = self.client.post(f"/api/sessions/{ident}/autoplay-choice", json={
                "api_key": BODY_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("choice"), "A")
        self.assertEqual(captured.get("api_key"), BODY_KEY)


class ExportNovelBodyCredsTest(unittest.TestCase):
    """DEF-E-04 回归：/export-novel（会话版）同样要有请求体兜底凭据。"""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_session_export_falls_back_to_body_key_after_restart(self):
        ident, _session = _seed_session(api_key="")
        captured = {}

        def fake_run_export(state, style, creds):
            captured["api_key"] = creds.get("api_key")
            return {"ok": True}

        with mock.patch.object(server, "_run_export", side_effect=fake_run_export):
            resp = self.client.post(f"/api/sessions/{ident}/export-novel",
                                    json={"style": "webnovel", "api_key": BODY_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(captured.get("api_key"), BODY_KEY)

    def test_session_export_prefers_session_key_over_body(self):
        ident, _session = _seed_session(api_key=SESS_KEY)
        captured = {}

        def fake_run_export(state, style, creds):
            captured["api_key"] = creds.get("api_key")
            return {"ok": True}

        with mock.patch.object(server, "_run_export", side_effect=fake_run_export):
            resp = self.client.post(f"/api/sessions/{ident}/export-novel",
                                    json={"style": "light", "api_key": BODY_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(captured.get("api_key"), SESS_KEY)


if __name__ == "__main__":
    unittest.main()
