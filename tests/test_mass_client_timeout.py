"""DEF-E-03 回归：MassClient 支持按次调用覆盖 timeout。

sm06 导出是三段模型调用，需要比默认 900s 更长的单请求预算；
客户端必须接受 timeout 形参并转发给 requests，而不是抛 TypeError。
"""
import types

import pytest
import requests

from tools.mass_test.client import MassClient


class _Resp:
    status_code = 200
    text = ""
    headers = {"content-type": "text/plain"}

    def json(self):
        raise ValueError("not json")


class _Throttle:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def backoff_after(self, *a, **k):
        return None


def _client(monkeypatch, tmp_path):
    c = MassClient("http://127.0.0.1:1",
                   {"http": types.SimpleNamespace(write=lambda *a, **k: None,
                                                  find_secret=lambda *a, **k: None)},
                   types.SimpleNamespace(report=lambda *a, **k: None),
                   _Throttle())
    seen = {}

    def fake_request(method, url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _Resp()

    monkeypatch.setattr(c.session, "request", fake_request)
    return c, seen


def test_request_default_timeout(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path)
    c.request("GET", "/x")
    assert seen["timeout"] == pytest.approx(900.0)


def test_request_override_timeout(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path)
    c.request("POST", "/x", json={"style": "webnovel"}, timeout=950.0)
    assert seen["timeout"] == pytest.approx(950.0)


def test_post_accepts_timeout_kwarg(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path)
    c.post("/api/sessions/s/export-novel", json={"style": "light"}, timeout=950.0)
    assert seen["timeout"] == pytest.approx(950.0)


class _TimeoutFirst:
    """第一次调用抛 ReadTimeout，其后记录后续调用。"""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.first = True

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, kwargs.get("json") if kwargs else None))
        if self.first:
            self.first = False
            raise requests.exceptions.ReadTimeout("read timed out")
        return _Resp()


def test_no_auto_retry_body_post_on_readtimeout(monkeypatch, tmp_path):
    """DEF-E-05 回归：带请求体的 POST 读超时后不得自动重发。

    sm06 实测：导出 POST 超过客户端 950s 预算后客户端重试，撞上仍在
    运行的第一个处理器 → 409，且服务端导出被重复执行。带体的 POST
    非幂等，RequestException 后必须直接抛出。
    """
    c, _ = _client(monkeypatch, tmp_path)
    probe = _TimeoutFirst()
    monkeypatch.setattr(c.session, "request", probe)
    with pytest.raises(requests.exceptions.ReadTimeout):
        c.request("POST", "/api/sessions/s/export-novel",
                  json={"style": "literary"}, timeout=1.0)
    assert len(probe.calls) == 1, probe.calls


def test_get_still_retries_on_readtimeout(monkeypatch, tmp_path):
    """幂等 GET 保持原有重试语义。"""
    c, _ = _client(monkeypatch, tmp_path)
    probe = _TimeoutFirst()
    monkeypatch.setattr(c.session, "request", probe)
    resp = c.request("GET", "/api/saves")
    assert resp.status_code == 200
    assert [m for m, _ in probe.calls] == ["GET", "GET"]
