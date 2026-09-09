# 书中织梦（Novelborne）代码结构与功能说明

> 适用版本：v3.0.0（2026 年 9 月）。本文描述当前代码库的真实结构与功能边界，与 [用户手册](USER_MANUAL.md) 配套阅读；使用步骤请以用户手册为准。

## 1. 技术栈与总体架构

- **后端**：Python 3.10+ / FastAPI / SQLite，单进程本地服务，同时托管前端构建产物（同端口）。
- **前端**：Vue 3 + TypeScript + Vite，多套主题（`--fe-*` 语义变量），桌面/移动端自适应。
- **模型服务**：叙事生成、全书蒸馏、人物访谈调用外部 OpenAI 兼容服务；原文阅读与文本搜索不调用模型。

分层调用单向：`server → app → services → engine`，不得反向依赖。

```text
run_app.py（唯一进程入口：--host/--port/--var/--no-browser）
  → core/server.py        FastAPI 路由层：参数校验、会话互斥锁、NDJSON 流适配、
                          静态托管（83 个路由）
    → core/app.py         对局编排层：on_start（开局装配）/ on_send（回合主流程）
      → core/services/    服务层：业务编排 + 模型注入（38 个服务模块）
        → core/engine/    机制层：纯计算、无 IO（74 个机制模块）
          → assets/       静态资源：规则、提示词、模板库、世界书
var/                      全部运行数据（自动创建，不入库）：数据库、会话、上传、日志
```

**两条硬约束**：

1. **契约脱敏红线**：对客户端返回的状态一律经 `core/api/contracts.py` 的 `public_state()` 脱敏，`system`、`api_key`、宿敌私有信息等永不外泄。
2. **密钥只进请求体**：模型 API Key 仅通过请求体提交、仅存内存，不写查询参数、磁盘或存档。

## 2. 仓库顶层结构

```text
├── run_app.py            # 唯一进程入口
├── setup_and_run.py      # 一键构建启动：环境检查 → 前端构建 → 启动（不自动装依赖）
├── start.bat             # Windows 双击入口（调用 setup_and_run.py）
├── requirements.txt      # Python 依赖
├── core/                 # 后端全部源码（见 §3）
├── frontend/             # Vue 3 前端（见 §4；node_modules/dist 不入库）
├── assets/               # 静态资源（见 §3.6）
├── tests/                # 后端测试套件
├── scripts/ tools/       # 开发与数据维护脚本（非运行时依赖）
├── standards/ examples/  # 规范与示例
├── build/                # 打包配方（FateEngine.spec、build_windows.bat 等）
├── docs/                 # 本文档与用户手册、用户手册截图
└── var/                  # 运行数据目录（程序自动创建，绝不入库）
```

## 3. 后端 core/

### 3.1 顶层

| 文件 | 职责 |
| --- | --- |
| `server.py` | FastAPI 路由层：Pydantic 校验、会话级互斥（并发请求返回 409）、流式端点 NDJSON 适配、前端静态托管 |
| `app.py` | 对局编排：`on_start` 开局装配与开场生成、`on_send` 回合主流程（事务快照 → 生成 → 门禁 → 结算 → 存档） |
| `fate_engine.py` | 模型接入门面：provider 配置、OpenAI 兼容客户端、上传与作品库路径 |
| `state_schema.py` | 回合事务键的唯一定义来源 |

### 3.2 core/api/（契约层）

| 文件 | 职责 |
| --- | --- |
| `contracts.py` | StreamEvent 事件契约、`public_state()` 脱敏与前端直读键合成 |
| `sessions.py` | 会话注册表与生命周期、每会话上传目录、内存凭据 |
| `operations.py` / `save_contract.py` | 操作与存档契约 |

### 3.3 core/services/（服务层，按职责域分组）

| 职责域 | 模块 |
| --- | --- |
| 书库与准备任务 | `book_library_service` `book_prepare_service` `book_search_service` `preparation_jobs_service`（全书准备任务的状态机：进行中 / READY / FAILED / INTERRUPTED / CANCELLED，含取消与恢复） |
| 角色域 | `character_service` `character_context_service` `character_evidence_service` `character_state_service` `role_context_projection`（数据库为唯一活动角色库，档案分层与原文证据） |
| 阅读器域 | `reader_chat_service`（章末访谈线程）、`reader_start_service`（章首开局）、`scene_locator_service`（位置定位） |
| 生成域 | `turn_pipeline`（回合中台）、`story_agent` `choice_agent` `agent_cluster_service`（simple / agent_cluster 两种策略）、`options_service` `narrative_flow_grader` `quest_grader` `chat_grader`（各域批改器）、`answer_polish_service` `generation_skills` |
| 开局与上下文 | `opening_service` `pre_game_service` `game_setup` `golden_finger_service` `story_context` `context_retrieval_service` `directives_service` |
| 基础设施 | `model_gateway`（provider/凭据/客户端唯一接线）、`native_gateway` `provider_cache` `registries`（中立注册表，断分层循环）、`ask_service` `structured_question_service` |

### 3.4 core/engine/（机制层，纯计算，按职责域分组）

| 职责域 | 模块（代表） |
| --- | --- |
| 回合与质量 | `turn_blueprint` `turn_composer` `turn_grader` `turn_transaction` `agent_refill` `agent_mode` `quality_gate` `elastic_gate`（批改-重填循环与 Keep-best，硬门禁不因模型意见放行） |
| 全书准备与蒸馏 | `anchor_distiller` `opening_distill` `plot_summary` `work_distiller` `character_semantic_distiller` `book_index` `chapter_tools` `chapter_arc` |
| 人物机制 | `character_db`（SQLite 角色库）`character_library` `character_designer` `character_creation_protocol` `roster` `roster_schema` `roster_relevance` `catalog` `skill_drift` `nemesis_agent` `name_collision` |
| 世界与剧情 | `quest` `break_anchor` `free_stage`（碎锚后自由线）`ripple` `faction` `dynamic_convergence` `story_graph` `story_ledger` `story_invariants` `story_snapshot` `plot_threading` `counterfactual` |
| 金手指与指令 | `golden_finger` `gf_designer` `cheat_code` `directives` |
| 支撑 | `distill`（模型调用统一通道）`token_accounting` `persistence`（存读档，原子替换）`context_compressor` `participation` `options` `tropes` `textkit` `papers` `parallel` |

### 3.5 其余子包

- `core/memory/`：结构化状态记忆（schema、状态提取、快照存储与校验）。
- `core/lore/`：动态世界书（条目 schema、关键词匹配冷却、按预算注入）。
- `core/prompts/`：提示词加载器（`assets/prompts`，`@@KEY@@` 渲染）。
- `core/ui/`：早期 Gradio 时代的展示辅助与共用纯函数，仅遗留引用。

### 3.6 assets/ 与 var/

- `assets/`：`prompts/`（提示词模板）、`rules/`、`data/`（技能目录、桥段库、2 万条通用叙事模板库 `layered_corpus.json`——组合生成的通用模板，不含任何原著文本）、`lore/`（默认世界书）、`papers/`（六档试卷配置）。
- `var/`：全部运行数据——SQLite 数据库、会话存档、上传原著、日志。删除该目录即彻底清除本实例数据。

## 4. 前端 frontend/src/

| 位置 | 职责 |
| --- | --- |
| `kernel/` | `apiClient.ts`（统一 base URL / JSON / NDJSON / 错误转换）、`platform.ts`（平台适配器）、`useNarrativeView.ts`（叙事正文流式渲染视图） |
| `shells/` | 四端壳层：`WebShell`（桌面浏览器）、`MobileWebShell`（手机网页，触控与安全区）、`WindowsShell`（pywebview 窗口）、`AndroidShell`（Capacitor） |
| `App.vue` | 共享工作台内容层（叙事工作台主界面） |
| `views/CharacterDesigner.vue` | 角色设计器分步流程 |
| `components/` | `LibraryScene`（我的书库）、`CharacterDossier`（人物档案）、`OriginalReaderModal`（原著阅读器）、`ReaderChatPanel`（阅读访谈）、`PreparationPanel` / `PreparationJobPanel`（全书准备任务）、`GenerationStatePanel`（生成状态）、`NovelExportModal`、`LanQrModal`、`ThemePicker` |
| `components/reader/` | 阅读器抽屉：书签、本章锚点、本章活跃人物 |
| `components/theme/` | `ThemeFrame` / `ThemeBadge` / `ThemeProgress` 公共主题组件 |
| `composables/` | 流式状态、阅读器状态、场景闲聊、角色池、UI 状态持久化等组合式逻辑 |
| `assets/themes/` | 多主题 CSS（`--fe-*` 语义变量） |

固定接线原则：共享业务只依赖 `kernel` 与领域组件；壳层以显式 `variant` 决定排列与平台能力，禁止以 UA 猜测产品形态。

## 5. HTTP API 概览（83 个路由，按功能分组）

服务启动后可访问 `http://127.0.0.1:21560/docs` 查看交互式文档。生成类端点返回 NDJSON 流（每行一个 JSON 事件：`state` / `delta` / `done` / `error`）；每个会话同一时刻只处理一个请求（并发返回 409）。

| 分组 | 端点（代表） | 说明 |
| --- | --- | --- |
| 系统与连接 | `GET /api/health` `GET /api/bootstrap` `POST /api/models/fetch` `POST /api/models/test` `GET /api/lan-info` `GET /api/lan-qrcode.png` | 健康检查、前端静态配置、模型列表与连通性测试、局域网扫码 |
| 书库与准备任务 | `GET/POST /api/books…` `POST /api/books/{id}/preparation-jobs` `GET /api/preparation-jobs/{job_id}`(+`/events`) `POST /api/preparation-jobs/{job_id}/cancel|resume` | 书目与章节、全书准备任务的提交 / 进度（NDJSON 事件）/ 取消 / 恢复 |
| 原著阅读与搜索 | `GET /api/books/{id}/chapters/{n}`(+`/anchors`) `GET /api/books/{id}/search`(+`/occurrences`) `POST /api/books/{id}/locate`(+`/select`) | 章节正文、锚点、精确 / 模糊搜索与命中定位（不调用模型） |
| 阅读访谈与章首开局 | `GET /api/books/{id}/reader-chat/roster` `POST /api/books/{id}/reader-chat/threads` `POST /api/reader-chat/threads/{id}/messages` `POST /api/books/{id}/chapter-start` | 章末边界访谈线程；章首边界开局 |
| 角色库与设计器 | `GET/POST /api/character-library`(+`/{card_id}` `/export` `/import`) `GET /api/character-designer/schema`(+`/generate` `/save`) `GET /api/characters/pool…` | 数据库角色库 CRUD 与导入导出、设计器表单与生成 |
| 金手指 | `POST /api/golden-fingers/recommend|propose|confirm` `GET/POST /api/gf-designer…` | 推荐与确认状态机、规格设计器 |
| 会话主流程 | `POST /api/sessions/start` `POST /api/sessions/{id}/messages` `GET /api/sessions/{id}/state` | 开局与回合推进（NDJSON 流式） |
| 游戏内功能 | `/api/sessions/{id}/quests…` `/break-anchor…` `/autoplay-choice` `/ask` `/questions…` `/api/chat/roster|send` | 任务、碎锚、托管选线、规则问答、角色闲聊 |
| 存档与导出 | `POST /api/sessions/{id}/save|load` `GET /api/saves` `POST /api/saves/load` `POST /api/sessions/{id}/export-novel` | 存读档与会话导出小说 |
| 已玩作品库 | `GET /api/library/playable` | 已成功开局的作品列表 |
| 自动试玩 | `POST /api/playtest/start|stop` `GET /api/playtest/status|stream` | 无人值守质量回归（内部工具） |

## 6. 功能与边界（v3.0.0）

### 6.1 主要入口

| 功能 | 用户操作 | 必须注意 |
| --- | --- | --- |
| 活动角色库 | 查看、编辑数据库中的角色 | 新数据库为空是正常状态；旧 JSON / Markdown 不自动回退 |
| 角色设计器 | 编辑后统一保存到角色库 | 以服务端成功与修订号为准，不再有双保存流程 |
| 全书准备 | 上传 TXT 后提交全书准备任务 | 可能收费；只有 READY 就绪，不创建已玩状态 |
| 已玩作品库 | 查看成功开局的作品 | prepared 与 played 不同，阅读器书库也不同 |
| 阅读搜索 | 输入词句，精确或文本近似，点击命中 | 无模型调用；不是语义搜索，可能看到后文摘录 |
| 阅读访谈 | 当前章末选择人物对话 | 独立线程，不推进游戏；资料不足须先准备 |
| 从本章开始 | 章首边界预览后回配置确认 | 创建新会话，不覆盖旧局，不提前加载本章后续事实 |
| Agent 集群 | 显式选择集群生成策略 | 有限调用、重试、时间、并发和预算，不能绕过代码硬门禁 |

### 6.2 关键边界

- **数据库是活动角色的唯一权威来源。** 姓名不是可靠身份标识，编辑保留稳定 ID 与修订信息；模型推断、作者设定与原文证据相互区分，未知资料不伪装成已验证事实。
- **准备任务与游戏生成是两件事。** 准备描述原著资料范围，`simple` 与 `agent_cluster` 描述生成策略；仅蒸馏不代表已开局。
- **草稿 / 候选 / 正式提交是三种状态。** 失败或取消的候选不结算为回合；硬门禁检查不因模型意见放行；已发出的模型请求可能已产生费用，预算记录不等于服务商账单。
- **知识边界受代码约束。** 章末访谈不带入游戏分支状态，章首开局不预支本章后续事件。

### 6.3 不作以下承诺

- 不承诺预装旧角色、旧作品编号可直接开局或自动恢复私有资产。
- 不承诺任意 TXT 都可切章、任何模型都能通过质量门或文笔只升不降。
- 不把代码存在、版本一致、测试局部成功写成全链路通过。
- 不提供虚构截图；用户手册中的截图均来自合成演示数据环境并已标注。

## 7. 版本与许可

本文对应 v3.0.0（2026 年 9 月）。项目源码采用 [GNU AGPL-3.0](../LICENSE)（AGPL-3.0-or-later）；用户上传的原著文本、角色与生成内容归其所有者，不随源码许可授予第三方分发权利。
