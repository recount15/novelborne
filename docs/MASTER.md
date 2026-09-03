# Novelborne v2.1.0 总文档

> 本文是项目的单一入口文档：项目概览、快速上手、用户手册、API 参考、
> 架构说明、机制详解、版本与发布信息集中于此；各专题的完整版在 `docs/` 下。
>
> - 用户手册（随 Release 附带）：`docs/USER_MANUAL.md`
> - API 全量参考：`docs/API.md`
> - 架构与开发：`docs/ARCHITECTURE.md`、`docs/HANDBOOK.md`
> - 发布说明：`docs/RELEASE_NOTES_v2.1.0.md`

---

## 1. 项目概览

**书中织梦 · Novelborne** 是一个由大模型驱动的互动小说世界模拟器。玩家以
穿越者身份进入原著世界（强化模式：上传完整 TXT；基础模式：作品库），模型
负责呈现角色与场景，程序负责世界规则、锚点、涟漪、任务、角色状态、收束力
与会话持久化。

核心设计：把大模型的不确定性限制在小的、可验证的生成任务中——

```
结构化出卷（导演卷）→ 并行填空（段卷 ∥ 选项卷）→ 空级批改 → 错题重填
→ 代码组装 → 全局润色 + 质量门（keep-best）→ 机制结算（并发波次）
```

### v2.1.0 主题：全链路并行化（质量与速度）

| 优化 | 内容 | 收益 |
|---|---|---|
| 流式草稿（S1） | 组装管线段卷完成即推正文草稿流，终稿整体替换 | 首字节从分钟级降至数秒 |
| 段级重填并发（S4） | 批改-重填按段并行 | 重填耗时 sum→max |
| 后结算 4 路并发（S2） | 任务/碎锚/宿敌/角色 patch 一波并发 | 结算耗时 sum→max |
| 压缩离线化（S3） | 上下文压缩延后到下回合开头 | 回合结束不再等待 |
| 润色 best-of-2（S5） | 双候选并发择优 | 质量取上界、耗时不变 |
| 选项 best-of-2（S6） | 双视角选项卷并发择优 + 蓝图节拍注入 | 选项多样性与贴合度↑ |
| 判定投票（Q3） | 任务判定 self-consistency 双卷 | 单次幻觉消除 |
| 聊天 best-of-2（Q4） | 闲聊双候选并发择优 | 角色 voice 一致性↑ |

并发安全：所有并行模型调用经 `copy_context` 快照携带回合级 Token 累加器，
计量不丢不重；全程 581 项单元测试覆盖。

---

## 2. 快速上手

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
python run_app.py            # 打开 http://127.0.0.1:8000
```

- 首屏配置模型连接（Key 只在内存，不落盘）→ 选模式 → 确认设定 →
  选金手指 → 开始模拟
- 强化模式：上传可切章 TXT → 校验并准备 → 开局后输入「确认金手指」
  「确认开局」
- 手机同 Wi-Fi 扫首页二维码继续同一局

详见 `docs/USER_MANUAL.md`（随 Release 附带的用户手册）。

---

## 3. API 参考（摘要）

服务为 FastAPI，66 个 HTTP 接口；生成类接口为 NDJSON 流式。完整文档见
`docs/API.md`（含全部请求/响应结构与可运行示例），交互式文档在 `/docs`。

| 分组 | 接口 |
|---|---|
| 系统 | `GET /api/health`、`GET /api/bootstrap`、`POST /api/models/fetch`、`POST /api/models/test` |
| 开局准备 | `/api/golden-fingers/*`（推荐/正式化/确认）、`/api/gf-designer/*`、`/api/uploads`（multipart，session_id 为表单字段）、`/api/character-designer/*`、`/api/character-library`、`/api/characters/pool` |
| 会话 | `POST /api/sessions/start`（NDJSON）、`POST /api/sessions/{id}/messages`（NDJSON）、`GET /api/sessions/{id}/state`、`/save`、`/load`、`GET /api/saves`、`/export-novel` |
| 游戏内 | `/quests/*`（offer/accept/decline/get）、`/break-anchor/*`、`/autoplay-choice`、`/questions/*`、`/ask`、`GET /api/chat/roster`、`POST /api/chat/send` |
| 作品库 | `/api/books/*`（列表/章节/锚点/搜索/预切/快速蒸馏） |
| 试玩 | `/api/playtest/*`（start/stop/status/stream） |
| 其他 | `/api/session/ui-state`、`/api/sessions/{id}/distill/progress`、`/api/lan-info`、`/api/lan-qrcode.png` |

关键约定：

- **NDJSON 流**：`{"type":"state"|"delta"|"done"|"error", "data":{...}}` 逐行
  读取；v2.1.0 起 `delta` 事件实时携带正文草稿
- **会话互斥**：同一 session 并发请求返回 409
- **脱敏**：返回的 state 永不含 system/api_key/nemesis_private

---

## 4. 架构说明（摘要）

```
frontend/        Vue 3 + Vite + Tailwind（26 主题）；NDJSON 流式渲染
core/server.py   FastAPI：66 路由、会话锁、上传、脱敏层（public_state）
core/app.py      on_start/on_send 回合编排；LEGACY 单卷兜底；结算波次
core/services/   turn_pipeline（出卷-填空-批改-组装）、options_service、
                 chat_service、character_service、golden_finger_service…
core/engine/     papers（试卷）、turn_blueprint、turn_grader、quality_gate
                 （keep-best 质量门）、agent_refill、parallel（并发控制器
                 HARD_LIMIT=10）、distill（漏斗+Token 计量）、quest、
                 break_anchor、nemesis_agent、context_compressor…
assets/papers/   各档位各阶段试卷定义（setup/climax/free）
assets/prompts/  提示词模板（单一来源，代码不内嵌提示词）
```

完整架构：`docs/ARCHITECTURE.md`；开发者手册：`docs/HANDBOOK.md`。

### 机制速览

- **锚点/收束力**：原著剧情锚点按章蒸馏，动态收束力决定剧情被拉回原著的
  强度；碎锚可逐阶段挣脱，完成后进入 free 自由剧情线
- **任务**：证据制判定（完成条件须正文逐字佐证），奖励经 `apply_turn`
  事务写入状态，发放失败挂起补发
- **角色状态**：state_memory 唯一权威源；模型只能提交 patch（evidence
  须为正文精确子串），校验通过才落账
- **质量门**：九维规则评分 + 证据制裁判分，keep-best + 分维无回退门 +
  特赦轮 + 终检防线；不合格回落 LEGACY 单卷重新生成
- **Token 计量**：全部模型调用经 distill 漏斗收口，回合级分项
  （director/segments/options/polish/gate/quest/chat）实时上 UI

---

## 5. 目录结构

```
├── run_app.py / run_windowed.py     # 服务入口 / 桌面窗口入口
├── core/                            # 后端（FastAPI + 引擎 + 服务层）
├── frontend/                        # Vue 3 前端（构建产物入 dist/）
├── assets/                          # 试卷/提示词/静态数据
├── tests/                           # 581 项单元测试
├── build/                           # PyInstaller 配方（spec/bat/ico）
├── docs/                            # 全部文档（本文件所在）
├── scripts/ tools/ standards/       # 辅助脚本与规范
└── var/                             # 运行数据（git 忽略，绝不入库）
```

---

## 6. 版本与发布

- 当前版本：**2.1.0**（BSD 3-Clause，见 LICENSE）
- 发布说明：`docs/RELEASE_NOTES_v2.1.0.md`
- 发布渠道：GitHub / Gitee，tag `v2.1.0`；Release 附件含源码包与用户手册
- 隐私红线：API key 只走内存/环境变量；运行数据（var/）、本机绝对路径、
  上传原著文本一律不入库；局域网地址动态探测不落盘

---

*文档版本 2.1.0 · 2026-09*
