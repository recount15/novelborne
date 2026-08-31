# 04 接口规范

## 1. 端点形态

- 全部端点集中在 `core/server.py`，前缀 `/api/`；前端静态路由（`/{path:path}`）必须在所有 API 路由之后注册。
- 请求模型用 pydantic（`BaseModel`），字段给出默认值与校验；响应用 `dict[str, Any]` 或 `StreamingResponse`。
- 错误一律 `HTTPException(status_code=4xx/5xx, detail="纯中文错误信息")`：
  - 400 参数/状态校验失败（附带中文原因）
  - 404 资源不存在（session/存档/上传）
  - 409 会话被占用（`sessions.acquire` 失败）
  - 502 模型侧失败（蒸馏/生成/解析）

## 2. 会话纪律

- 会话由 `core/api/sessions.py SessionManager` 管理；端点处理长任务必须 `sessions.acquire` / `finally: sessions.release`。
- session_id 只允许字母数字连字符下划线。
- state 进出前端必须经 `core/api/contracts.py:public_state` 脱敏：剥 `system`/`api_key`/`request_kwargs`/`persona_text`/`nemesis_private`。**新增敏感字段必须同步加入剥离清单**。

## 3. 流式事件（NDJSON）

- 开局/发消息走 `StreamingResponse` + NDJSON 事件流；GradIO 元组输出统一经 `stream_event_from_gradio` 转 `StreamEvent(type, data)`。
- 事件类型稳定：`state`（chat+state+status）、`message`。新增事件类型须同步前端 `types.ts` 的 `StreamEvent` 与 `readNdjson` 消费方。

## 4. 蒸馏/模型子调用

- 一切内部模型子调用（蒸馏/自检/托管/任务判定）必须走 `core/engine/distill.py:distill_model` 统一通道，禁止绕过直接 `client.chat.completions.create`（qa 通路等历史例外除外）。
- 端点内调用用模块级导入的 `distill_model`（`core.server` 命名空间），测试 mock 目标是 `api_server.distill_model`（mock `gradio_app._distill_model` 是已修复的历史错位，勿回退）。

## 5. 端点示例（进度汇报类）

```python
@app.get("/api/sessions/{session_id}/distill/progress")
def distill_progress(session_id: str) -> dict[str, Any]:
    session = _session_or_404(session_id)   # 404 先行
    state = session.state or {}
    # ...输出纯中文、无英文枚举/原始 JSON（status_zh 映射在前端之前想好）
```

- 轮询类端点必须：无状态副作用（只读）、快速返回、输出可直接展示（中文摘要 + 结构化条目）。
- 玩家可见字段禁止出现英文状态码裸值；保留机器字段（`status`）的同时必须给中文字段（`status_zh`）。
