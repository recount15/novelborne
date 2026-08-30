# 架构与代码地图（以代码为准，2026-08-30 重构中途快照）

> 本文描述**代码的实际行为**（逐函数测绘核实，非设计宣称）。重构进行中，
> 各阶段状态见文末"重构进度"。接手前先读《HANDBOOK.md》跑通环境。

## 1. 分层总图

```
run_app.py ──── 唯一进程入口：启动 FastAPI（core/server.py）并打开浏览器
    │
    ▼
core/server.py ──────────── API 层（54 路由）：参数校验 + HTTP 语义 + 会话锁
    │                        端点薄编排，业务在 app.py 与 services/
    ├── core/api/            契约层：NDJSON 流适配、public_state 脱敏
    │       contracts.py     on_start/on_send 的元组输出 → StreamEvent → NDJSON
    │       sessions.py      会话注册表（内存 dict + 锁）
    │
    ├── core/app.py ──────── 对局编排层：on_start（开局装配+开场生成）、
    │                        on_send（回合主流程）；yield 经 _out_start/_out_send
    │                        统一构造（元组形状唯一定义点）
    │
    ├── core/services/ ───── 服务层（重构 Phase 3 渐进沉淀）
    │       registries.py    DistillerRegistry + 角色池缓存（中立注册表，
    │                        断 app↔server↔engine 三方循环）
    │       ask_service.py   问答/作弊码状态机（ask 端点业务全量内聚）
    │       game_setup.py    开局装配纯函数（作品来源/宿敌/名册/阵营势差）
    │
    ├── core/state_schema.py 状态契约：TRANSACTIONAL_KEYS（回合事务键唯一来源）
    │                        + start_setting()（顶层键优先、start_params 兜底）
    │
    ├── core/engine/ ─────── 机制层（38 模块，单一职责）：涟漪/锚点/名册/金手指/
    │       惰性 __init__     蒸馏/宿敌/任务/碎锚/压缩…（__getattr__ 按需加载，
    │                        PyInstaller 构建须 collect-submodules 全量收编）
    │
    ├── core/fate_engine.py 引擎门面：provider 配置/客户端/思考参数/上传/
    │                        作品库/存档路径（805 行 13 职责，Phase 4 拆分目标）
    │
    ├── core/ui/ ─────────── 展示辅助（Gradio 遗留 + 共用纯函数）
    │       common.py        桥段库/世界书/会话日志（资源路径指向 assets/）
    │
    ├── core/memory/  结构化状态记忆（blank_state/StateStore/patch/commit）
    ├── core/lore/    动态世界书（LoreInjector/load_entries）
    └── core/prompts/ 提示词加载器

frontend/ ── Vue 3 + TS + Tailwind 单页应用（src/App.vue 为单体，
             Phase 5 拆 composables）；构建产物 dist/ 由后端同端口托管

assets/ ──── 全部静态数据：rules/（作品库+运行时规则）、data/（角色卡/
             桥段库×5/技能/金手指/samples 样书）、prompts/、lore/
var/ ─────── 全部运行数据（自动创建，git 忽略）：db/books/saves/sessions/
             uploads/logs/outputs/
```

## 2. 一次对局的生命周期（数据流）

1. **开局** `POST /api/sessions/start` → server 薄编排 → `app.on_start`：
   装配（game_setup 纯函数）→ 穿越身份落定（模型调用，system prompt 构建前
   完成无名成员写回）→ 强化模式剧情摘要（阻塞）→ 开场白流式生成。
   产出 stream 事件（NDJSON，每行 `{"type":"state","data":{...}}`，
   `delta` 为增量文本块，客户端累加）。
2. **回合** `POST /api/sessions/{id}/messages` → `app.on_send`：
   两步确认状态机 → 事务快照（TRANSACTIONAL_KEYS 33 键深拷贝）→ llm_msg
   装配（记忆/世界书/涟漪/桥段/锚点约束）→ 流式生成 → 类 Agent 质检循环 →
   机械门禁（体量/点名/锚点三校验，失败自动重写一次再软放行，仍败则
   **回滚事务快照**并提示玩家换行动）→ 回合管线结算（收束/任务/碎锚/宿敌/压缩）→ 存档。
3. **状态**：state 是 45+ 键的 dict（见 state_schema.py 事务键）。
   对客户端经 `api/contracts.py public_state()` 脱敏（剔除 system/api_key/
   nemesis_private 等，**合成** relay_active/wish_remaining 等前端直读键——
   契约红线，重构不得破坏）。

## 3. 依赖规则（重构后的硬约束）

- 分层单向：server → app/services → engine → （不得反向）
- engine 不得 import app/server（registries.py 为中立交汇点）
- 资源路径统一走 `fe.BASE_DIR`（assets 布局，源码/PyInstaller 一致）
- on_start/on_send 对外只 yield `_out_start`/`_out_send` 构造的元组
- **已知例外**：server.py 的 /api/playtest/* 端点惰性 import
  `tools/playtest_kit`（API 层 → tools 包）；Phase 4 拟迁入 core/services
- `engine/runtime_mechanics.py` 为兼容转发层（re-export faction/participation/
  ripple/roster/tropes 旧名）；app/fate_engine/ui.common/game_setup 仍走旧路径，
  Phase 4 改直连后删除

## 4. 重构进度（六阶段，每阶段过回归闸门后提交）

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 死代码清除（6 处 + 2 死模块） | ✅ |
| 1 | yield 元组 → 唯一构造器（35 处） | ✅ |
| 2 | state 事务键注册表 + 双读收敛 | ✅ |
| 3a | 全局注册表中立化（三方循环断根） | ✅ |
| 3b | ask 端点服务化（219→37 行） | ✅ |
| 3c | on_start 装配段 → game_setup | ✅ |
| 3d/e | 穿越落定段 / on_send 回合管线拆分 | ⏳ |
| 4 | fate_engine 拆分 + engine 门面收编 + paths 统一 | ⏳ |
| 5 | 前端 composables（删 TS 复刻公式） | ⏳ |

详细蓝图：仓库外 `var/refactor/DESIGN.md` 与四份测绘报告（map_app/
map_server/map_engine/map_periph.md）。

## 5. 模块清单（逐文件实际职责，2026-08-30 逐行核实）

> 「上游」指经 grep 核实的 import/调用方；改动某文件前先看它的上游。

### 根入口 / core 顶层

| 文件 | 实际职责 | 上游 |
|---|---|---|
| run_app.py | 唯一进程入口：解析 --port/--var/--host/--no-browser，先写 FATE_* 环境变量再导入 core.server，启动 uvicorn + 局域网二维码 + 开浏览器 | 用户 CLI；FateEngine.spec |
| core/server.py | FastAPI 路由层：Pydantic 校验、会话锁、on_start/on_send 元组→NDJSON 流适配、frontend/dist 静态托管、playtest 监控端点 | run_app.py |
| core/app.py | 对局编排：on_start（开局装配+开场流式）、on_send（两步确认→事务快照→llm_msg 装配→类Agent质检→机械门禁→回合结算→存档）；后半仍有 Gradio Blocks 遗留 | server、tools/run_strengthened_playtest |
| core/fate_engine.py | 模型接入门面：provider 配置/OpenAI 兼容客户端/思考参数、作品库（W 条目）解析、personas 扫描、上传读取、BASE_DIR/WRITABLE_DIR | app、server、services、ui、engine/gf_designer |
| core/state_schema.py | TRANSACTIONAL_KEYS（回合事务键唯一来源）+ start_setting() 双读收敛 | app |

### core/api/ 与 core/services/

| 文件 | 实际职责 | 上游 |
|---|---|---|
| api/contracts.py | StreamEvent 事件契约、public_state 脱敏与前端直读键合成（契约红线）、元组→事件适配 | server、api/sessions |
| api/sessions.py | ApiSession/SessionManager：会话生命周期、每会话上传目录、内存凭据与锁 | server |
| services/registries.py | DistillerRegistry + 角色池缓存中立收编（断 app/server/engine 循环） | app、server、ask_service、engine/character_library |
| services/ask_service.py | ask 端点业务全量：规则问答装配脱敏、作弊码三愿/永久通路状态机、领域异常 | server |
| services/game_setup.py | 开局装配纯函数：作品来源解析/宿敌人格/名册装配/阵营势差 | app |

### core/engine/（38 模块）

| 文件 | 实际职责 | 上游 |
|---|---|---|
| __init__.py | 门面：_LAZY_EXPORTS + __getattr__ 惰性加载 | app、server、fate_engine、ui、services、tools |
| agent_mode.py | 类Agent 自检提示词装配/问题清单解析/修订指令 | app |
| anchor_distiller.py | 后台单章锚点蒸馏线程：九字段解析/引文对齐/校验落盘 | app、tools |
| autoplay.py | 托管子智能体：性格驱动选线（单回合、不改状态） | server |
| break_anchor.py | 全局碎锚：积势进度与发起门禁、多阶段任务、成功降锚（不扣积势）/失败冷却 | app、server、ask_service |
| budget.py | 章节回合预算转发口（实现在 chapter_tools） | engine 门面 |
| catalog.py | 伙伴/女主预设目录 schema、去重、assets/data 加载 | server、character_db/library、name_collision、scripts |
| chapter_tools.py | 本地切章与索引（编码回退、标题模式库），含 CLI | server、ui/common、budget、tools |
| character_creation_protocol.py | 角色创建规程/自检的代码化 | character_designer、scripts/protocol_recheck |
| character_db.py | SQLite 角色库：建库/CRUD/批量/按栏位查询 | catalog、character_library、scripts |
| character_designer.py | 角色设计器：身份校验、语料分类、融合生成与规整 | server、work_distiller |
| character_library.py | 角色卡文件库 CRUD（builtin/user/overrides 遮蔽），与 SQLite 双写 | server |
| cheat_code.py | 作弊码双码体系：三愿、永久通路、愿望脱敏 | ask_service、server、app |
| context_compressor.py | 每 10 回合历史压缩装配与保真校验 | app |
| distill.py | 内部子调用统一通道 distill_model（120s 持锁/300s 后台超时） | server、app、ask_service |
| elastic_gate.py | 弹性门限：两稿不过后代码层修复（剥非叙事块+合成补足选项）零模型放行 | app |
| dynamic_convergence.py | 动态收束力：连续位置+有界漂移+滞回换挡 | app |
| faction.py | 阵营势差与宿敌难度非线性计算 | runtime_mechanics 经由 |
| gender_guard.py | 穿越保障：附身对照表 prompt/解析、叙事约束块 | app |
| gf_designer.py | 金手指设计器：规格拼接、质量门、润色、本地规格库 | server |
| golden_finger.py | 金手指推荐与确认状态机、GF(D)=D^1.15 | engine 门面、gf_designer |
| ledger.py | 强化模式进度账本原子读写 | app、tools |
| name_collision.py | 同名监测与世界观测名改名 | app |
| nemesis_agent.py | 宿敌自主决策：分级信息视野、失真摘要 | app |
| novel_exporter.py | 会话→小说三遍导出流水线 | server |
| opening_flow.py | 开局四阶段门禁状态机 | app |
| options.py | A–F 选项解析/剥离/渲染 | app、nemesis_agent |
| participation.py | 人物参与度、场景字数预算、交互约束块 | server、runtime_mechanics、tools |
| persistence.py | 存读档（脱敏+原子替换）、sessions/ 工作记录 | app、server、ask_service、tools |
| plot_summary.py | 两步蒸馏第一步：剧情大概 prompt | app、work_distiller、tools |
| quest.py | 任务机制：offer/解析、时限奖励公式、状态机 | server、app |
| ripple.py | 涟漪 L0–L4、积势、相容性 K | faction、quest、nemesis_agent、ui/common |
| roster.py | 伙伴/女主/宿敌提示块装配 | runtime_mechanics、tools |
| roster_relevance.py | 选角与剧情相关度评分 | app |
| roster_schema.py | 名册配置规范化/校验/序列化 | app、tools |
| runtime_mechanics.py | 兼容转发层（无实现，Phase 4 删除） | app、fate_engine、ui/common、game_setup |
| skill_drift.py | 性格 6 维倾向 EMA 结算与提示块 | app、server |
| textkit.py | 共用分词/切分（解 ripple/tropes 互导） | ripple、tropes |
| tropes.py | 桥段库：九风格分类、倒排检索、模板渲染 | runtime_mechanics、roster |
| work_distiller.py | 上传作品快速蒸馏入库（work_library.md + 角色卡） | app、server |

### core/lore、memory、prompts、ui

| 文件 | 实际职责 | 上游 |
|---|---|---|
| lore/schema.py + matcher.py + injector.py | 世界书条目 dataclass/关键词匹配（冷却窗口）/按优先级预算注入 | app、ui/common 经 lore 包 |
| memory/schema.py | 结构化状态 schema 与 blank_state | state_store、validator |
| memory/extractor.py | 从行动/回复正则抽取状态变更提案 | app 经 memory 包 |
| memory/state_store.py | 快照合并、diff、历史与面板渲染 | app 经 memory 包 |
| memory/state_validator.py | 快照/patch 确定性校验 | state_store |
| prompts/__init__.py | 提示词加载器：缓存读取 assets/prompts、@@KEY@@ 渲染 | fate_engine、work_distiller |
| ui/common.py | 会话日志、token 统计、切章/章节文本、涟漪评分、桥段与世界书注入器 | app、game_setup |
| ui/status_bar.py | 进度条/Token/蒸馏灯纯文本 HTML | app |
| ui/profiles_panel.py | config.json 持久化、.env、提供商默认值 | app、golden_finger_panel |
| ui/golden_finger_panel.py | Gradio 金手指面板回调（遗留） | app |
| ui/roster_form.py | 技能预设、女主池、数量归一化 | app、game_setup |
| ui/theme.py | 设计 token 与 Gradio CSS（遗留） | app |

### frontend/src/

| 文件 | 实际职责 | 上游 |
|---|---|---|
| main.ts | Vue 入口：挂载 App、全局样式与霞鹜文楷字体 | index.html |
| App.vue | 单体主界面：开局向导/名册选角/NDJSON 对局流/存读档/六主题/作弊码（Phase 5 拆 composables） | main.ts |
| api.ts | REST 与 NDJSON 流封装 | App.vue、两组件 |
| types.ts | public_state/bootstrap 契约 TS 类型 | api.ts、App.vue |
| themeSwitch.ts | 五主题元数据与 applyTheme/currentTheme 持久化 | App.vue |
| components/NovelExportModal.vue | 导出小说弹窗 | App.vue |
| components/SiameseCat.vue | 暹罗猫装饰 SVG | App.vue |
| views/CharacterDesigner.vue | 角色设计器向导：身份→语料→选择题→融合→入库 | App.vue |
| assets/main.css + themes/*.css | 全局样式与 --fe-* 语义变量六主题（classic 在 main.css，其余 5 套独立文件） | 全部组件 |

### tools/ 与 scripts/（非运行时）

| 位置 | 实际职责 |
|---|---|
| tools/playtest_kit/ | 实机检验工作包：pipeline（SSE 后台线程）、runner（全流程）、standalone（HTTP 黑盒）、agent_pilot（类Agent对比）、run_tests（一键入口）；server 的 /api/playtest/* 惰性引用 pipeline/runner |
| tools/run_strengthened_playtest.py | 强化模式端到端试玩（临时 var 隔离，直驱 app） |
| tools/validate_characters.py | 角色卡 JSON 规格校验 CLI |
| tools/其余（build_final_roster 等 5 个） | 旧 data/ 布局时代一次性扩库工序，路径已失效（待归档） |
| scripts/（6 个） | 一次性数据迁移与集群修正（角色卡 SQLite 化时代产物） |
| build/ | build_windows.bat（产物在根 dist\）+ FateEngine.spec（collect-submodules 收编惰性 engine） |
