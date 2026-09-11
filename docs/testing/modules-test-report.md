# 全功能模块覆盖测试报告（v3.0.0）

- 日期：2026-09-09（run 4 最终证据）
- 范围：后端全部对外功能模块（系统/模型接入/金手指/设计器/角色库/角色池/上传与书籍/准备任务/搜索/快速蒸馏/场景定位/阅读器访谈/游戏全流程/已玩作品库/游戏闲聊/存档导出/真实检验管线），共 18 个模块。
- 方法：隔离实例黑盒驱动（不触碰用户在用实例 127.0.0.1:21560），mock 模型服务按提示词标记路由并记录 JSONL 证据；仅合成小说世界（李青/白芷/北墙/旧册/铜扣暗记/茶棚/守军），不使用任何真实凭据。
- 驱动：`tools/verify_modules.py`（18 模块顺序执行，逐模块 JSON 证据 + 汇总 summary.json + mock 路由完整性校验）。
- 结论先行：**18 个模块中 16 个通过、2 个失败；两个失败均为已定位根因的产品缺陷（F6 已玩作品库标记永不写入、F7 内置真实检验管线与强化开局门禁不兼容），其余全部模块在 mock 模型下可正常使用。**

## 一、测试环境

| 组件 | 说明 |
| --- | --- |
| 应用 | `python run_app.py --port 21596 --var <临时目录>/var --no-browser`，另设 `FATE_API_PORT=21596`（playtest 自引用）与 `PLAYTEST_TXT=<合成小说fixture>` |
| 模型 | `tools/mock_model_server.py --port 21595`，按提示词标记路由（block_evidence、identity_pair、fullbook_rich_character、semantic_scene、reader_dialogue、director、quest_offer、export_*、character_chat 等 46 路由），全量调用记录 `mock-calls.jsonl` |
| 数据 | 全新 var 目录（books/saves/sessions/uploads 均为本轮新建），测试结束即弃 |
| 进程 | 仅终止自启动并记录 PID 的测试进程（本轮 app PID 17756、mock PID 33604，事后核对端口归属再 taskkill） |

## 二、结果汇总（run 4）

```
16 通过 / 2 失败 / 0 跳过；mock 路由齐全
```

| # | 模块 | 覆盖要点 | 结果 |
| --- | --- | --- | --- |
| 1 | system | health/bootstrap/lan-info/lan-qrcode（容错 501/503） | PASS |
| 2 | models | fetch 模型列表、test 连通（mock） | PASS |
| 3 | golden_finger | 推荐（nemesis_d 6.0）→ 提案 → 确认（服务端校验） | PASS |
| 4 | gf_designer | options/compose 上限校验/polish（凭据来源=polish）/specs 存取 | PASS |
| 5 | character_designer | schema/identity 七字段+corpus 三类+answers/generate→save | PASS |
| 6 | character_library | 建卡/详情/更新（revision++）/导出/导入覆盖/删除→404 | PASS |
| 7 | character_pool | 四栏槽位详情 | PASS |
| 8 | uploads_books | novel TXT + persona MD 上传、books 列表/详情/章节/404 | PASS |
| 9 | preparation_jobs | window 任务→READY→events；chapter-start；fullbook 任务→即时取消→CANCELLED→恢复→READY；prep 状态双模式 ready=true；章节锚点（活跃人物≥1） | PASS |
| 10 | book_search | occurrences exact（李青）+ fuzzy（铜扣暗记的刻痕）+ context 搜索（chunks） | PASS |
| 11 | quick_distill | work_distill 快速全书蒸馏（rules 缓存失效） | PASS |
| 12 | locate | 字面/语义定位、during 场景选择 | PASS |
| 13 | reader_chat | roster（第5章）/thread/send/同 request_id 幂等重放 | PASS |
| 14 | game_flow | 强化开局（book_id+preparation_job_id+白芷随行）→金手指确认→开局确认→ui-state→追问/批量作答→ask 普通/许愿/中继→任务接取→自动推进→破锚→蒸馏进度→存档/读档/会话载入→导出 | PASS |
| 15 | playable_library | 开局+存档后 `/api/library/playable` 应含本书 | **FAIL（F6）** |
| 16 | side_chat | roster（在场人物）+ chat/send（query 参数 session_id）+ 质量元数据 | PASS |
| 17 | save_export | 存档点导出小说（manifest+chapters） | PASS |
| 18 | playtest | 内置真实检验管线（SSE/轮询/终止） | **FAIL（F7）** |

mock 调用直方图（节选）：block_evidence×22、identity_pair×496、fullbook_rich_character×32、character_semantic_dims×32、semantic_scene×22、director×2、segment×6、options×4、post_polish×4、quality_judge×4、quest_offer×2、quest_verdict×2、export_plot/style/polish 各×2、character_chat×2、reader_dialogue×2、work_distill×1、gf_polish×1、designer_fusion×1。另有 6 次「质量修订卷」提示词未在 mock 路由表中（走通用回退应答，回合仍正常完成）——仅测试基建覆盖缺口，非产品问题。

## 三、发现的问题（产品侧，编号接矩阵报告 F1–F5）

### F6（高）已玩作品库标记永不写入，`/api/library/playable` 恒为空

- 现象：强化开局成功、存档成功后 `GET /api/library/playable` 返回 `[]`；书籍目录中不存在 `played_ready.json`。
- 归因证据链（run 4）：
  1. 失败同时刻 `GET /api/books/{id}/preparation?mode=fullbook` 返回 `ready=true`（驱动记录 `playable_attribution`），排除"准备未就绪"；
  2. 书籍目录仅有 anchors/book_index/chapter_index/chapters/opening_ready/preparation，**无 played_ready.json**；
  3. 存档 `modules-game-1.json` 的 `start_params` 键为 companions/mode/novel/work/… 等 23 个，**不含 `novel_file` 也不含 `book_id`**；
  4. 代码链：`core/app.py:2019-2032` 构造持久化 `start_params` 时从未写入 `novel_file`；`core/server.py:819-825` 却以 `start_params["novel_file"]` 推导书目录调用 `mark_book_played`——该键恒为空，**标记在任何开局路径下都不会写**；
  5. 次级问题：`core/server.py:826-827` 用 `except (OSError, ValueError): pass` 静默吞掉标记写入失败，即使将来键补上，失败也无任何日志。
- 影响：已玩作品库功能整体不可用（列表永远为空）。
- 修复方向：开局提交处改为使用本次请求已解析的 `book_dir`/`novel_path`（`server.py:2200/2265/2280` 已在作用域内）直接调用 `mark_book_played`，而非依赖持久化 start_params；同时将异常改为记日志而非 `pass`。`book_library_service.py` 本身逻辑正确（写入前复验、列表前复验+source_hash 比对）。
- **修复状态（run 5 验证通过）**：`_stream_response` 新增 `played_book_dir` 参数，start() 在作用域内解析书目录（book_id 开局用 `book_dir`；上传开局用 `books/<上传文件主干>`）传入；开局权威提交后投影写入 played 标记，失败记 uvicorn.error 日志不回滚开局。run 5 中 `playable_library` 模块 PASS，`/api/library/playable` 返回带归因的书目。

### F7（高）内置真实检验管线与强化开局门禁不兼容，playtest 永远无法开局

- 现象：`POST /api/playtest/start` 后 run 以 `error` 终止，`playtest_error="开局失败，终止检验"`，内部检查首项即 `{"detail":"请先完成当前原著的全书准备任务，再提交 book_id 与 preparation_job_id 开局"}`（HTTP 409）。
- 根因：`tools/playtest_kit/runner.py` 自行上传 TXT 后直接以 `mode="强化模式"+novel_upload_id` 调 `/api/sessions/start`（不创建准备任务、不带 book_id/preparation_job_id）；而 `core/server.py:2208-2211` 对强化模式硬性要求 book_id + READY 的 fullbook 准备任务（verified_cards、gap_report 完整）。两条约束互斥，**内置检验管线在当前门禁下永远 409**。
- 影响：「真实检验」功能不可用（矩阵测试期间以驱动侧自建准备任务绕开，故未暴露）。
- 修复方向（产品决策）：runner 在 start 前先创建并等待 fullbook 准备任务（复用 `wait_preparation` 逻辑），携带 job_id 开局；或为检验管线提供显式旁路。二者取一，不应放宽对真人用户的门禁。
- **修复状态（run 5 验证通过）**：取第一种方案——runner 上传 TXT（上传即切章、响应含 book_id）→ 创建 fullbook 准备任务（携带 idempotency_key）→ 轮询至 READY 才开局，`start_body` 携带 `book_id + preparation_job_id + session_id`（不再传 novel_upload_id，避免与服务端 422 互斥校验冲突）。run 5 中 `playtest` 模块 PASS：23+ 内部检查含「全书准备:任务创建 / 全书准备:READY」全过，真人门禁未做任何放宽。

### F8（低）`PLAYTEST_TXT` 未设置时 playtest 以裸 AttributeError 终止

- 现象（run 1 复现）：未设 `PLAYTEST_TXT` 时 `tools/playtest_kit/runner.py:90` `txt.read_bytes()` 抛 `AttributeError: 'NoneType' object has no attribute 'read_bytes'`，对外只表现为 run "error"，无人类可读原因（TXT_PATH 在 runner.py:21 默认 None）。
- 修复方向：`_resolve_txt` 对 None 给出中文错误（"未配置 PLAYTEST_TXT fixture"）。
- **修复状态（已修复）**：`_resolve_txt` 现抛 `RuntimeError("未配置检验用原著 TXT：请设置 PLAYTEST_TXT 环境变量，或在检验启动配置中提供 txt_path")`。

## 四、驱动侧修正记录（测试工具，非产品缺陷）

1. 准备任务响应 `job_id` 双形态（顶层或 `job` 包裹）统一提取，否则 fullbook_ready 永远不成立（run 1 → run 2）。
2. 搜索断言键与模式改为 `total_hits/hits` + `exact|fuzzy`；模块顺序调整为准备任务之后（context 搜索依赖 book_index.json）（run 1 → run 2）。
3. playtest 轮询终态集合补 `error`，避免 900s 挂起（run 1 → run 2）。
4. side_chat 目标改为 roster 首位在场角色：roster=active_members（场景在场者），开局后仅李青在场、白芷未入场景是 D11 语义的正确行为，非缺陷（run 2 → run 4）。
5. fullbook 取消→恢复路径：resume 为 202 异步重启，首个轮询可能仍读到残留 CANCELLED（run 3 竞态）；驱动补 30s 宽限期等待状态离开 CANCELLED，真实恢复失败仍会判败（run 3 → run 4）。

## 五、局限性

- mock 模型按提示词标记路由，验证的是**调用契约与状态机**，不评估生成内容质量；6 次「质量修订卷」调用走通用回退（见二节注）。
- 前端 UI 不在本次范围（另见 v3.0.0 U00–U08 计划）；矩阵测试报告已覆盖 2×2 生成模式组合。
- F6/F7 在 run 4 依据 observe-and-report 约定仅定位与归因；run 5 已按第八节完成修复并回归验证。
- **已知隔离缺口（每次运行后必须恢复资产）**：quick_distill 模块会把合成条目追加写入仓库 `assets/rules/work_library.md`（即使 `--var` 指向临时目录，规则缓存路径仍锚定仓库资产）。每轮含 quick_distill 的驱动运行后，需从发布包恢复该文件并复验 sha256（`921371c3…`）+ `tests.test_asset_gate`。run 5 后已恢复并复验通过。

## 六、证据索引（`docs/testing/evidence/modules/run4/`）

- `summary.json`：18 模块逐项 ok/failures。
- `<module>.json` ×18：断言、记录字段、失败明细（playable_library.json 含 `playable_attribution` 归因记录；playtest.json 含内部检查表与 `playtest_error`）。
- `driver-console.log`：控制台 PASS/FAIL/SKIP 全文。
- `mock-calls.jsonl`：全部模型调用（路由、时长、字符数、提示词预览）。
- `app.out` / `mock.out`：两侧服务日志。
- `playtest-novel.txt`：本轮合成小说 fixture（gen_playtest_fixture.py 产物，14785 字符）。

## 七、结论

全模块可用性良好：18 个模块中 16 个在隔离+mock 环境下完整走通，包括此前从未端到端验证过的阅读器访谈、游戏闲聊、存档导出、角色库导入导出与准备任务取消/恢复路径。需要优先处理的是 F6（已玩作品库标记永不写入——一处键名失配导致整功能不可用，修复面小）与 F7（内置真实检验管线被开局门禁挡死——需要产品决策 runner 侧补准备流程）；F8 为低优先级诊断质量改进。（三者在 run 5 已全部修复并回归，见第八节：18/18 通过。）

## 八、修复验证与档位放宽（run 5，2026-09-09）

### 8.1 修复内容（对应第三节 F6/F7/F8）

| 编号 | 修复 | 验证 |
|---|---|---|
| F6 | `_stream_response` 增加 `played_book_dir` 参数：开局用请求作用域内解析的书目录写 played 标记，失败记日志不回滚开局（`core/server.py`） | run 5 `playable_library` PASS，库列表返回归因书目 |
| F7 | playtest runner 先跑完准备再开局：上传→建 fullbook 准备任务（含 idempotency_key）→轮询 READY→`book_id+preparation_job_id+session_id` 开局（`tools/playtest_kit/runner.py`） | run 5 `playtest` PASS，强化开局硬门禁未放宽 |
| F8 | `PLAYTEST_TXT` 未设置时抛中文 RuntimeError | 手工验证，错误信息可读 |

F7 修复过程中追加发现并处理（均为测试基础设施，非产品缺陷）：

1. runner 开局未携带 `session_id`：book_id 流程下 start 会新建会话，而后续消息仍发往上传会话，收到 400「当前 session 尚未开始对局」。已在 start_body 携带上传会话的 session_id（服务端 `sessions.create` 对既有 id 幂等重挂）。
2. `decide()` 在空选项时 `options[0]` 抛裸 IndexError；现改为中文 RuntimeError（空选项本身即开局/投影异常，应显式暴露）。
3. mock 的 `_reply_export_rewrite` 按第一个空行切分回显草稿，而回合间正文恰以空行分隔——导出三遍改写会把开局回合整段截掉。改为优先按模板草稿标记（【跑团记录】/【待改写章节】/【待润色章节】）切分（`tools/mock_model_server.py`）。
4. 驱动 playtest 轮数 1→2：单回合下已提交正文仅约 500 字，导出长度断言（>500）无余量；两回合既让断言有真实余量，也多覆盖一轮决策路径。

### 8.2 普通模式剧情丰度上限放宽：简明（2 档）→ 丰厚（4 档）

按产品决策执行「放宽到丰厚，质量门禁保留必要的限制」：

- `core/engine/papers.py`：`BASIC_MODE_MAX_TIER = 4`；`available_tiers`/`validate_selection`/文案统一由常量派生；**第 5 档（鸿篇）仍建议类 agent、第 6 档（史诗）仍强制类 agent，未动**。
- `assets/papers/small_l3_*.json`、`large_l4_*.json`：`basic_mode: true`（资产加载校验 `basic_mode == (tier <= 4)` 通过）。
- `core/app.py`：旧丰富度就近映射的普通模式钳制 `min(paper_tier, BASIC_MODE_MAX_TIER)`；缺省回落档保持。
- `frontend/src/App.vue`：`DEFAULT_PAPER_TIERS` 3/4 档 `basic_ok: true`，钳制注释与提示文案同步（「基础模式仅可用 1–4 档（至『丰厚』）」）。
- `tests/test_papers.py`：门禁用例改为从 `BASIC_MODE_MAX_TIER` 派生（64 用例通过；连同 asset 门禁共 71 用例 OK）。
- bootstrap 实测下发：1 轻盈/2 简明/3 标准/4 丰厚 `basic_ok=true`；5 鸿篇/6 史诗 `basic_ok=false`。

### 8.3 回归结果（run 5 final）

隔离栈（mock 21595 + app 21596，独立临时 var 目录，合成世界 fixture）全量重跑：

```
18 通过 / 0 失败 / 0 跳过；mock 路由齐全
（system, models, golden_finger, gf_designer, character_designer, character_library,
 character_pool, uploads_books, preparation_jobs, book_search, quick_distill, locate,
 reader_chat, game_flow, playable_library, side_chat, save_export, playtest 全部 PASS）
```

run 5 后已从发布包恢复 `assets/rules/work_library.md`（sha256 `921371c3…` 与批准值一致）并复跑 `tests.test_asset_gate + tests.test_papers`（71 OK）。

### 8.4 证据索引（`docs/testing/evidence/modules/run5/`）

- `summary.json` + `<module>.json` ×18：run 5 final 全量断言与记录。
- `run5-final.log`：驱动控制台全文（18 PASS / mock 路由齐全）。
- `mock-calls-final.jsonl`：本轮全部模型调用（路由、时长、提示词预览）。
- `app.out` / `mock.out`：两侧服务日志；`playtest-novel.txt`：本轮合成小说 fixture。
- `fix-trail/playtest-retry4-export-length.json`：F7 修复中途的失败样本（导出正文 366 字 < 500，用于定位 mock 回显截断问题）。
- `fix-trail/playtest-retry5-pass.json`：mock 回显修复后同一模块通过样本。

---

## 九、Copilot AI 助手 + AI 配置迁移（run 6，2026-09）

用户需求：标题下导航行（工作台/我的书库/人物档案）新增 **AI 助手**（Copilot）与 **AI 配置**两个按钮；Copilot 一站式展示游戏状态、集成全部功能入口、提供用户文档检索，并可通过 AI 对话完成操作；左侧栏「模型与参数」完整配置迁入 AI 配置弹层。

### 9.1 实现要点

- **后端服务** `core/services/copilot_service.py`：14 个白名单工具（读：get_game_overview / list_saves / list_books / list_playable_library / search_docs / list_entries / open_entry；操作：save_game / load_save / create_preparation_job / autoplay_choice / quest_accept / quest_decline / export_novel——操作工具由端点层注入持锁闭包，服务层零 HTTP）；对话协议 = 模型输出一行 `{"tool","args"}` JSON → 服务端执行 → 以「【工具结果】」回填，自然语言即终答；单轮最多 6 步；`_scrub` 递归脱敏 API Key；快照只含公开字段。
- **端点** `core/server.py`：`GET /api/copilot/overview[?session_id=]`、`GET /api/copilot/docs?q=`、`POST /api/copilot/chat`（会话锁、凭据优先级 会话>请求体、缺 key 400、CopilotClientError→400 / CopilotUpstreamError→502）。
- **mock 路由** `tools/mock_model_server.py`：`copilot_chat` 特判在展平路由之前（其系统提示词含关键词，须用最后一条 user 消息做意图判定）；「【工具结果】」前缀直接给终答。
- **前端**：`CopilotPanel.vue`（右侧滑出：状态摘要、14 入口芯片、对话+工具徽标+「打开「xx」」按钮、手册搜索）；`ModelConfigModal.vue`（原左栏字段全部迁入弹层）；`App.vue` 导航行新增两按钮、`onCopilotEntry` 入口分发、左栏改为只读摘要+「打开 AI 配置」（开局按钮保留）；`npm run build`（vue-tsc + vite）通过。
- **驱动** `tools/verify_modules.py`：新增 `copilot` 模块（概览/带会话概览/文档检索/状态对话调用 get_game_overview/文档对话调用 search_docs/响应不含 API Key/无凭据 400）；`copilot_chat` 纳入 REQUIRED_MOCK_ROUTES。

### 9.2 测试结果

- **单测** `tests/test_copilot.py`（21 用例）：白名单封闭性、无会话操作显式「不可用」、参数不匹配可见报错、parse_tool_call 各形态、快照无凭据、`_scrub` 递归、手册检索命中/未命中、对话循环（未知工具→终答、步数上限兜底、历史仅 user/assistant 且仅 1 条系统提示、空历史 400）——21/21 OK；连同基线（asset 门禁+试卷）共 92 用例 OK。
- **隔离栈回归 run 6**（mock 21595 + app 21596，临时 var，合成 fixture）：**19 通过 / 0 失败 / 0 跳过；mock 路由齐全**（新增 copilot 模块 PASS；`copilot_chat` 命中 4 次）。证据：`docs/testing/evidence/modules/run6/`。
- **资产恢复复验**：run 6 后 `assets/rules/work_library.md` 已从发布包恢复（sha256 `921371c3…` 与批准值一致）。
- 说明：`python -m unittest discover`（644 收集）存在 2 个先于本次工作的 V00 隔离哨兵装载顺序伪差（字母序导入在 v00 前触发守卫；`test_v3_v00_reject_existing_application_import` 单跑时因 conftest 导入时机快照漂移），与 Copilot 改动无关（该路径不导入任何本次新增/修改模块）；全量正确口径以 pytest + V00 预载为准，记录基线口径（92 用例）全绿。

### 9.3 用户侧生效条件

档位 UI（普通模式可选 1–4 档）与本次两个新按钮均需：前端 dist 已由本次构建更新（`index-Dkdum5rU.js`）；用户重启 `python run_app.py`（旧进程在启动时加载了旧试卷/引导数据，且 serve 的是旧 dist 缓存）。
