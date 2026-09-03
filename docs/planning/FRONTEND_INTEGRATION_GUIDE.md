# v2.0.4 前端集成指南

## 概述

本文档说明阶段 F（角色闲聊）和阶段 E（Token 实时显示）的前端集成方案。

---

## 一、角色闲聊 UI（阶段 F）

### 1.1 API 端点（需添加）

**后端文件**: `core/app.py`

```python
# 获取活跃角色列表
@app.get("/chat/roster")
async def get_chat_roster(session_id: str = Query(...)):
    """获取当前活跃角色列表供闲聊"""
    from core.services import chat_service
    state = _load_state(session_id)
    roster = chat_service.get_roster(state)
    return {"roster": roster}

# 发送闲聊消息
@app.post("/chat/send")
async def send_chat_message(
    session_id: str = Query(...),
    character_name: str = Body(...),
    message: str = Body(...),
):
    """与角色闲聊"""
    from core.services import chat_service
    
    state = _load_state(session_id)
    
    try:
        result = chat_service.generate_reply(
            character_name, message, state,
            client=_get_client(state),
            model=state.get("model", ""),
            request_kwargs=_get_request_kwargs(state),
            provider=state.get("provider", "deepseek")
        )
        
        # 保存聊天记录
        chat_service.save_chat(state, character_name, message, result["reply"])
        _save_state(session_id, state)
        
        return {
            "reply": result["reply"],
            "character": result["character"],
            "meta": result["meta"]
        }
    except chat_service.ChatClientError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except chat_service.ChatUpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e))
```

### 1.2 前端 API 函数（添加到 `frontend/src/api.ts`）

```typescript
export interface ChatRosterEntry {
  name: string
  voice: string
  desire: string
  fear: string
}

export interface ChatReply {
  reply: string
  character: string
  meta: {
    input_length: number
    reply_length: number
    quality_issues: string[]
  }
}

export async function getChatRoster(sessionId: string): Promise<ChatRosterEntry[]> {
  const res = await fetch(`/chat/roster?session_id=${sessionId}`)
  if (!res.ok) throw new Error('Failed to fetch chat roster')
  const data = await res.json()
  return data.roster
}

export async function sendChatMessage(
  sessionId: string,
  characterName: string,
  message: string
): Promise<ChatReply> {
  const res = await fetch(`/chat/send?session_id=${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ character_name: characterName, message })
  })
  if (!res.ok) {
    const error = await res.json()
    throw new Error(error.detail || 'Failed to send chat message')
  }
  return res.json()
}
```

### 1.3 前端组件（添加到 `frontend/src/App.vue`）

**位置**: 在选项区域（line ~3009）**上方**添加

```vue
<!-- 角色闲聊区域 -->
<div v-if="activeMembers.length && inGame" class="mb-3 rounded-md border border-(--fe-border) bg-(--fe-panel) p-2">
  <div class="mb-2 flex items-center gap-2">
    <UsersRound :size="14" class="text-(--fe-accent)" />
    <span class="text-xs font-semibold">角色闲聊</span>
  </div>
  
  <!-- 角色选择下拉框 -->
  <select 
    v-model="selectedChatCharacter" 
    class="field mb-2 w-full text-xs"
    :disabled="busy || chatBusy"
  >
    <option value="">选择角色...</option>
    <option v-for="member in activeMembers" :key="member.name" :value="member.name">
      {{ member.name }}
    </option>
  </select>
  
  <!-- 聊天历史（可折叠） -->
  <div v-if="selectedChatCharacter && chatHistory.length" class="mb-2 max-h-32 space-y-1 overflow-y-auto text-[11px]">
    <div v-for="(msg, i) in chatHistory" :key="i" class="rounded bg-(--fe-panel-2) p-1.5">
      <div class="text-(--fe-accent)">你: {{ msg.player }}</div>
      <div class="mt-0.5 text-(--fe-ink)">{{ selectedChatCharacter }}: {{ msg.reply }}</div>
    </div>
  </div>
  
  <!-- 输入区域 -->
  <div v-if="selectedChatCharacter" class="flex gap-2">
    <input
      v-model="chatInput"
      type="text"
      class="field flex-1 text-xs"
      :placeholder="`与${selectedChatCharacter}聊天...`"
      :disabled="busy || chatBusy"
      @keydown.enter="sendChat"
    />
    <button
      type="button"
      class="small-action primary"
      :disabled="busy || chatBusy || !chatInput.trim()"
      @click="sendChat"
    >
      <LoaderCircle v-if="chatBusy" class="animate-spin" :size="12" />
      <Send v-else :size="12" />
    </button>
  </div>
  
  <p v-if="chatError" class="mt-1 text-[10px] text-(--fe-error)">{{ chatError }}</p>
</div>
```

### 1.4 前端逻辑（添加到 `<script setup>` 部分）

```typescript
// 角色闲聊状态
const selectedChatCharacter = ref('')
const chatInput = ref('')
const chatBusy = ref(false)
const chatError = ref('')

const chatHistory = computed(() => {
  if (!selectedChatCharacter.value) return []
  const sideChats = (state.value as any).side_chats || {}
  return sideChats[selectedChatCharacter.value] || []
})

async function sendChat() {
  const character = selectedChatCharacter.value
  const message = chatInput.value.trim()
  if (!character || !message || chatBusy.value) return
  
  chatBusy.value = true
  chatError.value = ''
  
  try {
    const result = await sendChatMessage(sessionId.value, character, message)
    chatInput.value = ''
    
    // 更新状态以包含新的聊天记录
    await fetchSessionState(sessionId.value).then(s => { state.value = s })
  } catch (err) {
    chatError.value = String(err)
  } finally {
    chatBusy.value = false
  }
}

// 切换角色时清空输入和错误
watch(selectedChatCharacter, () => {
  chatInput.value = ''
  chatError.value = ''
})
```

---

## 二、Token 实时显示（阶段 E）

### 2.1 后端修改（`core/app.py`）

**修改点 1**: 每次 `_out_send` yield 携带 Token 信息

```python
def _out_send(...):
    """流式输出辅助函数"""
    # 获取当前 Token 使用量
    from core.engine import token_accounting
    usage = token_accounting.get_turn_usage() or {}
    
    yield {
        "narrative": narrative,
        "options": options,
        # ... 其他字段
        "tok_in": usage.get("prompt_tokens", 0),
        "tok_out": usage.get("completion_tokens", 0),
        "tok_total": usage.get("total_tokens", 0),
        "tok_source": "measured",  # 或 "estimated"
        # 分项统计
        "tok_breakdown": {
            "director": usage.get("director", 0),
            "segments": usage.get("segments", 0),
            "options": usage.get("options", 0),
            "polish": usage.get("polish", 0),
            "gate": usage.get("gate", 0),
            "quest": usage.get("quest", 0),
            "chat": usage.get("chat", 0),
            "other": usage.get("other", 0),
        }
    }
```

**修改点 2**: 回合结束时保存 usage 到 agent_meta

```python
# 在 settle_round 或 run_turn 结束时
from core.engine import token_accounting
usage = token_accounting.get_turn_usage()
if usage:
    state.setdefault("agent_meta", {})["usage"] = {
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "total_tokens": usage["total_tokens"],
        "breakdown": {
            "director": usage["director"],
            "segments": usage["segments"],
            "options": usage["options"],
            "polish": usage["polish"],
            "gate": usage["gate"],
            "quest": usage["quest"],
            "chat": usage["chat"],
            "other": usage["other"],
        }
    }
```

**修改点 3**: 回合外端点（chat/send, quest/offer）响应体带 usage

```python
@app.post("/chat/send")
async def send_chat_message(...):
    from core.engine import token_accounting
    
    # 初始化独立使用量跟踪
    token_accounting.init_turn_usage()
    
    result = chat_service.generate_reply(...)
    
    usage = token_accounting.get_turn_usage() or {}
    
    return {
        "reply": result["reply"],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    }
```

### 2.2 前端修改（`frontend/src/App.vue`）

**修改点 1**: 流式更新时实时更新 Token 显示

```typescript
// 在 readNdjson 处理 StreamEvent 时
function handleStreamEvent(event: StreamEvent) {
  // ... 现有逻辑
  
  // 更新 Token 统计（如果存在）
  if (event.tok_in !== undefined) {
    tokenUsage.value.in += event.tok_in
    tokenUsage.value.out += event.tok_out
    tokenUsage.value.lastIn = event.tok_in
    tokenUsage.value.lastOut = event.tok_out
    tokenUsage.value.source = event.tok_source || 'measured'
  }
}
```

**修改点 2**: 显示分项统计（可选，通过 tooltip 或展开）

在现有 Token 显示区域（line ~3081-3088）添加：

```vue
<div class="metric col-span-2 border-t" 
     :title="tokenBreakdownTooltip">
  <span>本局 Token{{ tokenUsage.source === 'measured' ? '' : '（估算）' }}</span>
  <strong class="metric-token">
    入 {{ fmtTok(tokenUsage.in) }} · 出 {{ fmtTok(tokenUsage.out) }}
    <em v-if="tokenUsage.source === 'measured'" class="metric-token-badge">实测</em>
  </strong>
</div>
```

添加 computed:

```typescript
const tokenBreakdownTooltip = computed(() => {
  const breakdown = tokenUsage.value.breakdown || {}
  return `分项统计：
导演 ${breakdown.director || 0}
段卷 ${breakdown.segments || 0}
选项 ${breakdown.options || 0}
润色 ${breakdown.polish || 0}
质量门 ${breakdown.gate || 0}
任务 ${breakdown.quest || 0}
闲聊 ${breakdown.chat || 0}
其他 ${breakdown.other || 0}`
})
```

---

## 三、relay 修复（阶段 C）

### 3.1 修复假入口（`frontend/src/App.vue` line ~1545-1556）

**查找现有代码**:
```typescript
// 搜索 "relay" 相关的假入口
```

**修复方案**: 确保调用 `/directives/append_relay` 真实端点

```typescript
async function addRelayFact(fact: string) {
  const res = await fetch(`/directives/append_relay?session_id=${sessionId.value}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fact })
  })
  if (!res.ok) throw new Error('Failed to append relay fact')
  return res.json()
}
```

### 3.2 后端端点（添加到 `core/app.py`）

```python
@app.post("/directives/append_relay")
async def append_relay_directive(
    session_id: str = Query(...),
    fact: str = Body(..., embed=True)
):
    """永久增补铁律"""
    from core.services import directives_service
    
    state = _load_state(session_id)
    
    try:
        result = directives_service.append_relay_fact(
            state, fact,
            client=_get_client(state),
            model=state.get("model", ""),
            request_kwargs=_get_request_kwargs(state),
            provider=state.get("provider", "deepseek")
        )
        
        _save_state(session_id, state)
        
        return {
            "row": result["row"],
            "text": result["text"],
            "rejected": result["rejected"],
            "count": result["count"]
        }
    except directives_service.DirectiveClientError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

---

## 四、实施优先级

1. **高优先级**（核心功能）:
   - [ ] 角色闲聊 API 端点（后端）
   - [ ] 角色闲聊 UI（前端）
   - [ ] Token 实时显示（流式更新）

2. **中优先级**（增强功能）:
   - [ ] Token 分项统计显示
   - [ ] relay 假入口修复

3. **低优先级**（可选）:
   - [ ] 聊天历史持久化显示
   - [ ] Token 估算优化

---

## 五、测试清单

### 角色闲聊测试
- [ ] 无活跃角色时不显示闲聊区域
- [ ] 下拉框正确列出所有活跃角色
- [ ] 发送消息后收到符合角色 voice 的回复
- [ ] 聊天记录正确保存和显示
- [ ] busy 状态下禁用输入
- [ ] 状态隔离（聊天不影响主剧情）

### Token 显示测试
- [ ] 流式生成期间 Token 数实时跳动
- [ ] 回合结束后显示准确的 Token 总数
- [ ] "实测"徽标正确显示
- [ ] 分项统计 tooltip 显示正确数据

### relay 测试
- [ ] 增补铁律调用真实端点
- [ ] 增补后下回合生效
- [ ] 错误处理正确

---

## 六、注意事项

1. **状态隔离**: 角色闲聊绝不能修改 `history`、`state_memory`、`quest` 等核心状态
2. **busy 状态**: 所有新 UI 元素在 `busy` 时应禁用
3. **错误处理**: 所有 API 调用需要 try-catch 和错误提示
4. **响应式**: 使用 Vue 3 Composition API 的 `ref` 和 `computed`
5. **样式一致**: 复用现有 CSS 类（`field`、`small-action`、`text-xs` 等）

---

## 七、后续工作

完成前端集成后：
1. 本地测试（开发模式）
2. 构建生产版本（`npm run build`）
3. M2/M3 真实测试（需 Kimi API Key）
4. 浏览器全流程测试
5. 文档更新
