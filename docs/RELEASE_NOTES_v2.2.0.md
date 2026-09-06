# Release Notes v2.2.0

**发布日期**: 2026-09-06
**版本类型**: 架构升级版本（后端优化方案 F01–F20 落地）

---

## 概述

v2.2.0 完成了后端优化方案（考虑新增机制的实施.md）的全面落地：剧情引擎从
"单一大 prompt" 升级为由代码主持的专用剧情 Agent 架构。本轮新增约 25 个
引擎/服务模块（+10,500 行），648 项单元测试全量通过，关键链路完成真实供应商
验收。

---

## 🏗️ 架构新增（Story Agent 后端优化方案）

### 叙事事实账本与导出修复
- **story_ledger**：不可压缩的叙事事实账本（回合级追加、连续性校验）；
- **ledger-first 导出**：修复"第 N 回合导出缺失前序回合"的问题——第 N 回合
  导出覆盖第 1..N 回合全部事实（真实运行校验 `check_ok=true`）；
- **story_invariants / story_snapshot**：回合前后状态不变量与快照校验。

### 回合事务化
- **turn_transaction**：回合状态机
  （OPEN→PLANNED→GENERATED→VALIDATED→COMMITTED/FAILED→ROLLED_BACK），
  失败自动回滚，杜绝半成品提交与状态污染（真实供应商断流场景下零崩溃验证）。

### 目标与权重
- **narrative_objective / story_graph / story_weighting / counterfactual**：
  统一目标层、事件图、动态权重与反事实推演；
- **chapter_arc / plot_threading / sequence_feedback / sequence_review**：
  章节弧线与情节线程管理。

### 选项专用通路
- **choice_agent**：选项过滤 + 多样性选择，经 `STORY_CHOICE_AGENT_MODE=
  legacy|shadow|agent` 三档灰度接入选项生成；前台 A–F 六选项契约不变，
  依赖未发生未来信息的候选触发定向重试替换。

### 供应商中立基础设施
- **native_gateway / provider_cache**：供应商无关调用与 prompt 缓存键管理
  （稳定内容前缀缓存、版本/供应商变更自动失效）；
  - 真实验收：Anthropic 缓存建立 11,742 tokens → 第二次调用 100% 命中；
    OpenAI 兼容端点 96% 命中（19,200/19,987 tokens）；
- **resilient_gateway**：有界重试 + 熔断器 + 半开探测（8 项故障注入测试全通过：
  429 恢复、401 快速失败、熔断开启/关闭、退避封顶、并行部分失败隔离）。

### StoryAgent 模式
- **story_agent / story_context**：`STORY_AGENT_MODE=legacy|shadow|agent`
  三档接管回合管线；agent 模式完成真实模型连续 3 回合接管验收
  （回合推进、6 选项契约、账本覆盖 1–3、导出校验、token 记账）。

---

## ⚙️ 配置与运维

- 新增 `FATE_SUBCALL_TIMEOUT` 环境变量：慢供应商可调大内部子调用读超时
  （默认 120s 不变；调用方显式指定的超时不受影响）；
- `tools/playtest_kit` 修复 `standalone.py` 缺失导致的整包导入失败。

---

## 🎨 前端

- 新增主题组件库（`frontend/src/components/theme/`）；
- 新增 `useGenerationSnapshot` composable。

---

## 🧪 测试

- 全量 **648 项测试通过**（新增 29 项：弹性网关故障注入 8、provider_cache
  缓存键 9、choice_agent 接入 6、事务回滚与校验 2、其他）；
- 真实供应商长程验收脚本与报告见 `scripts/real_*.py` 与
  `docs/BACKEND_OPTIMIZATION_STATUS.md`（后端优化 F01–F20 状态矩阵，19/20 完成）。

---

## ⬆️ 升级说明

- 无破坏性 API 变更，前端契约（6 选项 A–F、事件、导出）保持不变；
- 新增灰度开关默认关闭（`STORY_AGENT_MODE`/`STORY_CHOICE_AGENT_MODE`
  默认 shadow），开启 agent 模式即启用全部新架构；
- Windows 版构建流程不变：`build\build_windows.bat` → `dist\FateEngine\`。
