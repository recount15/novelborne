# -*- coding: utf-8 -*-
"""局域网信息、二维码和会话接续回归测试。"""
from __future__ import annotations

import os
import uuid
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from core import server


class LanSessionIdTest(unittest.TestCase):
    def test_hex_uuid_is_validated_but_not_reformatted(self):
        ident = uuid.uuid4().hex
        self.assertEqual(server._lan_session_id(ident), ident)
        self.assertNotIn("-", ident)

    def test_hyphenated_uuid_is_preserved(self):
        ident = str(uuid.uuid4())
        self.assertEqual(server._lan_session_id(ident), ident)

    def test_invalid_session_is_removed(self):
        self.assertIsNone(server._lan_session_id("not-a-session"))
        self.assertIsNone(server._lan_session_id(""))


class LanInfoTest(unittest.TestCase):
    def test_multiple_addresses_return_multiple_urls_and_exact_session(self):
        ident = uuid.uuid4().hex
        with mock.patch.object(server, "_lan_addresses", return_value=["192.168.1.20", "10.0.0.8"]), \
                mock.patch.object(server, "_LAN_LISTENING", True):
            info = server._lan_info(8765, ident)
        self.assertEqual(info["session_id"], ident)
        self.assertEqual(len(info["urls"]), 2)
        self.assertEqual(info["url"], info["urls"][0]["url"])
        for item in info["urls"]:
            parsed = urlparse(item["url"])
            self.assertEqual(parsed.port, 8765)
            self.assertEqual(parse_qs(parsed.query).get("session"), [ident])
        self.assertTrue(info["listening_lan"])

    def test_loopback_listener_reports_not_listening(self):
        with mock.patch.object(server, "_lan_addresses", return_value=["192.168.1.20"]), \
                mock.patch.object(server, "_LAN_LISTENING", False), \
                mock.patch.dict(os.environ, {"FATE_API_HOST": "127.0.0.1"}):
            info = server._lan_info(8000)
        self.assertFalse(info["listening_lan"])
        self.assertIn("回环", info["hint"])

    def test_no_address_has_clear_hint(self):
        with mock.patch.object(server, "_lan_addresses", return_value=[]):
            info = server._lan_info(8000)
        self.assertIsNone(info["url"])
        self.assertEqual(info["urls"], [])
        self.assertIn("IPv4", info["hint"])


class LanRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.ident = uuid.uuid4().hex
        session = server.sessions.create(self.ident)
        session.state = {"game_ready": True, "round": 3}

    def test_info_url_restores_existing_hex_session(self):
        with mock.patch.object(server, "_lan_addresses", return_value=["192.168.50.10"]), \
                mock.patch.object(server, "_LAN_LISTENING", True):
            response = self.client.get("/api/lan-info", params={"session_id": self.ident})
        self.assertEqual(response.status_code, 200)
        info = response.json()
        self.assertIn(self.ident, info["url"])
        linked = urlparse(info["url"])
        linked_id = parse_qs(linked.query)["session"][0]
        self.assertEqual(linked_id, self.ident)
        state_response = self.client.get(f"/api/sessions/{linked_id}/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["session_id"], self.ident)

    def test_qrcode_png_for_selected_adapter(self):
        with mock.patch.object(server, "_lan_addresses", return_value=["192.168.50.10", "10.0.0.8"]), \
                mock.patch.object(server, "_LAN_LISTENING", True):
            response = self.client.get(
                "/api/lan-qrcode.png",
                params={"session_id": self.ident, "address": "10.0.0.8"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(response.content), 200)

    def test_qrcode_rejects_unknown_adapter(self):
        with mock.patch.object(server, "_lan_addresses", return_value=["192.168.50.10"]):
            response = self.client.get(
                "/api/lan-qrcode.png", params={"address": "203.0.113.1"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
