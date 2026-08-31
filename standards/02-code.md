# 02 代码规范

## 1. 导入（本项目最易翻车的点）

**只允许 `core.*` 绝对导入**：

```python
from core import app                    # 对局流程
from core import fate_engine as fe      # 模型接入层
from core import engine                 # 机制包门面
from core.engine.distill import distill_model
from core.engine import quest, persistence
from core.api.contracts import public_state
from core.ui import common as ui_common
from core.memory import blank_state
from core.lore import LoreInjector
from core import prompts
```

**禁止**：
- `import app` / `import fate_engine` / `import engine` / `import api_server`（裸名时代已终结）
- `from engine.xxx import ...` / `from api.xxx import ...`（裸名）
- 任何形式的 `sys.path.insert/append`（历史包袱，2026-08-28 已清除）
- `import core.engine.xxx` 后裸用 `engine.xxx`——该形式只绑定 `core` 名；需要 `engine.` 前缀引用时写 `from core import engine`

**`import xxx as yyy` 别名场景**：文件内以 `engine.`/`fe.` 前缀引用时，确保对应名字被绑定（`from core import engine` / `from core import fate_engine as fe`）。

## 2. engine 包注册

- `core/engine/__init__.py` 的 `_LAZY_EXPORTS`（函数名 → 子模块）与 `_SUBMODULES`（子模块白名单）是惰性导出门面。**新增 engine 子模块必须登记到 `_SUBMODULES`**，对外函数登记到 `_LAZY_EXPORTS`；禁止引用不存在的模块（resource_extractor 死声明事故即此）。
- engine 内部互相引用用 `from core.engine.xxx import ...`；`__init__.py` 内部用相对导入。

## 3. 错误处理

- **空结果 ≠ 成功**：模型响应成功但正文为空必须视为失败并降级/抛错（distill.py 空串降级阶梯是先例）。禁止把空串/空 dict 当成功静默返回。
- **降级阶梯模式**：外部服务调用按「完整参数 → 剥思考参数 → 剥采样参数 → 流式累积」降级，每级失败落下一级，全部失败抛错（见 `core/engine/distill.py`）。
- **非参数类错误立即上抛**，不得吞掉重试；参数类错误（TypeError / `_is_unsupported_parameter_error`）才降级。
- 玩家可见错误**纯中文**：剥掉英文异常类名与原始 JSON（`_safe_distill_error` 先例）；凭据先脱敏。
- 后台任务失败必须可诊断：写入 state 的 `distill`/`status` 字段，且保证可自动重试（anchor_distiller 的首章重试修复是先例——队列不得因"假定已完成"而永久跳过失败项）。

## 4. 风格约定

- 文件头 `# -*- coding: utf-8 -*-` + 模块 docstring（职责一句话 + 关键约定）。
- 中文注释、中文用户可见文本；内部标识符英文 snake_case。
- 类型标注：`from __future__ import annotations` 开头；公共函数标注参数与返回类型。
- 常量大写集中定义（如 `SLOT_KEY_VOCAB`、`ROLE_TYPE_TO_DB_ROLE`），词表类常量改动必须同步使用方校验逻辑。
- 单文件超 ~2500 行时考虑拆分（core/app.py 是历史上限，不再放大）。

## 5. 前端（frontend/src）

- Vue 3 `<script setup lang="ts">` + Tailwind；类型集中 `types.ts`，API 调用集中 `api.ts`。
- 构建 `npm run build`（vue-tsc 类型检查 + vite 打包），产物 `frontend/dist` 由后端托管。
- 玩家可见文案纯中文；状态码到中文的映射在前端完成（如蒸馏状态 `pending/in_progress/done/failed` → 中文标签）。
- 轮询类逻辑必须有清理（`onBeforeUnmount` 清 interval），失败静默重试不打断游玩。
