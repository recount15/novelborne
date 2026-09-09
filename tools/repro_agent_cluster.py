# -*- coding: utf-8 -*-
"""离线复现 enhanced_agent 卡点：直接调用 turn_pipeline.run_turn（agent_cluster 分支）。

用卡住会话的完整运行态（经 /api/sessions/{sid}/state 导出的合并态）+ 指向
本地 mock 的 client 复现开局第一幕生成，让被 ClusterError 吞掉的内层异常
以完整 traceback 裸露。只用于诊断，不属于产品代码路径。

用法：
    python tools/repro_agent_cluster.py <state.json> [mock_base_url]
"""
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_VAR = os.path.join(os.environ.get("TEMP", "/tmp"), "nb-matrix-var")
os.environ["FATE_VAR_DIR"] = _VAR

from core import fate_engine as fe  # noqa: E402  必须在设置 FATE_VAR_DIR 后导入

assert "nb-matrix-var" in fe.WRITABLE_DIR, "WRITABLE_DIR 未指向矩阵测试 var: %s" % fe.WRITABLE_DIR

state_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("TEMP", "/tmp"), "nb-matrix-evidence", "stuck-agent-state.json")
mock_url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:21593"

with open(state_path, encoding="utf-8") as fh:
    payload = json.load(fh)
state = payload.get("state", payload)
print("state: agent_mode=%s story_agent_mode=%s tier=%s mode=%s round=%s" % (
    state.get("agent_mode"), state.get("story_agent_mode"),
    state.get("paper_tier"), state.get("mode"), state.get("round")))

import core.services.agent_cluster_service as acs  # noqa: E402
import core.services.generation_skills as gs  # noqa: E402
from core.services import turn_pipeline  # noqa: E402


def _expose(name, fn):
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except BaseException:
            print("\n===== inner exception in %s =====" % name, file=sys.stderr)
            traceback.print_exc()
            raise
    return inner


for _name in ("build_turn_snapshot", "model_callbacks", "validate_turn_output"):
    setattr(gs, _name, _expose(_name, getattr(gs, _name)))
acs.AgentClusterService.generate = _expose("AgentClusterService.generate",
                                           acs.AgentClusterService.generate)

client = fe.make_client("mock-key", "custom", mock_url)
try:
    result = turn_pipeline.run_turn(
        state, client, "mock-model", {}, "custom",
        message="开始第一幕。请依据已确认设定生成正式开场。",
        system_prompt=state.get("system") or "", tier=6)
    if isinstance(result, str):
        print("RESULT: LEGACY signal %r" % result)
    else:
        print("RESULT: narrative %d chars, options %d, paper_key=%s" % (
            len(result.narrative or ""), len(result.options or []),
            getattr(result, "paper_key", None)))
except BaseException:
    print("\n===== run_turn top-level exception =====", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
