# fate-engine 测试工作包（playtest_kit）

**定位：纯工具、零业务数据。** 本目录只含检验代码；原著 TXT、正文样本、
报告 JSON 等一切业务数据均不在此存放，缺省样本从 `data/samples/` 读取，
产物统一写到 `outputs/`。

## 模块一览

| 文件 | 职责 | 入口方式 |
|---|---|---|
| `pipeline.py` | 后台线程检验管线：SSE 推送 + 内存池 + `outputs/playtest_live.jsonl` 留档，单例运行态 | 由 `api_server.py` 的 `/api/playtest/*` 端点调度 |
| `runner.py` | 全流程 HTTP 检验 runner：bootstrap→上传→开局→两步确认→回合→任务/托管/作弊码/存读档→导出 | 由 pipeline 调度 |
| `standalone.py` | 独立命令行版全功能 HTTP 检验（不走 playtest 端点） | `python -m tools.playtest_kit.standalone` |
| `agent_pilot.py` | 类Agent模式开/关对比实测 pilot：绕过 HTTP 直接驱动 `on_send` 链路 | `python -m tools.playtest_kit.agent_pilot` |
| `monitor.html` | 浏览器实时监控页 | 访问 `/playtest-monitor` |
| `run_tests.py` | 一键入口：unit / all / live / pilot 四个套件 | `python tools/playtest_kit/run_tests.py [suite]` |

覆盖范围补充：standalone/runner 的 N 回合主循环里，性格结算（每回合 tick_after_action）
与碎锚状态机（积势攒满即触发 offer→accept→逐阶段结算）会被对局自然打到，无需专门用例。

## 快速上手

```bash
# 1) 本地 mock 回归（不需要 key、不联网）
python tools/playtest_kit/run_tests.py unit    # 全量 unittest
python tools/playtest_kit/run_tests.py all     # unittest + 强化试玩端到端

# 2) 真实模型 HTTP 检验（先启动 api_server）
export FATE_API_KEY=sk-xxx        # 只经环境变量传递，绝不落盘
export FATE_PROVIDER=zhipu        # 可选 deepseek / zhipu / custom ...
export FATE_MODEL=glm-5.3-flash
python tools/playtest_kit/run_tests.py live --rounds 30

# 3) 类Agent模式对比实测（不经 api_server）
python tools/playtest_kit/run_tests.py pilot --rounds 2
# 或旧入口（向后兼容薄包装）：python tools/run_agent_mode_pilot.py
```

浏览器实时观看在线检验：启动后打开 `http://127.0.0.1:8000/playtest-monitor`。

## 配置注入（无硬编码业务常量）

- **供应商 / base_url / 模型**：全部 CLI 参数或 `FATE_PROVIDER` / `FATE_BASE_URL`
  / `FATE_MODEL` 环境变量；api_server 端点则由请求体字段决定。
- **原著 TXT**：优先级 启动配置 `txt_path` > `PLAYTEST_TXT` 环境变量 >
  缺省 `data/samples/强化试玩书.txt`。
- **API key**：只经内存 / 环境变量传递；日志与报告仅写掩码。

## 与主程序的契约边界

`api_server.py` 对本包的依赖（改动前必须保持）：

```python
from tools.playtest_kit import pipeline as playtest_pipeline   # /api/playtest/*
from tools.playtest_kit import runner   as playtest_runner     # start_run(config, runner.run)
PLAYTEST_MONITOR = PROJECT_ROOT / "tools" / "playtest_kit" / "monitor.html"
```

监控页与状态接口的快照结构见 `pipeline._Reporter.snapshot()`。

## 数据边界声明

- 本包不携带任何小说文本、锚点 JSON、样本正文或报告数据；
- 缺省试玩样本在仓库中的唯一权威位置是 `data/samples/强化试玩书.txt`
  （2967 B，三章节小型边城悬疑故事，供本地切章/蒸馏链路回归用）；
- 各脚本运行产物（报告 JSON、回合日志 jsonl、正文样本 txt）一律写入
  项目根 `outputs/` 目录。
