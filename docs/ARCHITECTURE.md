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
    │       game_setup.py    开局装配纯函数（名册/作品/宿敌/阵营）〔Phase 3c 落地中〕
    │
    ├── core/state_schema.py 状态契约：TRANSACTIONAL_KEYS（回合事务键唯一来源）
    │                        + start_setting()（顶层键优先、start_params 兜底）
    │
    ├── core/engine/ ─────── 机制层（39 模块，单一职责）：涟漪/锚点/名册/金手指/
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
             桥段库×5/技能/金手指）、prompts/、lore/、samples/
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

## 4. 重构进度（六阶段，每阶段过回归闸门后提交）

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 死代码清除（6 处 + 2 死模块） | ✅ |
| 1 | yield 元组 → 唯一构造器（35 处） | ✅ |
| 2 | state 事务键注册表 + 双读收敛 | ✅ |
| 3a | 全局注册表中立化（三方循环断根） | ✅ |
| 3b | ask 端点服务化（219→37 行） | ✅ |
| 3c | on_start 装配段 → game_setup | 🔄 |
| 3d/e | 穿越落定段 / on_send 回合管线拆分 | ⏳ |
| 4 | fate_engine 拆分 + engine 门面收编 + paths 统一 | ⏳ |
| 5 | 前端 composables（删 TS 复刻公式） | ⏳ |

详细蓝图：仓库外 `var/refactor/DESIGN.md` 与四份测绘报告（map_app/
map_server/map_engine/map_periph.md）。
