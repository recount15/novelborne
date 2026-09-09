# 书中行 · 命运引擎 — 项目规范（AI 协作宪法）

> **本目录是 AI 与开发者修改本项目前必须阅读的规范。**
> 目标：任何改动不破坏「单一入口、单一文档、引擎集中于 core/、数据集中于 assets/、运行数据集中于 var/」的统一结构。

## 红线速览（违反任何一条都算事故）

1. **唯一入口**：`run_app.py`。禁止新增其他启动脚本/入口文件。
2. **唯一用户文档**：`README.md`。禁止新建面向用户的说明文档；docs/ 仅存历史档案且必须带「⚠️ 历史文档」标注。
3. **代码只进 `core/`**：引擎、服务、UI 辅助、lore、memory、提示词加载器都在 `core/` 包内；禁止在项目根新建 `.py` 模块。
4. **内部导入只用 `from core import ...` / `from core.xxx import ...` 绝对导入**。禁止裸写 `import app` / `import fate_engine` / `import engine` / `from engine.xxx`，禁止任何形式的 `sys.path` 修补。
5. **静态数据只进 `assets/`**：rules / data / personas / prompts / lore。禁止在 `core/` 里放数据文件（JSON/MD/TXT）。
6. **运行数据只进 `var/`**：存档、日志、数据库、切章书库、上传、输出。写盘路径必须经 `fe.WRITABLE_DIR` 或 `PROJECT_ROOT / "var"` 派生，禁止硬编码项目根相对路径。
7. **凭据永不落盘**：API Key 只在内存与请求体传递；写入 state/存档/日志前必须脱敏（`redact_secrets` / `_safe_distill_error` 已有先例）。
8. **改动必须跑回归**：`python -m unittest discover -s tests -t .` 全绿（当前 719 用例，1 skip）才算完成；禁止引入新失败。
9. **删除即更新引用**：删文件/移目录前必须 grep 全部引用点（代码、测试、构建脚本、spec、文档），一处不漏。
10. **玩家可见文本纯中文**：任何面向玩家的输出不得出现英文异常名、内部字段名、原始 JSON。

## 规范索引

| 文件 | 主题 | 何时必读 |
|---|---|---|
| [01-architecture.md](01-architecture.md) | 架构规范：目录职责、依赖方向、模块边界 | 新增/移动/删除任何文件前 |
| [02-code.md](02-code.md) | 代码规范：导入、命名、错误处理、兼容降级 | 写任何 Python/TS 代码前 |
| [03-data.md](03-data.md) | 数据规范：数据落位、路径派生、schema 变更 | 动数据文件或读写路径前 |
| [04-api.md](04-api.md) | 接口规范：端点形态、事件契约、脱敏 | 新增/修改 API 端点前 |
| [05-testing.md](05-testing.md) | 测试规范：离线原则、目录组织、mock 边界 | 写/改测试前 |
| [06-changes.md](06-changes.md) | 更改提交规范：改动流程、备份、验证、文档同步 | 每次改动执行时 |
| [07-files.md](07-files.md) | 文件保存规范：命名、编码、落位、清理 | 新建任何文件前 |

## 结构一图

```
fate-engine/
├── run_app.py     # 唯一入口
├── README.md      # 唯一用户文档
├── core/          # 全部引擎代码（server/app/fate_engine/engine/api/ui/lore/memory/prompts）
├── assets/        # 全部静态数据（rules/data/personas/prompts/lore）
├── var/           # 全部运行数据（db/books/saves/sessions/logs/outputs/uploads）
├── tests/         # 全部测试
├── tools/         # 试玩与维护脚本
├── build/         # exe 构建脚本与 spec
├── frontend/      # Vue 前端（独立 npm 工程）
├── docs/          # 历史设计档案（只读）
└── standards/     # 本目录：项目规范
```
