# Novelborne API 调用文档

> 版本：v2.1.0 ｜ 适用：Novelborne 本地服务（默认 `http://127.0.0.1:8000`）
> 所有接口均为 HTTP JSON API；生成类接口为 **NDJSON 流式**（见 §2 协议约定）。
> 交互式文档（Swagger UI）：服务启动后访问 `http://127.0.0.1:8000/docs`。

---

## 目录

1. [通用约定](#1-通用约定)
2. [流式协议（NDJSON）](#2-流式协议ndjson)
3. [系统与连接](#3-系统与连接)
4. [开局准备（金手指 / 角色设计 / 上传）](#4-开局准备)
5. [会话主流程（开局 / 推进 / 状态 / 存档）](#5-会话主流程)
6. [游戏内功能（任务 / 碎锚 / 提问 / 闲聊 / 导出）](#6-游戏内功能)
7. [作品库与原著阅读](#7-作品库与原著阅读)
8. [自动试玩（Playtest）](#8-自动试玩playtest)
9. [完整调用示例](#9-完整调用示例)

---

## 1. 通用约定

### 1.1 基础地址

```
BASE = http://127.0.0.1:8000     # 默认；可用 FATE_API_HOST / FATE_API_PORT 或启动参数覆盖
```

### 1.2 认证

**无服务端认证**（本地应用）。模型服务商的 API Key 由调用方在请求体中传递
（`api_key` 字段），服务只在内存中持有，绝不写入磁盘或存档。首次 `start`
携带的 key 会绑定到会话，后续请求可省略。

### 1.3 错误格式

所有非 2xx 响应为 FastAPI 标准 detail 结构：

```json
{ "detail": "错误描述（中文）" }
```

常见状态码：

| 状态码 | 含义 |
|---|---|
| 400 | 参数校验失败 / 功能前置条件不满足（如未开局就发消息） |
| 404 | session / 上传文件 / 存档不存在 |
| 409 | 该 session 正在处理另一个请求（会话级互斥锁） |
| 422 | 请求体字段类型/约束不符（detail 为字段级错误数组） |
| 502 | 上游模型服务返回错误 |

### 1.4 会话互斥

每个 session 同一时刻只处理一个请求；并发第二个请求返回 **409**。
生成类请求（开局/推进）耗时较长（数十秒到数分钟），客户端应串行调用。

### 1.5 session_id 规则

只允许 `字母 / 数字 / 连字符 / 下划线`；不传则服务端生成（uuid hex）。
**`/api/uploads` 的 `session_id` 是 multipart 表单字段，不是查询参数。**

---

## 2. 流式协议（NDJSON）

开局（`/api/sessions/start`）与推进（`/api/sessions/{id}/messages`）返回
`application/x-ndjson`：每行一个 JSON 事件，按序读取直到 `done` 或 `error`。

```
{"type": "state",    "data": {"chat": [...], "state": {...}, "status": "..."}}
{"type": "delta",    "data": {"delta": "正文增量片段"}}          ← v2.1.0 起组装管线逐段推送草稿
{"type": "done",     "data": {"session_id": "...", "operation": "start|message"}}
{"type": "error",    "data": {"operation": "...", "message": "错误描述"}}
```

事件类型：

| type | 说明 |
|---|---|
| `state` | 完整界面状态快照：`chat`（消息列表）、`state`（脱敏后的游戏状态）、`status`（状态栏文字）、`token`（token 用量）等 |
| `delta` | 正文增量（v2.1.0：组装管线段卷完成即推草稿流，终稿以最后一个 `state` 事件整体替换） |
| `done` | 流正常结束 |
| `error` | 出错；开局失败时服务端自动回滚会话状态 |

`state` 中的关键字段：`game_ready`（开局是否完成）、`options`（本回合选项
`[{key, text, preview, factor}]`）、`round`、`tok_last`（上回合 token
`[输入, 输出]`）、`agent_meta`（管线审计）、`active_members`（在场角色）。
注意 `state` 已脱敏：`system`、`api_key`、`nemesis_private` 等永不返回。

---

## 3. 系统与连接

### 3.1 GET /api/health

健康检查。返回：

```json
{ "status": "ok", "version": "2.1.0" }
```

### 3.2 GET /api/bootstrap

一次性拉取前端/调用方所需的全部静态配置：providers 列表（label/base_url/models）、
modes（`["基础模式","强化模式"]`）、difficulties、golden_fingers 预设、
personas、themes、richness 档位表等。**写 API 客户端先调它。**

### 3.3 POST /api/models/fetch

拉取指定服务商的可用模型列表（需 key）。

```json
请求: { "provider": "custom", "base_url": "https://.../v1", "api_key": "sk-..." }
响应: { "models": ["kimi-k3", "..."] }
```

### 3.4 POST /api/models/test

测试连接与模型可用性。

```json
请求: { "provider": "custom", "base_url": "https://.../v1", "api_key": "sk-...", "model": "kimi-k3" }
响应: { "ok": true, "message": "连接成功", "latency_ms": 812 }
```

### 3.5 GET /api/lan-info

局域网访问信息（供手机扫码）。返回 `{ listening, lan_url, addresses }`。

### 3.6 GET /api/lan-qrcode.png

局域网地址二维码 PNG（`?session_id=...&address=...`）。

### 3.7 GET /api/playtest-monitor

自动试玩监控页（HTML，内部工具）。

---

## 4. 开局准备

### 4.1 POST /api/golden-fingers/recommend

按世界/人设/难度/宿敌强度推荐金手指（本地规则，无模型调用）。

```json
请求: { "world": "都市", "persona": "大学生", "difficulty": "D4 普通", "nemesis_d": 6.0 }
响应: { "choices": ["过目不忘", "...", "无金手指", "自定义..."],
        "specs": [{...金手指规格}], "nemesis_d": 6.0, "gf": 0.46, ... }
```

### 4.2 POST /api/golden-fingers/propose

把玩家自由描述的金手指正式化为结构化规格（≤3 次尝试，attempt 1–3）。

```json
请求: { "world": "...", "persona": "...", "difficulty": "D4 普通",
        "text": "每天可以回溯一小时时间", "attempt": 1 }
响应: { "proposal": {...规格}, "validated": true, "budget": {...}, "issues": [] }
```

### 4.3 POST /api/golden-fingers/confirm

确认金手指规格（返回最终确认对象，用于 start 的 `golden_finger_proposal`）。

```json
请求: { "proposal": {…上一步的 proposal} }
```

### 4.4 GET/POST /api/gf-designer/options | /compose | /polish | /specs

金手指设计器（表单化设计 + LLM 润色 + 保存规格）。

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/gf-designer/options` | GET | 构成/燃料/代价/冷却等下拉选项 |
| `/api/gf-designer/compose` | POST | 草稿 → 结构化规格 + 质量门 |
| `/api/gf-designer/polish` | POST | LLM 润色规格（带 provider/key/model） |
| `/api/gf-designer/specs` | GET/POST | 保存的规格列表 / 保存新规格 |
| `/api/gf-designer/specs/{spec_id}` | GET | 取单个规格 |

`compose` 请求体（全部可选，显式字段覆盖 `draft`）：

```json
{ "composition": "信息", "fuels": [], "cost": "", "cooldown": "",
  "difficulty": "D4 普通", "name": "", "effect": "", "scope": "", "fit": "",
  "world": "", "draft": {} }
```

### 4.5 POST /api/uploads

上传 TXT/MD 文件（multipart/form-data）。**`session_id`、`kind` 是表单字段。**

| kind | 用途 |
|---|---|
| `novel` | 强化模式的完整原著 TXT |
| `persona` | 人设文件 |
| `roster-skill` | 名册角色卡 |
| `nemesis` | 自定义宿敌 |

```
POST /api/uploads
Content-Type: multipart/form-data
  file:        (binary) book.txt
  session_id:  my_session_1
  kind:        novel

响应: { "session_id": "my_session_1",
        "upload": { "upload_id": "9602c7af…", "filename": "book.txt",
                    "display_name": "book", "kind": "novel", "bytes": 10855 } }
```

返回的 `upload_id` 填入 start 的 `novel_upload_id` / `persona_upload_id` /
`nemesis_upload_id`。上传与开局必须使用**同一个 session_id**。

### 4.6 角色设计器与角色库

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/character-designer/schema` | GET | 角色设计器表单 schema |
| `/api/character-designer/generate` | POST | LLM 生成角色卡（带 provider/key/model/session_id） |
| `/api/character-designer/save` | POST | 保存人设 Markdown 到文件 |
| `/api/characters/pool` | GET | 内置+自定义角色池列表 |
| `/api/characters/pool/{card_id}/detail` | GET | 角色卡详情 |
| `/api/character-library` | GET/POST | 角色库列表 / 新增或更新（UPSERT） |
| `/api/character-library/{card_id}` | PUT/DELETE | 更新 / 删除 |
| `/api/character-library/export` | GET | 导出角色库 JSON |
| `/api/character-library/import` | POST | 批量导入 `{ "characters": [...], "overwrite": false }` |

`character-library` UPSERT 主要字段：`name`（必填）、`role`（伙伴/主角/宿敌…）、
`work`、`archetype`、`desire`、`fear`、`abilities`、`voice`、`background`、
`gender` 及四栏槽位类型（`protagonist_type`/`mainline_type`/`partner_type`/`nemesis_type`）。

---

## 5. 会话主流程

### 5.1 POST /api/sessions/start —— 开局（流式）

完整请求体（均可选，列出的为主要字段）：

```jsonc
{
  "session_id": "my_session_1",        // 不传则服务端生成
  "provider": "custom",                // deepseek/qwen/kimi/zhipu/openai/custom
  "base_url": "https://.../v1",        // provider=custom 必填；其他可覆盖预设
  "api_key": "sk-...",                 // 绝不落盘
  "model": "kimi-k3",
  "thinking_mode": "auto",             // auto/on/off
  "thinking_param": "",

  "mode": "强化模式",                  // "基础模式"（作品库）/"强化模式"（上传原著）
  "work": null,                        // 基础模式：作品库书名；强化模式必须 null
  "novel_upload_id": "9602c7af…",      // 强化模式必填（/api/uploads 的返回值）
  "fragment": "现代都市",              // 基础模式：题材碎片
  "role": "大学生",
  "timepoint": "开学季",
  "difficulty": "简单",                // 难度档
  "protagonist_gender": "unknown",     // male/female/unknown

  "golden_finger": "学霸系统",         // 已确认的金手指名
  "golden_finger_proposal": {},        // /api/golden-fingers/confirm 的产物（自定义时）
  "persona_preset": "",                // 通用性格预设名
  "persona_custom": "",                // 自定义性格描述
  "persona_upload_id": null,

  "distill_enabled": true,             // 强化模式锚点蒸馏
  "companion_roster": [{"name": "小明", "voice": "开朗外向"}],
  "heroine_roster":  [{"name": "小红", "voice": "文静温柔"}],
  "companion_count": 1,
  "heroine_count": 1,
  "heroine_mode": "单女主",            // "单女主"/"多女主"（单女主时 heroine_roster ≤1）

  "enable_nemesis": false,             // 强化模式宿敌
  "nemesis_select": "",
  "nemesis_upload_id": null,

  "convergence": "较高",               // 收束力（字符串档位）
  "story_richness": 800,               // 300–1000 单回合体量刻度
  "paper_tier": 2,                     // 1–6 试卷档位；缺省按 richness 映射
  "story_agent_mode": false,           // 类 Agent 生成（第6档必须开）
  "roster_card_ids": [],               // [{"slot":"主角|主线|伙伴|宿敌","card_id":"..."}]
  "use_enhanced_pregame": false,
  "pre_game_state": {},
  "client_request_id": null            // 幂等去重（可选）
}
```

约束：`convergence` 必须是字符串；`story_richness` ∈ [300, 1000]；
`roster_card_ids` 必须是数组；强化模式必须先上传可切章 TXT（上传失败或章节
识别不出返回 400）；单女主模式女主名单 >1 返回 400。

响应：NDJSON 流（§2）。流结束后 GET `/api/sessions/{id}/state` 应看到
`game_ready: true`。强化模式开局后还需两步确认（§5.2）。

### 5.2 POST /api/sessions/{session_id}/messages —— 推进一回合（流式）

```json
请求: { "message": "我走进教室，环顾四周。",       // 1–20000 字
        "api_key": null, "model": null, ... }       // 会话已绑定 key 时可省略
```

- 普通文本 = 玩家自由行动；`选择A：<选项文本>` = 选择选项。
- 强化模式刚开局时依次发送 `"确认金手指"`、`"确认开局"` 完成开局确认。
- 响应 NDJSON：v2.1.0 起组装管线**段卷完成即推 `delta` 草稿**（首字节约数秒），
  后处理完成后最终 `state` 事件携带终稿与 `options`。
- 409 = 上一回合仍在处理；404 = session 不存在。

### 5.3 GET /api/sessions/{session_id}/state —— 读取状态

返回 `{ "session_id": "...", "state": {…脱敏状态} }`（见 §2 字段说明）。
页面刷新/进程重启后服务会尝试从磁盘存档自动回填会话。

### 5.4 存档

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/sessions/{id}/save` | POST | 保存存档 `{ "save_id": "latest" \| 自定义名 }` |
| `/api/saves` | GET | 存档列表（save_id / session_id / saved_at / 摘要） |
| `/api/saves/load` | POST | 按存档恢复 `{ "save_id": "latest" }` |
| `/api/sessions/{id}/load` | POST | 把指定存档加载进该会话（覆盖当前进度） |

### 5.5 导出小说

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/sessions/{id}/export-novel` | POST | 导出当前对局为小说文稿（可带 LLM 润色 style/provider/key） |
| `/api/saves/{save_id}/export-novel` | POST | 导出指定存档 |

### 5.6 UI 状态

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/session/ui-state` | POST | `{ "session_id": "...", "ui_state": {...} }` 保存前端 UI 状态（内存） |
| `/api/session/ui-state?session_id=` | GET | 读取 |

### 5.7 GET /api/sessions/{session_id}/distill/progress

强化模式锚点蒸馏进度（前端 5s 轮询）：`{ stage, current, total, status }`。

---

## 6. 游戏内功能

### 6.1 任务系统

| 接口 | 方法 | 请求体 | 功能 |
|---|---|---|---|
| `/api/sessions/{id}/quests/offer` | POST | `{ "kind": "short|medium|long", "difficulty": 0.5 }` | 生成任务提议 |
| `/api/sessions/{id}/quests/accept` | POST | — | 接受当前提议 |
| `/api/sessions/{id}/quests/decline` | POST | — | 拒绝当前提议 |
| `/api/sessions/{id}/quests` | GET | — | 当前任务状态（目标/进度/奖励） |

任务完成判定在每回合推进时由证据制判定 Agent 自动进行（正文逐字引文佐证），
完成后奖励经 `apply_turn` 写入状态。

### 6.2 碎锚（打破剧情锚点）

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/sessions/{id}/break-anchor/offer` | POST | 发起碎锚挑战提议 |
| `/api/sessions/{id}/break-anchor/accept` | POST | 接受 |
| `/api/sessions/{id}/break-anchor/decline` | POST | 拒绝 |

碎锚全阶段完成后进入 free 阶段（自由剧情线，专属试卷与质量门变体）。

### 6.3 代理选择与批量提问

| 接口 | 方法 | 请求体 | 功能 |
|---|---|---|---|
| `/api/sessions/{id}/autoplay-choice` | POST | — | 由"主角性格子智能体"替玩家选择本回合行动 |
| `/api/sessions/{id}/questions/batch` | POST | `{ "questions": [...], "context": {}, "max_concurrency": 10 }` | 批量结构化提问 |
| `/api/sessions/{id}/questions/answer` | POST | `{ "question": {...}, "answer": ... }` | 回答单个提问 |
| `/api/sessions/{id}/ask` | POST | `{ "question": "…" }`（≤4000字） | 基于规则文档答疑（不影响剧情） |
| `/api/setup/questions` | POST | 开局前的设定类提问 |

### 6.4 角色闲聊（v2.1.0 重点修复）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/chat/roster?session_id=` | GET | 可聊天角色列表。开局即可用（无活跃角色时回落到同伴/女主名册）。响应 `{ "roster": [{"name","voice","desire","fear",...}] }` |
| `/api/chat/send?session_id=` | POST | `{ "character_name": "小明", "message": "你好" }`（≤500字） |

`chat/send` 响应：

```json
{ "reply": "（角色以自己的 voice 回复）",
  "character": "小明",
  "meta": { "reply_length": 104, "quality_issues": [], "refills": 0, "usage": {...} },
  "usage": { "total": 1188, "prompt": 1133, "completion": 55 } }
```

**硬保证**：聊天只写 `state["side_chats"]`，绝不改写
history/state_memory/quest/convergence——不推进剧情、不消耗回合、不触发结算。
双卷并发生成择优（v2.1.0）。

---

## 7. 作品库与原著阅读

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/books` | GET | 作品库书目（基础模式可选作品 + 已收录书目） |
| `/api/books/{book_id}` | GET | 书目详情 |
| `/api/books/{book_id}/chapters/{chapter_index}` | GET | 章节正文 |
| `/api/books/{book_id}/chapters/{chapter_index}/anchors` | GET | 该章剧情锚点 |
| `/api/books/{book_id}/search` | GET | 书内搜索（`?q=`） |
| `/api/books/{book_id}/prepare` | POST | 预切章/预热 |
| `/api/books/quick-distill` | POST | 手动快速蒸馏：把已切章 TXT 收录进作品库 `{ "book_id": "...", "api_key": "...", "provider": "...", "base_url": "...", "model": "..." }` |

---

## 8. 自动试玩（Playtest）

无人值守跑局的质量回归工具：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/playtest/start` | POST | `{ "api_key": "sk-...", "provider": "custom", "base_url": "...", "model": "...", "thinking_mode": "auto", "rounds": 30, "story_richness": 700, "force": false }`（rounds 1–100） |
| `/api/playtest/stop` | POST | 停止 |
| `/api/playtest/status` | GET | `{ "running": bool, "round": int, ... }` |
| `/api/playtest/stream` | GET | NDJSON 进度流 |

---

## 9. 完整调用示例

```python
import json, requests

BASE = "http://127.0.0.1:8000"
SID  = "my_session_1"
KEY  = "sk-..."          # 只放环境变量，别写进代码

def stream(url, body):
    """读 NDJSON 流，返回最后一个 state 与错误。"""
    last, err = None, None
    with requests.post(url, json=body, stream=True, timeout=1800) as r:
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw: continue
            ev = json.loads(raw)
            if ev["type"] == "error": err = ev["data"]["message"]
            if ev["type"] == "state": last = ev["data"]
    return last, err

# ① 基础模式开局（强化模式见 §4.5 先上传 TXT）
state, err = stream(f"{BASE}/api/sessions/start", {
    "session_id": SID, "provider": "custom",
    "base_url": "https://your-endpoint.example/v1",
    "api_key": KEY, "model": "kimi-k3",
    "mode": "基础模式", "fragment": "现代都市", "role": "大学生",
    "difficulty": "简单", "convergence": "较高", "story_richness": 800,
    "golden_finger": "学霸系统",
    "companion_roster": [{"name": "小明", "voice": "开朗外向"}],
    "heroine_roster": [{"name": "小红", "voice": "文静温柔"}],
    "heroine_mode": "单女主",
})
assert state["state"]["game_ready"] is True

# ② 角色闲聊（开局即可用）
roster = requests.get(f"{BASE}/api/chat/roster",
                      params={"session_id": SID}).json()["roster"]
reply = requests.post(f"{BASE}/api/chat/send",
                      params={"session_id": SID},
                      json={"character_name": roster[0]["name"],
                            "message": "今天感觉怎么样？"}).json()
print(reply["reply"], reply["usage"])

# ③ 推进一回合（选择选项或自由行动）
opt = state["state"]["options"][0]
msg = f"选择{opt['key']}：{opt['text']}"
state, err = stream(f"{BASE}/api/sessions/{SID}/messages", {"message": msg})

# ④ 存档 / 读档
requests.post(f"{BASE}/api/sessions/{SID}/save", json={"save_id": "latest"})
saves = requests.get(f"{BASE}/api/saves").json()
```

> 完整可运行示例随 Release 附带（`examples/api_walkthrough.py`）。
