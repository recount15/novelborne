# -*- coding: utf-8 -*-
"""大规模全真测试编排器：真实书目 + 真实模型 + 每实例实时日志 + 质量瑕疵检测。

安全约束：
- 凭据只从环境变量读取（FATE_TEST_API_KEY / FATE_TEST_BASE_URL / FATE_TEST_MODEL），
  绝不写入本包源码、日志、报告；
- 只终止本包启动并记录 PID 的进程；
- 私有书目内容只进本地 var/outputs，不进任何对外产物。
"""
