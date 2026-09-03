# 01 架构规范

## 1. 目录职责（不可逾越）

| 目录 | 职责 | 禁止 |
|---|---|---|
| `core/` | 全部引擎与运行时代码（Python 包，含 `__init__.py`） | 放数据文件（JSON/MD/TXT/CSV）；放测试 |
| `core/engine/` | 机制层：纯计算为主，不直接读用户配置 | 依赖 `core.app`/`core.fate_engine`（除已声明的惰性例外） |
| `core/app.py` | 对局流程编排（on_start/on_send 状态机） | 直接拼模型请求参数（经 `core.engine.distill` 通道） |
| `core/fate_engine.py` | 模型接入层：客户端、流式、思考参数映射、规则装配 | 写业务状态（状态机归 app.py） |
| `core/server.py` | FastAPI 端点 + 静态托管 + 会话接线 | 写机制逻辑（调 engine/app） |
| `core/api/` | API 边界纯函数（契约/脱敏/会话生命周期） | 依赖 engine 以外的东西 |
| `core/ui/` | 界面辅助（面板渲染、状态栏、名册表单） | 被 engine 依赖 |
| `assets/` | 全部静态数据 | 放 `.py`（trope_index.py 等历史工具除外，逐步迁出） |
| `var/` | 全部运行数据 | 提交到版本库（.gitignore 已忽略） |
| `tests/` | 全部测试 | 放生产代码 |
| `frontend/` | Vue 独立工程 | 后端代码进入（只经 /api 通信） |

## 2. 依赖方向（单向，禁止反向）

```
run_app.py → core.server → core.app（对局流程）→ core.engine（机制）→ assets（数据）
                  │              │
                  ├──────────────┴→ core.fate_engine（模型接入）
                  └→ core.api（契约/会话）
core.engine ──仅两个已声明惰性例外──→ core.fate_engine（distill.py）
                                  └→ core.app（character_library.py:286）
```

- 新增 `engine → fate_engine/app` 依赖必须走**函数内惰性导入**并在 docstring 声明原因（现有两处是天花板，不要再加）。
- `core.lore` / `core.memory` 只允许被 `core.app` / `core.ui` 使用，engine 不得依赖。

## 3. 启动与进程模型

- 唯一入口 `run_app.py` → `core.server:app`（uvicorn，:8000）→ 托管 `frontend/dist`。
- 前端与 API 同端口；Vite dev（:5173）仅限开发调试。
- 后台任务（锚点蒸馏）为守护线程，注册表在 `core.app._DISTILLERS`，按书目录 key 隔离，开局/回合时经 `enqueue` 推进。

## 4. 配置与凭据

- 用户配置：`var/config.json`（非敏感）与 `var/.env`（敏感），由 `core/fate_engine.py` 的 `WRITABLE_DIR` 定位。
- API Key：请求体/环境变量/界面输入 → 进程内存；**永不写入** state、存档、日志、会话记录。

## 5. 新增模块的落位决策树

1. 是机制/规则计算？→ `core/engine/<name>.py`（并在 `core/engine/__init__.py` 的 `_LAZY_EXPORTS` 登记）
2. 是对局流程分支？→ `core/app.py` 内函数
3. 是 API 端点？→ `core/server.py` + 契约入 `core/api/contracts.py`
4. 是数据？→ `assets/` 对应子目录
5. 是工具脚本？→ `tools/`（一次性维护脚本）或 `tools/playtest_kit/`（试玩）
6. 以上都不是 → 先停下来重想，不要新建顶层目录
