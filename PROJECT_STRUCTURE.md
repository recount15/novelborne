# Novelborne v2.0.4 项目结构

## 目录概览

```
Novelborne-2.0.0-clean-source/
├── core/                      # 核心引擎
│   ├── engine/               # 引擎机制层（纯计算，无 IO）
│   ├── memory/              # 状态存储
│   ├── services/            # 服务层（编排 + 模型注入）
│   ├── app.py              # LEGACY Gradio 应用
│   ├── server.py           # FastAPI 服务器
│   └── fate_engine.py      # LEGACY 引擎
│
├── frontend/               # Vue 3 前端
│   ├── src/               # 源代码
│   └── dist/              # 构建产物
│
├── tests/                 # 测试套件（515+ 测试）
├── assets/                # 资源（试卷配置、角色池）
├── docs/                  # 文档
│   ├── deliveries/       # 交付报告
│   ├── planning/         # 规划文档
│   └── *.md              # 技术文档
│
├── scripts/              # 脚本工具
├── standards/            # 标准规范
├── tools/               # 开发工具
│
├── README.md            # 项目说明（中文）
├── README.en.md         # 项目说明（英文）
├── run_app.py          # Gradio 启动脚本
├── run_windowed.py     # 窗口模式启动
├── verify_c_d_f_e.py   # 阶段验证脚本
└── requirements.txt    # Python 依赖
```

## 核心模块

### Engine 层（纯计算，无 IO）

**批改-重填机制**:
- `agent_refill.py` - 批改-重填循环框架（类 agent 机制）
- `turn_grader.py` - 段空/选项空批改器

**任务系统**:
- `quest.py` - 任务生命周期管理
- `quest_offer.py` - 任务提议生成
- `quest_judgment.py` - 任务判定逻辑

**主角状态**:
- `protagonist_state.py` - 主角状态单源视图
- `state_validator.py` - 状态校验

**质量保障**:
- `quality_gate.py` - 九维质量评分
- `elastic_gate.py` - 弹性门禁（格式修复）

**作弊码系统**:
- `cheat_code.py` - 作弊码基础机制
- `wish_grant.py` - 作弊码结构化落地

**碎锚管线**:
- `break_anchor.py` - 锚点破碎机制
- `free_stage.py` - 碎锚后 free 阶段支持

**其他机制**:
- `autoplay.py` - 托管选线（主角性格驱动）
- `distill.py` - 模型调用统一 choke point
- `token_accounting.py` - Token 使用量计量

### Services 层（编排 + 模型注入）

**核心服务**:
- `turn_pipeline.py` - 回合试卷管线（中台门面）
- `chat_service.py` - 角色闲聊服务（agent_refill 优化）
- `chat_grader.py` - 闲聊批改器（结构化质量检查）

**生成服务**:
- `answer_polish_service.py` - 润色服务
- `character_service.py` - 角色状态更新

### Memory 层（状态管理）

- `extractor.py` - 从叙述中提取状态变化
- `state_store.py` - 状态快照与差异管理
- `state_validator.py` - 状态 schema 校验

## 架构原则

### 1. 分层红线
- **Engine 层**: 纯计算，零 IO
- **Services 层**: 编排逻辑，模型注入
- **App/Server 层**: IO 处理，路由分发

### 2. 单一事实源
- 状态: `state_memory` 唯一权威源
- 配置: `assets/papers/*.json` 单源试卷

### 3. 可测试性
- Engine 层零 IO，便于单元测试
- 515+ 测试覆盖核心逻辑

### 4. 模型调用规范
- 统一走 `distill_model` choke point
- Token 计量自动收集

## 类 Agent 机制

### 批改-重填模式（agent_refill.py）

**核心流程**:
```
初稿 → 批改 → [有错误？] → 重填 → 批改 → Keep-best
```

**应用场景**:
- 段卷批改与重填
- 角色闲聊生成（v2.0.4 新增）

**特点**:
- Keep-best 机制避免质量劣化
- 兜底工厂提供 graceful degradation

## 文档结构

### docs/deliveries/ - 交付报告
- `DELIVERY_A_B.md` - 阶段 A+B 交付
- `DELIVERY_C_D_F_E.md` - 阶段 C+D+F+E 交付
- `FINAL_COMPLETE_DELIVERY.md` - 完整交付总结
- `M1_TEST_PLAN.md` - M1 测试计划

### docs/planning/ - 规划文档
- `FRONTEND_INTEGRATION_GUIDE.md` - 前端集成指南

### docs/ - 技术文档
- `AGENT_REFILL_OPTIMIZATION.md` - Agent 优化报告
- `PROJECT_STRUCTURE.md` - 本文档
- `RELEASE_NOTES_v2.0.1.md` - v2.0.1 发布说明
- `RELEASE_NOTES_v2.0.2.md` - v2.0.2 发布说明

---

**维护者**: Novelborne 团队  
**最后更新**: 2026-09-02  
**版本**: v2.0.4
