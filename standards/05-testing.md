# 05 测试规范

## 1. 组织与运行

- 全部测试集中在 `tests/`（unittest 风格，`test_*.py`），根目录不放测试，core/ 内不放测试。
- 运行（项目根下）：

```bash
python -m unittest discover -s tests -t .
```

- `tests/__init__.py` 负责把项目根加入 `sys.path`（core 包可导入）；测试文件内不再自行修补 sys.path。
- 需要项目根路径时：`ROOT = Path(__file__).resolve().parent.parent`（tests/ → 项目根，**两级**）。

## 2. 离线铁律

- **绝不打真实模型 API**：模型调用全部 mock（`mock.patch.object(api_server, "distill_model", ...)` / 假 client / `fe.stream_reply` mock）。
- 绝不依赖真实网络、真实 API Key、真实外部服务。
- 写盘一律 `tempfile.TemporaryDirectory()`，测试间隔离（SessionManager 用 `addCleanup` 还原单例）。

## 3. mock 边界（历史事故清单）

- 蒸馏通道 mock 目标：`api_server.distill_model`（端点直接导入的那个），**不是** `gradio_app._distill_model`——后者只影响 app 内部调用。两个通道都存在时按被测代码的实际调用点选择。
- 客户端 mock：`mock.patch.object(api_server.fe, "make_client", return_value=FakeClient())`；FakeClient 的 `create(**kwargs)` 返回带 `choices[0].message.content` 的假响应。
- 字符串形式 mock 目标（`mock.patch("core.engine.xxx.yyy")`）要写全 `core.` 前缀——裸 `engine.xxx` 已不存在。
- patch 环境变量不用 `mock.patch.dict(os.environ)` 全量替换（会清空宿主超长环境），用单变量设置+还原（test_api_novel_export 先例）。

## 4. 覆盖要求

新功能必须带回归测试，且优先覆盖**失败路径**：

- 空结果/异常输入 → 必须抛错或降级，不得静默（空 content 降级、空 JSON 解析、空摘要先例）。
- 兼容性阶梯：逐级降级路径各有用例（参 `test_distill_channel.py`）。
- 玩家可见文本：断言无英文异常名、无内部字段名、无 JSON 符号（`GameplayBriefingTests.test_briefing_has_no_internal_terms` 先例）。
- 结构卫生：文件路径、构建脚本、.gitignore 的断言放 `test_offline_suite.WiringAndHygieneTests`。

## 5. 端到端回归

- `tests/test_strengthened_playtest.py`：强化模式全流程（确认金手指→确认开局→第一幕→回滚→存档）子进程回归；子进程 `PYTHONPATH` 必须含项目根。
- 任何开局链路改动必须跑此测试 + `test_opening_flow.py` + `test_gameplay_wiring.py`。

## 6. 完成定义

- `discover` 全绿（当前 719 用例，1 skip）才算完成；**禁止**留下新失败，禁止用 skip 掩盖失败。
- 修复 bug 时先写/改失败测试复现，再修实现，最后全量回归。
