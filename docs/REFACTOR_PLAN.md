# Novelborne 回合生成大重构 · 终版整合计划

> 当前实现状态（本轮收口）：M0/M1/M2/M3/M4/M5 主体均已落地；强化模式默认 `compose_mode=true`，普通模式仍走 legacy。新增全局“答卷整合润色”中台阶段：它统一处理锚点、角色、金手指、涟漪、任务、碎锚、宿敌、铁律、记忆与悬念，不设质量门，只做流程化后处理；失败安全回退初步组装稿。全量 Python 测试、强化 FakeClient 双模式试玩与前端构建均通过。后续真实模型验收重点是段卷内容质量与高档试卷体验，而非整回合格式门禁。

> 2026-08-31 定稿。本文件是唯一权威计划，供并行开发对齐；与早期讨论冲突处以本文件为准。
> 工作区：`C:\Novelborne\Novelborne-2.0.0-clean-source`。分层规则遵守 `standards/01-architecture.md`：新模块全部落 `core/engine/`，禁止 engine→app 反向依赖。

## 0. 八条核心原则

1. **试卷化生成**：回合 = 出卷 → 并行答卷 → 代码批改 → 定向重填 → 代码组装 → 一次去痕润色。AI 只做"按给定要求填空"，不做"带着一堆要求写整篇作文"。
2. **门禁粒度下沉（红线）**：质量检测全部放在**填的空**上（逐空批改 + 错题定向重填）；**整回合只保留格式性检验**（正文无 JSON/代码围栏/系统标记/隐藏段残留等）。整篇级机械门禁（体量/交互/锚点关键词）退役。
3. **API 并发**：全进程动态控制器，硬上限 10，AIMD 自适应，**达不到目标并发绝不报错**（超额排队，降速不降级）；优先级 开局蒸馏 > 回合波次 > 后台蒸馏。
4. **双族六档试卷**：1–3 档小卷族（槽位相同、槽内丰度不同），4–6 档大卷族（同族同理）。小卷族**必须包含世界运转全部必要槽位**；普通模式只能用 1–2 档；6 档**必须开启类 agent 生成**（代码硬校验）。
5. **开局蒸馏重中之重**：高速度、高质量、允许高开销——并行发射、两遍法、长章 map-reduce、开局零阻塞、角色卡过质量门后入库。
6. **角色/锚点/任务/碎锚一等公民**：角色卷（在场角色 beat contract）、角色状态 patch（补关系写路径）、锚点词表入卷、任务推进槽与碎锚阶段槽强制参与剧情生成。
7. **双作弊码（三愿/永久通路）结构化铁律账本**：登记结构化（fact_norm/scope/affected/conflicts）、注入按相关性选择（未命中不注入）、冲突取代。
8. **强化模式主推**；普通模式轻量（单次正文 + 1–2 档小卷 + 选项/LOG 结构化红利）。
9. **接口统一在中台**：`core/engine/` 只放纯机制（模型注入 callable、可离线测试、不读用户配置、不做 IO）；全部新管线的**编排门面统一放 `core/services/`**（解析 client/model、组合调度 engine 机制、回写 state），`core/app.py` 与 `core/server.py` 对新能力只 import services 门面，不直接摸 engine 新模块。依赖方向严格遵守 `standards/01-architecture.md`（`server → app/services → engine → assets` 单向；engine→fate_engine 仅 distill.py 一处惰性例外，不再新增）。

## 1. 回合流水线（强化模式，目标态）

```
Wave 0  纯代码预计算：机制上下文（世界书/传闻/涟漪K/活跃角色/桥段/预算/
        GF冷却/任务截点/碎锚阶段/性格倾向/铁律相关性命中）→ 选卷
Wave A  出卷（三卷并行，structured_call，错误清单回传重试≤2→确定性兜底）
        导演卷：beat/goal/conflict、anchor_plan(词取自锚点库)、分段事件互斥
        分配、ripple_resolution、world_beats、任务钩子、碎锚钩子、铁律落地槽、
        cliffhanger、log_draft
        角色卷：每位在场角色 beat contract（目标/互动/禁忌(角色卡)/关系走向）
        选项卷：6 种子（4金手指+2性格；active 任务/碎锚可注入剧情向种子）
Wave B  答卷（并行）：段卷×N（每段只带本段合约，事件互斥，按序上屏）
        ∥ 选项成稿卷 ∥（逢十）存档评价卷
批改    空级批改+错题重填（类agent核心）：见 §3；语义自检问题清单并入润色卷
Wave C  并行：润色（流式主展示；必含词/人名/因果句逐字保留+总字数窗口；
        润色后重过【格式性检验】，不过回退组装稿）
        ∥ 任务判定 ∥ 碎锚判定 ∥ 宿敌回合 ∥ 角色状态patch卷
Wave D  纯代码：LOG 代码渲染写日志、记忆/漂移/收束结算、锚点入账、
        history（只存最终展示文本）、压缩（消费存档）、落盘
        事务快照/回滚骨架不变
```

- 前端零改动：NDJSON 非前缀时发全文 delta（现有协议已支持整段替换）。
- agent 开关控制预算而非架构：开=空级重填≤2+语义自检+润色必开；关=一次填空+代码兜底+轻润色。

## 2. 试卷规格（双族六档）

| 档 | 族 | 目标字数 | 段数 | 可用性 |
|---|---|---|---|---|
| 1 轻盈 | 小卷 | ~400 | 1 | 普通模式可用 |
| 2 简明 | 小卷 | ~650 | 2 | 普通模式可用（最高档） |
| 3 标准（默认） | 小卷 | ~950 | 2–3 | 仅强化 |
| 4 丰厚 | 大卷 | ~1350 | 3 | 仅强化 |
| 5 鸿篇 | 大卷 | ~1850 | 4 | 仅强化，建议开类agent |
| 6 史诗 | 大卷 | ~2400 | 5 | 仅强化，**必须开类agent（硬校验）** |

- **槽位类型注册表**：`anchor_setup/anchor_climax/anchor_free`（锚点槽三态）、`character_interaction`（在场角色互动槽）、`golden_finger`（金手指紧凑）、`ripple_cost`（涟漪代价）、`quest_progress`（任务推进）、`break_anchor_stage`（碎锚阶段）、`directive_landing`（铁律落地）、`cliffhanger`（悬念钩子）；大卷族新增 `gf_deep`（金手指深描）、`ripple_echo`（涟漪回响扩槽）、`world_reaction`（世界反应）、`subplot`（支线/多线并进）。
- 小卷族任何档位都包含全部小卷槽位（无 active 任务/碎锚/铁律命中时对应槽在运行时缺省，但卷面模板里必须存在）；大卷族=小卷槽位扩容+四个新增槽。
- 卷面阶段：`setup`（铺垫卷，`chapter_round < turn_budget`）、`climax`（收束卷，尾声）、`free`（自由卷，全局碎锚/relay 激活后，锚点槽降级为参考）。
- 存储：`assets/papers/*.json`（声明式），`core/engine/papers.py` 加载+结构校验+档位门禁 API；旧 300–1000 丰富度按数值就近映射；前端滑杆改 6 档选择器（普通模式只显示 1–2 档）。
- 段级字数容差 ±20%；回目总字数由构造保证（段窗口求和）。

## 3. 门禁与批改（粒度红线落地）

**空级批改**（每空产出中文错误清单，供定向重填）：
- 段空：字数窗口 / must_include 逐词子串 / must_mention 点名+交互 marker / forbidden 雷区词。
- 锚点空：锚点词命中 + 动作/结果/因果 marker + 否定窗（复用 `participation.validate_anchor_convergence` 的段级化）。
- 选项空：恰 6 条、因素分布（4 金手指+2 性格）、去重、无来源标注、preview 存在。
- 角色空：名字 ∈ roster、evidence 必须为正文子串。
- 任务/碎锚空：requirement 关键词命中（+ Wave C 判定双确认，本地规则优先）。

**整体格式性检验**（唯一保留的整回合门禁）：正文无代码围栏、无单行 JSON 残留（≥80 字符）、无【系统…】标记、无 `<<<LOG>>>/<<<ARCHIVE>>>` 残留、选项块不混入正文。复用 `elastic_gate.strip_non_narrative_blocks` 的**检测**逻辑（只检不修）。

**重填预算**：每空 ≤2 次，错误清单附回提示词；全部仍失败→确定性兜底（段：含必含词的最小合成段；选项：渲染种子；蓝图：机械蓝图）。`scene_gate` 语义改为"格式检验通过"（assembled 模式下基本恒真），playtest 断言不变。

## 4. 开局蒸馏与角色入库流水线（M1，`core/engine/opening_distill.py`）

章切分完成即并行发射（开局优先级给满）：
1. plot 采样卷×3（首/中/末）∥ 角色抽取卷（多章样本直取）∥ 第 1 章锚点卷（两遍法）∥ 第 2–3 章锚点卷 ∥ 长章切块卷（>3500 字：~3000 字/块并行→合并→统一 validate）。
2. plot 合并卷 → 结构化 `{genre, premise, major_threads, tone}` → roster_relevance 精化（纯代码）。
3. 作品档案卷（等 plot 合并）：13 字段校验（anchors 1–6 非空、genre 必填），同名 upsert `work_library.md`（原子写，复用 `work_distiller`）。
4. 角色卡入库强化：上限 12 张、优先 主角/反派/女主；role/position 枚举、slot_keys 白名单；relationship_vector 引用名 ∈ 同批角色∪锚点 characters（防编造）；质量门 `character_designer.quality_assessment`（playable 以上入库，flat 重填一次仍不过丢弃）；入库 `character_library.save_card` 双写，同名同作品覆盖更新；单卡失败跳过。
5. 开局确认零阻塞：只等第 1 章锚点 validated（两遍法）；极端失败 `rescue_now` 摘录兜底放行（`origin=fallback`，后台 force 精化覆盖）。
6. 对局开始后蒸馏降为后台低优先级；`anchor_missing` 回合阻断彻底移除。

## 5. 并发控制器（M0，`core/engine/parallel.py`，已落地）

硬上限 10；`api_concurrency` 默认 6（环境变量 `FATE_API_CONCURRENCY`）；AIMD（3 次成功+1；限流 ÷2 至 1 + 15s 冷却恢复）；优先级 `PRIORITY_OPENING=0 / TURN=10 / BACKGROUND=20`；`slot()`/`priority_scope()`/`budget_model()`/`run_parallel()`；排队等待绝不因并发不足报错。

## 6. 结构化基础件（M0，`core/engine/structured.py`，已落地）

统一 `extract_json`（收敛 7 处重复）、声明式 `FieldSpec`+`validate`（中文错误清单）、`spec_prompt`、`structured_call`（错误清单回传重试≤2；校验始终不过返回 None 走兜底；传输层连续异常才上抛）。

## 7. 蒸馏池化（M0，`anchor_distiller.py`，已落地）

worker 池（`FATE_DISTILL_WORKERS` 默认 3，上限 4）；失败退避改延迟堆**不阻塞队列**；章级互斥（distill_now/worker/force 精化不双写）；`rescue_now`（模型一卷→摘录兜底，`origin=fallback`+force 精化重入队）。

## 8. 机制参与映射（强化模式）

| 机制 | 进入 | 消费 |
|---|---|---|
| 三愿/增补铁律 | Wave0 相关性选择→导演卷铁律落地槽 | 账本审计；LOG 世界字段 |
| 涟漪/相容K | Wave0→导演卷 ripple_resolution | Wave D 回填账目 |
| 金手指 | Wave0 冷却/代价→导演卷+4 种子 | LOG 代码渲染 |
| 任务系统 | Wave0 目标/requirement/截点→任务推进槽+剧情向种子 | Wave C 判定并行；奖励代码发放 |
| 碎锚任务 | Wave0 active 阶段 requirement→碎锚阶段槽+种子 | Wave C 本地规则优先判定 |
| 宿敌 | Wave C 自主回合 | 传闻喂下回合、LOG 宿敌字段、每5回合摘要代码渲染 |
| 性格漂移 | Wave0 prompt_block→角色卷 | Wave D tick 不变 |
| 世界书/传闻/桥段 | Wave0→导演卷素材（与铁律命中合并 world_beats） | LOG 世界字段 |
| 记忆提取器/压缩 | — | Wave D 不变 |

## 9. 双作弊码铁律账本（M5，`core/engine/directives.py`）

登记 `structured_call` 输出 `{fact_norm, scope, affected[], conflicts[]}`（scope≠机制、affected∈roster/世界书词表，重试≤2；三愿原子扣费不变）；注入按 `affected ∩ (锚点词/在场角色/地点)` 命中选择（未命中不注入，解决 relay 无限累积）；affected 重叠标记 superseded（代码+一次仲裁）；与锚点冲突不改门禁（铁律低于机制，由落地槽消化）；账本写 `ledger.cheat.directives`；relay 激活即碎锚→自由卷+停蒸馏池；前端契约不变。

## 10. 模块与文件规划

| 模块 | 层 | 职责 | 状态 |
|---|---|---|---|
| `core/engine/parallel.py` | 机制 | 动态并发控制器 | ✅ M0 |
| `core/engine/structured.py` | 机制 | 统一 JSON+FieldSpec+structured_call | ✅ M0 |
| `core/engine/anchor_distiller.py` | 机制 | worker 池/非阻塞退避/rescue_now | ✅ M0 |
| `core/engine/papers.py` + `assets/papers/` | 机制 | 试卷库+档位门禁 | 并行流水线 D |
| `core/engine/turn_grader.py` | 机制 | 空级批改+整体格式检验 | 并行流水线 E |
| `core/engine/turn_composer.py` | 机制 | 组装渲染（正文/选项块/LOG/展示分离） | 并行流水线 E |
| `core/engine/opening_distill.py` + 新提示词 | 机制 | 开局蒸馏+角色入库流水线（模型注入） | 并行流水线 B |
| `core/services/opening_service.py` | **中台门面** | 开局流水线对外接口：解析 client/model、优先级包装、state 回写 | 并行流水线 B |
| `core/engine/turn_blueprint.py` | 机制 | 蓝图合并校验+机械兜底 | M3 |
| `core/services/turn_pipeline.py` | **中台门面** | 回合管线对外接口（Wave 0–D 编排，on_send 只调它） | M3 |
| `core/engine/directives.py` + `core/services/directives_service.py` | 机制+门面 | 铁律账本 | M5 |
| `core/app.py` | 编排 | on_start/on_send 状态机（改为调 services 门面） | 主线串行（唯一热文件） |

**并行纪律**：`core/app.py`、`core/server.py`、`core/engine/__init__.py`（新模块名已由主线预登记）、既有模块只允许主线串行修改；各并行流水线只新建自己的文件与测试，完成后由主线接线。

## 11. 里程碑

- **M0** 并发控制器+structured+蒸馏池化+anchor_missing 消除+单测（主线，进行中）。
- **M1** 开局蒸馏+角色入库流水线（并行 B）。
- **M2** 选项/LOG 出正文（主线，app.py 串行）。
- **M3** 试卷管线 MVP（导演三卷∥→段卷∥→组装→润色）+任务/碎锚槽位；flag `compose_mode`，先做 3/4 档铺垫/收束卷；playtest 双跑。
- **M4** 类 agent 批改-重填+角色状态 patch+双族六档全量+档位门禁。
- **M5** 铁律账本+普通模式轻量接入+宿敌摘要代码渲染+开局叙事/导出同模式化+删死代码（digit 选项语法、OPTION_REPAIR_MESSAGE、extract_progress、整篇重写软放行阶梯、elastic_repair 大部）。

## 12. 验收

- `tools/playtest_kit/run_tests.py unit` 全绿；强化试玩（FakeClient）全绿。
- playtest runner 断言保持：scene_gate、options≥6、convergence 漂移 ≤0.051、public_clean、导出无 artifacts、三愿状态机、档位门禁、开局蒸馏时延/覆盖率/入库角色质量分。
- `agent_meta` 观测：逐空重填率、兜底率、各波时延、蒸馏吞吐、铁律命中数、并发自适应事件。
