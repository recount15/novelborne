# 2×2 生成模式矩阵实测报告（基础/强化 × 正常/类Agent）

- 日期：2026-09-09
- 版本：Novelborne v3.0.0 开发仓（`Novelborne-state-refactor`）
- 范围：模拟真实使用流程（上传合成原著 → 准备 → 开局 → 两步确认 → 多回合 → 存档/读档），观察四种生成路径的实际工作状况
- 数据：全部为本地合成小说（10 章 / 14,785 字）与占位凭据；未使用任何真实作品、真实 API Key 或用户运行数据

## 一、测试计划

### 1. 矩阵设计

| 配置 | 模式 | 生成方式 | 试卷档位 | 准备方式 | 场景选择 |
|---|---|---|---|---|---|
| basic | 基础模式 | 正常生成（LEGACY 单卷流式） | 2 | 开局时窗口蒸馏（无需预建任务） | 不携带 |
| basic_agent | 基础模式 | 类Agent 生成（技能簇 DAG） | 2 | window 准备任务（agent 开局硬性要求） | locator 选景（during，截点 offset>0） |
| enhanced | 强化模式 | 正常生成（试卷管线） | 3 | fullbook 全书准备任务 | 不携带 |
| enhanced_agent | 强化模式 | 类Agent 生成（技能簇 DAG） | 6 | fullbook 全书准备任务 | locator 选景（during，截点 offset>0） |

驱动脚本按产品路径逐配置执行：上传 → （如需）准备任务轮询至 READY → 开局（携带/不携带 scene_selection）→ 强化模式两步确认（确认金手指、确认开局）→ 发送 2 个回合（选择 A）→ 存档 → 读档。断言包括：每回合后 round 严格递增、save_stage=committed、选项恰为 A–F、助手消息不以 ⚠️ 开头、正文 ≥120 字、存读档成功。

### 2. 测试基础设施（全部本地、可复现）

- **mock 模型服务** `tools/mock_model_server.py`：OpenAI 兼容（非流式 + SSE 流式），按提示词特征路由到 35 类应答（锚点/身份对/富角色卡/导演卷/段卷/选项卷/裁判卷/润色卷/类Agent 技能簇 worker 等），逐调用写 JSONL 留证。模型"智能"为脚本化的契约合格输出，不含任何生成质量语义。
- **矩阵驱动** `tools/verify_mode_matrix.py`：`python tools/verify_mode_matrix.py --configs basic,basic_agent,enhanced,enhanced_agent --rounds 2 --mock-log <jsonl> --out <dir>`。
- **隔离应用实例**：独立端口 + `FATE_VAR_DIR` 独立数据目录（不触碰用户在用 var/），进程 PID 全程记录，测试后仅终止自己启动的进程。
- **诊断工具**：`tools/repro_agent_cluster.py`（离线复现技能簇回合，暴露被 ClusterError 吞掉的内层异常）、`tools/probe_basic_agent_start.py`、`tools/probe_agent_turn.py`（live 事件流探针）。

## 二、结果汇总

四配置全部 PASS（详见 `docs/testing/evidence/`）：

| 配置 | 结果 | 耗时 | 模型调用总数 | 终局回合 | 存档/读档 |
|---|---|---|---|---|---|
| basic | PASS | 0.4s | 6 | 2 | OK |
| basic_agent | PASS | 1.0s | 30 | 2 | OK |
| enhanced | PASS | 18.5s | 624 | 3 | OK |
| enhanced_agent | PASS | 11.2s | 612 | 3 | OK |

各配置模型调用直方图（按 mock 日志切片统计）：

- **basic**：traverse_map 1、opening_check 1、legacy_turn 2、options 2 —— 全程 LEGACY 单卷路径；开局首调为「开局核对」混合回复（核对清单 + 正文 + A–F），随后每回合一次单卷生成 + 一次选项生成。
- **basic_agent**：cluster_skill 30 —— 开局 + 2 回合共 3 次技能簇 DAG（evidence/continuity/motivation 三并发首波 → plan → scene_draft → polish → continuity_critic → options.candidates → options.critic → final_commit_arbiter），不经过任何试卷卷。
- **enhanced**：全书准备 582（block_evidence 22、identity_pair 496、fullbook_rich_character 32、character_semantic_dims 32）+ traverse_map 1 + 试卷管线（director 3、segment 9、options 7、post_polish 6、quality_judge 6、segment_refill 2）+ legacy_turn 2 + unmatched 6。
- **enhanced_agent**：全书准备 582 + cluster_skill 30 —— 准备阶段与 enhanced 完全一致，回合期完全切换为技能簇 DAG，不出现任何试卷卷调用。

回合计数说明：基础模式首幕由 start 流产出（开局后 round=0，2 回合后 round=2）；强化模式「确认开局」本身消耗第 1 回合生成第一幕（开局后 round=1，2 回合后 round=3）。两模式行为均符合 `core/app.py` 回合事务设计。

## 三、发现的问题（按严重度）

### F1（高）不选场景 + 类Agent：每一回合都失败回滚
- 现象：`story_agent_mode` 开启但开局未携带 scene_selection 时，`knowledge_cutoff` 永远不会写入 state；`build_turn_snapshot`（`core/services/generation_skills.py:317-322`）只对 round==0 兜底 `{"chapter_no":1,"offset":0}`，round≥1 直接 `authorized_cutoff_required`。
- 后果：`确认开局` 后的第一幕（round=1）即失败，之后每回合同样失败；用户只看到「⚠️ 调用模型服务失败：authorized_cutoff_required」+ 回合回滚（round 不前进、选项为空）。
- 前端事实：`App.vue` 仅在「阅读器从本章开始」或「证据定位选过候选」时才发送 scene_selection，勾选类Agent 并不强制场景选择（App.vue:1679-1692、2454 附近无联动校验）。
- 复现：`python tools/verify_mode_matrix.py --configs enhanced_agent --rounds 1`（去掉驱动里的 scene_selection 注入即可）。

### F2（高）阅读器「从本章开始」+ 类Agent：开局成功、第一回合失败
- 现象：chapter-start 路径产出的 scene_selection 截点为 `{chapter_no:N, offset:0}`（`reader_start_service.py:53`）；round≥1 时截点前引文为空串，`build_turn_snapshot` 判 `authorized_evidence_required`（`generation_skills.py:354`）。
- 后果：与 F1 同型的每回合回滚；而 locator 选景（during/after，offset>0）一切正常——本报告最终跑通路径即 locator。
- 边界说明：offset=0 语义是「本章开始之前零证据」，引擎拒绝空证据开局后续回合是安全设计，但「从本章开始」是 UI 一等入口，二者组合对用户不可用。

### F3（中）基础模式 + 类Agent 的准备依赖未在 UI 呈现
- 现象：`selected_strategy=="agent_cluster"` 的开局在 `build_turn_snapshot` 里要求已校验的书籍索引/准备，否则 `preparation_required`（`app.py:2303-2309` 有专门提示文案）。
- 实测：基础模式直接开 agent 局会被拒绝；先跑 window 准备任务（READY）后正常。驱动按此路径跑通（prep 0.6s）。

### F4（低）类Agent 失败时可观测性差
- 现象：`AgentClusterService.generate` 把所有非 Gate 异常吞为 `cluster_adapter_failed`，`_run_agent_cluster` 再包成 `ClusterError(code)`；app 层回滚后仅显示「模型服务调用失败：{code}」，无日志留痕。本次排查真实原因（F1/F2/mock 路由缺口）需要离线 monkeypatch 复现才能拿到内层 traceback。
- 建议：worker/cluster 失败时把内层异常写入运行日志或 `agent_meta`。

### F5（过程）驱动断言假阳性（已修复，测试工具问题）
- 早期驱动只断言 save_stage/options，而回合回滚恰好恢复到 committed 快照、选项沿用开局残留，导致失败回合被误判通过。已改为 round 严格递增 + ⚠️ 消息检测 + 正文长度断言。

## 四、局限性

1. mock 的回复是脚本化的契约合格输出（固定叙事文本、固定选项、裁判恒通过），**不能评估生成质量、文风、剧情连贯性与裁判卷真实把关能力**；`quality_judge` 恒 88 分意味着强化管线的质量门未被真正压力测试。
2. 身份消歧（identity_pair 496 次）由 mock 按同名规则回答，真实模型可能给出不一致裁决并触发冲突路径，该分支未被覆盖。
3. 回合数仅 2、单本书 10 章；跨章推进（章节预算/换章/碎锚）未覆盖。
4. enhanced 配置观测到 unmatched 6 与 legacy_turn 2 次辅助调用（回合仍全部通过门禁），未逐一归因，不影响结论但值得后续标注。

## 五、结论

- 四种组合的生成路径选择、准备依赖、开局门禁、回合事务、存读档在产品路径上均按设计工作；调用直方图清楚呈现三条生成路径的形态差异（LEGACY 单卷 / 试卷管线 / 技能簇 DAG）。
- 类Agent 生成的可用性当前依赖两个隐含前置：**先完成准备**、**开局必须携带截点 offset>0 的场景选择**；其中 F1/F2 是用户可直接踩到的缺陷，建议在 UI 强制场景选择（agent 模式下）或为无场景/零截点开局补默认 cutoff 推进策略后再复测。
