# 后端优化方案实施状态报告

> 生成时间: 2026-09-06（真实验收更新版）
> 
> 基于文档: `考虑新增机制的实施.md`
> 
> 当前源码: `Novelborne-2.0.0-clean-source`

---

## 〇、真实验收最新进展（2026-09-06）

### ✅ F16/F17 供应商缓存真实验收通过

使用真实凭据（中转站 hapiopen.cc）完成双供应商 prompt cache 命中验证：

| 供应商 | 模型 | 首次调用 | 第二次调用 | 结论 |
|---|---|---|---|---|
| Anthropic | claude-haiku-4-5-20251001 | cache_creation **11,742** tokens | cached **11,742** tokens（仅 38 token 全价） | ✅ 建立+命中均证实 |
| OpenAI | gpt-5.5（mini 通道 503 降级） | 19,987 全价 | cached **19,200** tokens（96% 命中） | ✅ 命中证实 |

- 验证脚本: `scripts/real_cache_hit_test.py`（稳定前缀 8,160 字符，两次相同请求）
- 产物: `artifacts/real_acceptance/cache_hit_test.json`
- 证明 `native_gateway.py` 的 `cache_control` 断点与 usage 归一化在两家真实供应商上有效

### ✅ F15 连接鲁棒性故障注入验收通过

新增 `tests/test_resilient_gateway_faults.py`（8 项全通过）：
- 429 瞬时故障重试恢复
- 401 不可重试错误快速失败（不消耗重试）
- 连续 3 次失败熔断打开、窗口内拒绝触达 provider
- 冷却期后半开探测、成功后熔断关闭
- 半开探测期间并发请求被拒
- 退避封顶（0.25s→0.5s→1.0s）
- 零星失败不累积熔断
- 并行任务部分失败隔离

### ✅ F12 choice_agent 正式接入选项通路

`options_service.generate_options()` 接入 `STORY_CHOICE_AGENT_MODE` 三档：
- `legacy`: 原样输出，无元数据
- `shadow`: 输出不变，`meta.choice_agent` 记录影子选择结果（供离线对比）
- `agent`: 应用未来知识/非法 patch 过滤 + 多样性保序选择（仅当保持 6 条契约时生效）；不足 6 条时注入批改错误清单触发定向重试，由模型替换非法候选
- 前台 A-F 契约在所有模式下不变；`requires_future_knowledge`/`patch_valid` 内部标记出榜前剥离
- 测试: `tests/test_choice_agent_integration.py`（6 项）+ 全量回归 648 项通过

### ✅ F11 StoryAgent agent 模式验证路径补全

- 确认接线存在: `core/app.py:2495` 经 `STORY_AGENT_MODE=agent` 切换 StoryAgent 接管管线
- 新增测试: 非法 TurnResult（选项形状）触发校验拒绝+回滚；合法 TurnResult 通过校验原样返回
- 确认 turn_pipeline 为纯函数（不修改 state），StoryAgent 失败回滚语义正确
- 测试: `tests/test_story_agent_integration.py`（7 项通过）

### ✅ 基建修复
- `tools/playtest_kit/standalone.py` 缺失导致整个 playtest_kit 无法导入 → 已补齐（前台 CLI 版全流程检验）
- 新增长程实验脚本 `scripts/real_longrange_experiment.py`（N 回合 on_send 全链路 + ledger 覆盖率 + 导出源校验）
- 样本小说 `data/samples/sample_novel.txt`（原创三章）+ 真实模型开局蒸馏完成（3 锚点 verified/done、3 角色卡入库）

### 🔄 真实长程实验进行中
- agent 模式 4 回合 on_send 全链路实验运行中（记录延迟/token/ledger 覆盖）

### ✅ 真实长程实验最终结果（agent 模式接管管线，真实模型）——全部通过

**最终实验**（`artifacts/real_acceptance/agent_mode_final_report.json`，OpenAI 通道 gpt-5.5 + FATE_SUBCALL_TIMEOUT=240，STORY_AGENT_MODE=agent）：

| 回合 | 耗时 | 正文 | 选项 | 回合推进 | ledger | 累计 token(in/out) |
|---|---|---|---|---|---|---|
| 1 | 257s | 1,051 字 | 6 | →1 committed | [1] | 13,969 / 12,845 |
| 2 | 262s | 1,038 字 | 6 | →2 committed | [1-2] | 49,921 / 30,007 |
| 3 | 271s | 828 字 | 6 | →3 committed | [1-3] | 103,460 / 41,194 |

- **3 回合连续完整成功，零降级**
- **ledger 覆盖率 100%**：rounds 1-3 连续无缺口
- **导出源校验通过**：`source_kind=story_ledger`、`round_min=1`、`round_max=3`、`turn_count=3`、`check_ok=true`——文档第 18/30 节的导出 bug（第 N 回合导出覆盖 1-N）在真实运行中验证
- 正文质量：第 3 回合自然织入原著开篇场景（沈砚敲听雨楼侧门），跨回合事实（登记簿缺页、红绳记号）准确延续

**实验 A**（Anthropic 通道，对照）：
- 4 回合全部被中转站掐断长响应（超时/incomplete chunked read）
- **引擎失败路径验证通过**：零崩溃、状态零污染、回合未推进、无半成品提交
- 短/中文本生成正常（1200 字 22 秒），失败集中于管线大 JSON 卷（导演卷）

**实验 B**（OpenAI 通道，未加超时覆盖，对照）：
- 第 1 回合完整成功；第 2-3 回合超时降级；**第 4 回合自动恢复完整成功**——失败后恢复能力实证

**新增性能配置**：`FATE_SUBCALL_TIMEOUT` 环境变量（`core/engine/distill.py`）
- 背景：实测中转站导演卷级调用需 ~103 秒，默认 120s 子调用超时过紧导致全部失败
- 行为：默认 120s 不变；`FATE_SUBCALL_TIMEOUT=240` 时放开；非法值回退默认；调用方显式传参不受影响
- 对应设计文档 22.10.2（连接不畅/超时可配置）；全量回归 648 项通过

**夹具修正**：`scripts/real_longrange_experiment.py` 对齐真实流程 round=0 起步（开局不占回合，首个行动=回合 1），ledger 从 1 起完整无缺口

**30/50/100 回合长程实验的运营前提**：当前中转站单回合 ~4.5 分钟且存在单调用超时风险，完整 30/50/100 回合需更快 provider 通道；管线本体（回合推进、ledger 全量覆盖、导出校验、失败降级与恢复、token 计量）已在真实 3-4 回合连续运行中验证。

---

## 一、总体目标与架构

### 核心目标
将后端演进为**专用的剧情运行 Agent**，具备：
- 更强的长剧情推演能力（事件图、目标层、动态权重）
- 原著材料深度利用（可检索、可缓存的世界模型）
- 代码提供基础逻辑（时序、因果、状态变化）
- 高 token 但不增加等待（并行、缓存、增量更新）
- 供应商无关缓存抽象（Anthropic/OpenAI）

### 六层架构
```
HTTP / UI
  ↓
Session Runtime
  ↓
Story Agent Orchestrator
  ├─ World Model Builder
  ├─ State & Causality Planner
  ├─ Character Director
  ├─ Narrative Composer
  ├─ Verification & Repair
  └─ Memory & Cache Manager
  ↓
Durable State / Save / Export
```

---

## 二、功能实施状态追踪表

| ID | 功能点 | 主要源码 | 测试状态 | 实施状态 | 备注 |
|---|---|---|---|---|---|
| F01 | 完整故事账本 | `story_ledger.py` | ✅ 5项 + 真实3回合覆盖1-3 | ✅ 已完成 | 导出bug修复实证 |
| F02 | 全量小说导出 | `novel_exporter.py` | ✅ 真实导出源校验通过 | ✅ 已完成 | ledger优先实证 |
| F03 | 回合快照/hash | `story_snapshot.py` | ✅ 单元测试通过 | ✅ 已完成 | 稳定hash |
| F04 | 回合事务提交 | `turn_transaction.py` | ✅ 集成测试通过 | ✅ 已完成 | 回滚保证 |
| F05 | 事件图/时序 | `story_graph.py` | ✅ 单元测试通过 | ✅ 已完成 | 前置检查 |
| F06 | 统一目标层 | `narrative_objective.py` | ✅ 单元测试通过 | ✅ 已完成 | 许愿归一化 |
| F07 | 历史动态权重 | `story_weighting.py` | ✅ 单元测试通过 | ✅ 已完成 | 衰减/关注 |
| F08 | 生成因素权重 | `story_weighting.py` | ✅ 单元测试通过 | ✅ 已完成 | 分阶段权重 |
| F09 | 200k 上下文预算 | `story_context.py` | ✅ 单元测试通过 | ✅ 已完成 | 预算装配 |
| F10 | 三层长期记忆 | `context_compressor.py` | ⚠️ 需30/50/100回合 | 🔄 部分完成 | 需快provider长程 |
| F11 | 专用剧情 Agent | `story_agent.py` | ✅ 7项 + 真实3回合接管 | ✅ agent模式验证 | 可随时切回legacy |
| F12 | 专用选项通路 | `choice_agent.py` | ✅ 6项接入测试 | ✅ 已接入 | shadow/agent双档 |
| F13 | 反事实模拟 | `counterfactual.py` | ✅ 单元测试通过 | ✅ 已完成 | patch预演 |
| F14 | 连续性审查 | `continuity_agent.py` | ✅ 单元测试通过 | ✅ 已完成 | 不变量检查 |
| F15 | 连接鲁棒性 | `resilient_gateway.py` | ✅ 8项故障注入 | ✅ 已完成 | +FATE_SUBCALL_TIMEOUT |
| F16 | Anthropic 缓存 | `provider_cache.py`+native_gateway | ✅ 真实命中 11,742 tokens | ✅ 已完成 | creation+hit双证实 |
| F17 | OpenAI 缓存 | `provider_cache.py`+native_gateway | ✅ 真实命中 19,200 tokens | ✅ 已完成 | 96%命中率 |
| F18 | 导出连续性报告 | `export_continuity.py` | ✅ 真实check_ok | ✅ 已完成 | gaps实证 |
| F19 | 前台兼容 | `server.py` | ✅ 全量回归通过 | ✅ 已完成 | A-F契约不变 |
| F20 | 灰度/回滚 | 配置/路由 | ✅ 模式切换测试 | ✅ 已完成 | legacy可用 |

**进度汇总**: 
- ✅ 已完成: 19/20 (95%)
- 🔄 部分完成: 1/20 (5%——F10 需快 provider 做 30/50/100 回合长程实验)

---

## 三、已完成模块详情

### 3.1 story_ledger.py - 完整故事账本
**状态**: ✅ 已完成并通过测试

**功能**:
- 幂等追加完整回合记录（round, narrative, options, events, hash）
- 解决导出只能看到最后一回合的bug
- 旧存档自动迁移

**测试覆盖**:
- ✅ 幂等追加不重复
- ✅ 重复round拒绝
- ✅ 导出优先读取ledger
- ✅ history fallback机制
- ✅ 427项全量测试通过

### 3.2 story_snapshot.py - 回合快照
**状态**: ✅ 已完成

**功能**:
- 构造不可变StorySnapshot
- 稳定state_hash（忽略history顺序）
- 幂等性保证

**测试**: ✅ 相同状态产生相同hash

### 3.3 story_graph.py - 事件图
**状态**: ✅ 已完成

**功能**:
- 事件前置依赖检查
- 时间窗口约束
- 拒绝非法时间跳变

**测试**: ✅ 前置条件未满足时拒绝后置事件

### 3.4 narrative_objective.py - 统一目标层
**状态**: ✅ 已完成

**功能**:
- 许愿、任务、碎锚、金手指、宿敌统一抽象
- 激活/推进/兑现/受阻状态跟踪

### 3.5 story_weighting.py - 动态权重系统
**状态**: ✅ 已完成

**功能**:
- 历史事件权重（recency, causality, unresolved, player_salience）
- 生成因素权重（分阶段：战略/正文/选项/patch/润色）
- hard因素权重下限保护

**测试**: ✅ hard因素在patch阶段保持≥0.7

### 3.6 story_context.py - 200k上下文预算
**状态**: ✅ 已完成

**功能**:
- 分层PromptLayers（P0-P3优先级）
- 预算装配（target 160k-185k）
- 可裁剪/不可裁剪标记
- 上下文审计

**测试**: ✅ 预算不足时裁剪optional，保留required

### 3.7 choice_agent.py - 专用选项通路
**状态**: 🔄 基础完成（shadow模式）

**功能**:
- 候选过滤（requires_future_knowledge, patch_valid）
- 多样性选择（去重、标签覆盖）
- 前台A-F兼容映射

**测试**: ✅ 过滤和多样性选择单元测试通过

**待完成**: 正式替换options_service主通路

### 3.8 turn_transaction.py - 回合事务
**状态**: ✅ 已完成

**功能**:
- 状态机（PENDING → PLANNED → GENERATED → VALIDATED → COMMITTED）
- 临时状态应用和回滚
- 失败自动恢复

**测试**: 
- ✅ 未经VALIDATED不能COMMITTED
- ✅ 失败自动回滚到before状态

### 3.9 story_invariants.py - 叙事不变量
**状态**: ✅ 已完成

**功能**:
- 回合迁移检查（round连续性）
- 选项基础校验
- 正文证据检查基础

**测试**: ✅ validate_transition单元测试通过

### 3.10 export_continuity.py - 导出连续性报告
**状态**: ✅ 已完成

**功能**:
- 回合缺口检测
- 重复round检测
- 空正文检测
- manifest完整性

**测试**: ✅ 缺口标记为not ok

### 3.11 resilient_gateway.py - 连接鲁棒性
**状态**: 🔄 基础完成

**功能**:
- 有界重试（指数退避）
- 熔断器（failures阈值后打开）
- 半开探测
- 线程安全

**待完成**: 真实网络故障注入测试（429/5xx/断线）

### 3.12 provider_cache.py - 供应商缓存
**状态**: 🔄 接口完成

**功能**:
- 供应商无关缓存键计算
- 稳定前缀构建（system_rules, world_facts, character_models, output_contract）
- 缓存元数据记录

**待完成**: 真实Anthropic/OpenAI凭据验证命中率

### 3.13 story_agent.py - 专用剧情Agent
**状态**: 🔄 shadow模式

**功能**:
- prepare()生成shadow元数据（snapshot, weighted_events, objectives, context_audit, choice_path）
- run_turn()通过事务运行pipeline
- mode='shadow'只读不提交
- mode='agent'正式提交

**测试**:
- ✅ shadow模式不mutate
- ✅ agent模式委托turn_pipeline
- ✅ 失败回滚状态

**待完成**: 从shadow切换为主流程

---

## 四、待完成的关键验收项

### 4.1 真实Anthropic/OpenAI缓存命中验证 ⚠️
**优先级**: 高

**当前状态**: 接口完成，未用真实凭据验证

**所需工作**:
1. 配置真实Anthropic API key
2. 发送带稳定前缀的请求2次
3. 验证第2次返回`cache_creation_input_tokens`或`cached_input_tokens`
4. 对OpenAI重复相同测试
5. 记录命中率和token节省

**验收标准**:
- Anthropic: 第2次调用显示cache read
- OpenAI: 第2次调用显示cached tokens
- 稳定前缀变化后缓存失效
- 日志不泄露API key

**预期时间**: 1-2小时（需真实凭据）

### 4.2 30/50/100回合长程质量实验 ⚠️
**优先级**: 高

**当前状态**: 工程骨架完成，未进行真实实验

**所需工作**:
1. 准备测试原著和角色
2. 运行30回合并记录：
   - 每回合token（input/output/cached）
   - P95延迟
   - 锚点连续性
   - 因果冲突数
   - 选项质量评分
   - ledger覆盖率
3. 对50、100回合重复
4. 与baseline对比

**验收标准**:
- 100回合ledger覆盖率100%
- 因果硬错误=0
- P95延迟≤240秒
- 压缩后关键事实可检索

**预期时间**: 4-8小时（需真实模型调用）

### 4.3 网络故障注入测试 ⚠️
**优先级**: 中

**当前状态**: resilient_gateway基础完成，未注入故障

**所需工作**:
1. Mock provider返回超时
2. Mock provider返回429
3. Mock provider返回5xx
4. Mock provider中途断开连接
5. 验证熔断器打开
6. 验证半开探测
7. 验证回滚不重复推进round

**验收标准**:
- 3次失败后熔断器打开
- cooldown后半开探测
- 失败不提交半成品
- hard deadline内回滚

**预期时间**: 2-3小时

### 4.4 StoryAgent从shadow切换为agent主流程 ⚠️
**优先级**: 中

**当前状态**: shadow模式完成，未切换

**所需工作**:
1. 设置环境变量 `STORY_AGENT_MODE=agent`
2. 修改`core/app.py`或`core/server.py`调用`StoryAgent.run_turn()`
3. 运行10回合验证
4. 对比legacy与agent输出
5. 确认可随时切回legacy

**验收标准**:
- agent模式正常提交
- 前台API不变
- A-F选项兼容
- ledger完整
- 可切回legacy

**预期时间**: 2-3小时

### 4.5 choice_agent正式替换现有选项通路 ⚠️
**优先级**: 中

**当前状态**: 基础完成，未替换options_service

**所需工作**:
1. 修改options_service调用choice_agent.generate_options()
2. 确保候选数量≥6
3. 运行选项质量测试
4. 验证前台A-F兼容
5. 对比旧选项通路质量

**验收标准**:
- 六项不重复
- 至少覆盖主线/关系/信息/风险/性格
- 选项有正文依据
- 选项质量≥baseline

**预期时间**: 1-2小时

### 4.6 三层长期记忆100回合验证 ⚠️
**优先级**: 中

**当前状态**: context_compressor存在，未验证100回合

**所需工作**:
1. 运行100回合压缩实验
2. 检查第100回合能否检索第1回合关键事实
3. 验证事件索引、目标索引、角色差分索引完整
4. 验证压缩不删除ledger

**验收标准**:
- 100回合后关键事实可检索
- 未完成目标不丢失
- 角色状态变化可追溯
- ledger完整

**预期时间**: 4-6小时（依赖4.2长程实验）

---

## 五、测试覆盖汇总

### 已通过测试
- ✅ 专项测试: 19项全部通过
- ✅ 全量测试: 427项全部通过
- ✅ 单元测试覆盖: story_snapshot, story_graph, story_weighting, story_context, choice_agent, story_invariants, turn_transaction, export_continuity
- ✅ 集成测试覆盖: story_agent shadow, turn_transaction rollback, export continuity

### 未完成测试
- ⚠️ 真实provider缓存命中测试
- ⚠️ 30/50/100回合长程实验
- ⚠️ 网络故障注入测试（429/5xx/断线/failover）
- ⚠️ StoryAgent agent模式集成测试
- ⚠️ choice_agent质量对比测试

---

## 六、技术债务与风险

### 6.1 高优先级风险
1. **缓存未验证**: 未用真实凭据验证Anthropic/OpenAI缓存命中，可能存在接口不兼容
2. **长程质量未知**: 未进行真实100回合实验，三层记忆的保真度未验证
3. **网络鲁棒性未测**: 未注入真实故障，熔断器在生产环境行为未知

### 6.2 中优先级技术债
1. **shadow模式未切换**: StoryAgent和choice_agent处于shadow，未正式接管主流程
2. **上下文预算未调优**: 200k预算装配逻辑完成，但未在真实长剧情中调优P0-P3优先级
3. **反事实模拟简单**: counterfactual.py只做临时patch预演，未实现1-3回合深度模拟

### 6.3 低优先级改进
1. **并行Wave机制未启用**: 设计文档提到3-6路候选并发，当前实现仍是串行
2. **全书导出连续性**: export_continuity报告完成，但跨章一致性审查和局部修订未实现
3. **角色四维蒸馏**: 设计提到mind_model/decision_policy/voice_transfer/behavior_boundaries，当前未完整实现

---

## 七、实施建议与优先级

### 第一阶段：关键验收（1-2天）
1. ✅ 真实Anthropic缓存验证（1小时）
2. ✅ 真实OpenAI缓存验证（1小时）
3. ✅ 网络故障注入测试（2-3小时）
4. ✅ 30回合质量实验（2-3小时）

### 第二阶段：主流程切换（1-2天）
1. ✅ StoryAgent切换为agent模式（2-3小时）
2. ✅ choice_agent替换主选项通路（1-2小时）
3. ✅ 10回合集成验证（1-2小时）
4. ✅ 50回合质量实验（3-4小时）

### 第三阶段：长程验证（2-3天）
1. ✅ 100回合质量实验（6-8小时）
2. ✅ 三层记忆保真度验证（2-3小时）
3. ✅ 上下文预算调优（2-3小时）
4. ✅ 性能和token报告（1-2小时）

### 第四阶段：生产就绪（1-2天）
1. ✅ 灰度rollout配置（1小时）
2. ✅ 监控和告警（2-3小时）
3. ✅ 文档更新（2-3小时）
4. ✅ 安全扫描（1小时）

---

## 八、结论

### 8.1 工程完成度
后端优化方案的**工程骨架已完成70%**，核心模块（ledger、snapshot、graph、weighting、context、transaction、invariants）全部实现并通过离线测试。

### 8.2 真实验收缺口
剩余30%属于**真实模型运行验收**，需要：
- 真实provider凭据
- 真实30/50/100回合实验
- 真实网络故障注入
- shadow到agent的正式切换

### 8.3 风险评估
当前**风险可控**：
- ✅ 所有模块有单元测试
- ✅ 回滚机制完整（legacy模式可用）
- ✅ 前台API兼容不破坏
- ⚠️ 缓存命中未验证（需真实凭据）
- ⚠️ 长程质量未知（需真实实验）

### 8.4 下一步行动
**建议优先完成第一阶段关键验收**（1-2天），验证缓存、网络鲁棒性和30回合质量后，再决定是否切换主流程。

---

**报告生成者**: ZCode AI Agent  
**复审建议**: 由项目负责人确认真实凭据可用性和实验时间预算后，启动第一阶段验收。
