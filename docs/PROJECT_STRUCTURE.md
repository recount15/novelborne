# Novelborne v2.0.4 项目结构

## 目录结构

```
Novelborne-2.0.0-clean-source/
├── core/                      # 核心引擎
│   ├── engine/               # 引擎机制层
│   │   ├── agent_refill.py  # 批改-重填机制
│   │   ├── autoplay.py      # 托管选线
│   │   ├── cheat_code.py    # 作弊码机制
│   │   ├── distill.py       # 模型调用统一通道
│   │   ├── free_stage.py    # 碎锚 free 管线
│   │   ├── protagonist_state.py  # 主角状态
│   │   ├── quality_gate.py  # 质量门
│   │   ├── quest.py         # 任务机制
│   │   ├── token_accounting.py  # Token 计量
│   │   ├── wish_grant.py    # 作弊码落地
│   │   └── ...
│   ├── memory/              # 状态存储
│   │   ├── extractor.py    # 状态提取
│   │   ├── state_store.py  # 状态管理
│   │   └── state_validator.py  # 状态校验
│   ├── services/            # 服务层
│   │   ├── chat_service.py      # 角色闲聊
│   │   ├── directives_service.py  # 铁律账本
│   │   ├── turn_pipeline.py     # 回合管线
│   │   └── ...
│   ├── app.py              # LEGACY Gradio 应用
│   ├── server.py           # FastAPI 服务器
│   └── fate_engine.py      # LEGACY 引擎
│
├── frontend/               # 前端
│   ├── src/
│   │   ├── App.vue        # 主应用
│   │   ├── api.ts         # API 封装
│   │   └── ...
│   └── dist/              # 构建产物
│
├── tests/                 # 测试
│   ├── test_quest.py      # 任务测试
│   ├── test_protagonist_state.py  # 状态测试
│   ├── test_chat_service.py      # 闲聊测试
│   ├── test_wish_grant.py        # 作弊码测试
│   └── ...
│
├── assets/                # 资源
│   ├── papers/           # 试卷配置
│   └── ...
│
├── docs/                 # 文档
│   ├── deliveries/      # 交付文档
│   ├── planning/        # 规划文档
│   └── ...
│
├── scripts/             # 脚本工具
├── standards/           # 标准规范
├── tools/              # 开发工具
│
├── run_app.py          # 启动脚本（Gradio）
├── run_windowed.py     # 窗口模式启动
├── verify_c_d_f_e.py  # 验证脚本
└── requirements.txt    # 依赖
```

## 核心模块职责

### Engine 层（纯计算，无 IO）
- **agent_refill**: 批改-重填循环（类 agent 机制）
- **autoplay**: 托管选线（主角性格驱动）
- **cheat_code**: 作弊码基础机制
- **distill**: 模型调用统一 choke point
- **free_stage**: 碎锚后 free 阶段支持
- **protagonist_state**: 主角状态单源视图
- **quality_gate**: 九维质量评分
- **quest**: 任务生命周期管理
- **token_accounting**: Token 使用量计量
- **wish_grant**: 作弊码结构化落地

### Services 层（编排 + 模型注入）
- **chat_service**: 角色闲聊服务
- **directives_service**: 铁律账本服务
- **turn_pipeline**: 回合试卷管线中台门面

### Memory 层（状态管理）
- **extractor**: 从叙述中提取状态变化
- **state_store**: 状态快照与差异管理
- **state_validator**: 状态 schema 校验

## 架构原则

1. **分层红线**: Engine 纯计算，Services 编排，App/Server 处理 IO
2. **单一事实源**: 状态、配置、机制均单源，避免重复
3. **可测试性**: Engine 层零 IO，便于单元测试
4. **模型注入**: 模型调用通过参数注入，不在机制层直接调用

## 类 Agent 机制

### 批改-重填模式（agent_refill.py）
- **用途**: 段卷、选项卷、角色卷的质量提升
- **流程**: 初稿 → 批改 → 重填（≤attempts 次）→ 兜底
- **特点**: 单空独立，失败不影响其他空

### 托管模式（autoplay.py）
- **用途**: 主角性格驱动的选项自动选择
- **流程**: 提示词装配 → 模型选择 → 结果解析
- **特点**: 单回合，不连续推进

## 测试策略

- **单元测试**: Engine 层纯计算逻辑（499 tests）
- **集成测试**: Services 层编排逻辑
- **端到端测试**: 完整流程验证（需 API Key）

## 文档结构

- **交付文档**: `docs/deliveries/` - 各阶段交付报告
- **规划文档**: `docs/planning/` - 计划与设计
- **技术文档**: `docs/` - 架构、API、规范
- **用户文档**: `README.md`, `README.en.md`

