# -*- coding: utf-8 -*-
"""fate-engine 测试工作包（纯工具，零业务数据）。

自包含检验工具集：对运行中的 api_server 做端到端真实模型检验，
或绕过 HTTP 直接驱动 on_send 链路做类Agent模式对比实测。

模块一览：
- pipeline      后台线程检验管线（SSE 推送 + 内存池 + jsonl 留档），api_server 桥接
- runner        全流程 HTTP 检验 runner（bootstrap→开局→回合→导出），供 pipeline 调度
- standalone    独立命令行版全功能检验（不经 api_server 的 playtest 端点）
- agent_pilot   类Agent模式开/关对比实测 pilot（直接驱动 on_send 链路）
- monitor.html  浏览器实时监控页（/playtest-monitor 静态提供）
"""
from tools.playtest_kit import pipeline, runner, standalone  # noqa: F401

__all__ = ["pipeline", "runner", "standalone"]
