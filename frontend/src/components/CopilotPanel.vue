<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Bot, BookOpen, ChevronDown, LoaderCircle, Send, X } from 'lucide-vue-next'
import { apiClient } from '../kernel/apiClient'

export interface CopilotCredentials { provider: string; base_url: string; api_key: string; model: string }
interface CopilotEntry { id: string; label: string; area: string; desc: string }
interface CopilotAction { tool: string; args: Record<string, unknown>; ok: boolean; result: unknown }
interface ChatMessage { role: 'user' | 'assistant'; content: string; actions?: CopilotAction[] }

const props = defineProps<{
  open: boolean
  credentials: CopilotCredentials
  sessionId: string | null
}>()

const emit = defineEmits<{ close: []; entry: [id: string] }>()

const overview = ref<{ snapshot: Record<string, unknown>; entries: CopilotEntry[]; doc_sections: string[] } | null>(null)
const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const error = ref('')
const entriesOpen = ref(true)
const docsQuery = ref('')
const docsLoading = ref(false)
const docsResults = ref<Array<{ title: string; excerpt: string }>>([])
const chatBody = ref<HTMLElement | null>(null)
const docsInput = ref<HTMLInputElement | null>(null)

const inGame = computed(() =>
  Boolean(overview.value?.snapshot && (overview.value.snapshot as { in_game?: boolean }).in_game))

const snapshotLine = ref('')

watch(() => props.open, async (open) => {
  if (!open) return
  error.value = ''
  await refreshOverview()
  if (!messages.value.length) {
    messages.value = [{
      role: 'assistant',
      content: '我是 Copilot 助手：可以汇报当前对局状态、检索用户手册，也可以替你执行存档、准备、托管、导出等操作。下方的功能入口可直接点击。',
    }]
  }
  await nextTick(scrollChat)
})

async function refreshOverview(): Promise<void> {
  try {
    const query = props.sessionId ? `?session_id=${encodeURIComponent(props.sessionId)}` : ''
    const response = await fetch(apiClient.url(`/api/copilot/overview${query}`))
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json() as Partial<{ snapshot: Record<string, unknown>; entries: CopilotEntry[]; doc_sections: string[] }>
    overview.value = {
      snapshot: data.snapshot ?? {},
      entries: data.entries ?? [],
      doc_sections: data.doc_sections ?? [],
    }
    const snap = data.snapshot || {}
    snapshotLine.value = snap.in_game
      ? `${String(snap.mode ?? '')} · 第 ${String(snap.round ?? '?')} 回合 · ${String(snap.chapter ?? '')} 章${snap.work ? ` · ${String(snap.work)}` : ''}`
      : '当前没有进行中的对局（文档与书库功能仍可用）'
  } catch (exc) {
    snapshotLine.value = '状态读取失败，可稍后重试'
    error.value = `读取 Copilot 概览失败：${(exc as Error).message}`
  }
}

async function send(): Promise<void> {
  const text = input.value.trim()
  if (!text || sending.value) return
  input.value = ''
  error.value = ''
  messages.value.push({ role: 'user', content: text })
  sending.value = true
  await nextTick(scrollChat)
  try {
    const history = messages.value.slice(-12).map((m) => ({ role: m.role, content: m.content }))
    const response = await fetch(apiClient.url('/api/copilot/chat'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: history,
        session_id: props.sessionId ?? undefined,
        provider: props.credentials.provider || undefined,
        base_url: props.credentials.base_url || undefined,
        api_key: props.credentials.api_key || undefined,
        model: props.credentials.model || undefined,
      }),
    })
    const data = await response.json().catch(() => ({})) as { answer?: string; actions?: CopilotAction[]; detail?: string }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`)
    messages.value.push({ role: 'assistant', content: String(data.answer || '（空回复）'), actions: data.actions || [] })
    await refreshOverview()
  } catch (exc) {
    error.value = `Copilot 请求失败：${(exc as Error).message}`
  } finally {
    sending.value = false
    await nextTick(scrollChat)
  }
}

async function searchDocs(): Promise<void> {
  const query = docsQuery.value.trim()
  docsLoading.value = true
  error.value = ''
  try {
    const response = await fetch(apiClient.url(`/api/copilot/docs?q=${encodeURIComponent(query)}`))
    const data = await response.json() as { results?: Array<{ title: string; excerpt: string }> }
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    docsResults.value = data.results || []
    if (!docsResults.value.length) docsResults.value = [{ title: '没有匹配的章节', excerpt: '换个关键词试试，例如「开局」「蒸馏」「导出」。' }]
  } catch (exc) {
    error.value = `文档检索失败：${(exc as Error).message}`
  } finally {
    docsLoading.value = false
  }
}

function onEntry(id: string): void {
  if (id === 'copilot_docs') {
    docsInput.value?.focus()
    return
  }
  emit('entry', id)
}

// 回复里的 open_entry 工具结果 → 可点击入口按钮
function entryOf(action: CopilotAction): CopilotEntry | null {
  const result = action.result as { entry?: CopilotEntry } | null
  return result && typeof result === 'object' && result.entry ? result.entry : null
}

function scrollChat(): void {
  const el = chatBody.value
  if (el) el.scrollTop = el.scrollHeight
}
</script>

<template>
  <Transition name="copilot-slide">
    <aside v-if="open" class="copilot-overlay" @click.self="emit('close')">
      <section class="copilot-panel" role="dialog" aria-modal="true" aria-label="Copilot 助手">
        <header class="copilot-head">
          <Bot :size="16" class="text-(--fe-accent)" />
          <div class="min-w-0 flex-1">
            <h2>Copilot 助手</h2>
            <p class="copilot-status" :class="inGame ? 'on' : ''">{{ snapshotLine }}</p>
          </div>
          <button class="copilot-icon" title="关闭" aria-label="关闭" @click="emit('close')"><X :size="16" /></button>
        </header>

        <div class="copilot-entries">
          <button class="copilot-toggle" @click="entriesOpen = !entriesOpen">
            <span>功能入口</span>
            <ChevronDown :size="14" class="copilot-chev" :class="entriesOpen ? 'open' : ''" />
          </button>
          <div v-if="entriesOpen" class="copilot-chips">
            <button
              v-for="entry in overview?.entries || []"
              :key="entry.id"
              type="button"
              class="copilot-chip"
              :title="entry.desc"
              @click="onEntry(entry.id)"
            >{{ entry.label }}</button>
          </div>
        </div>

        <div ref="chatBody" class="copilot-chat">
          <div v-for="(message, i) in messages" :key="i" class="copilot-msg" :class="message.role">
            <p class="copilot-bubble">{{ message.content }}</p>
            <div v-if="message.actions?.length" class="copilot-actions">
              <span
                v-for="action in message.actions"
                :key="action.tool + String(i)"
                class="copilot-action"
                :class="action.ok ? 'ok' : 'fail'"
                :title="JSON.stringify(action.result).slice(0, 200)"
              >{{ action.ok ? '✓' : '✗' }} {{ action.tool }}</span>
              <button
                v-for="action in message.actions.filter((a) => entryOf(a))"
                :key="'entry-' + action.tool"
                type="button"
                class="copilot-entry-btn"
                @click="onEntry(entryOf(action)!.id)"
              >打开「{{ entryOf(action)!.label }}」</button>
            </div>
          </div>
          <div v-if="sending" class="copilot-msg assistant">
            <p class="copilot-bubble copilot-thinking"><LoaderCircle :size="13" class="animate-spin" /> 正在思考与执行…</p>
          </div>
        </div>

        <div class="copilot-docs">
          <div class="copilot-docs-row">
            <input
              ref="docsInput"
              v-model="docsQuery"
              class="copilot-input"
              placeholder="搜索用户手册（如：开局 / 蒸馏 / 导出）"
              @keydown.enter="searchDocs"
            />
            <button type="button" class="copilot-docs-btn" :disabled="docsLoading" @click="searchDocs">
              <LoaderCircle v-if="docsLoading" :size="12" class="animate-spin" />
              <BookOpen v-else :size="12" /> 查手册
            </button>
          </div>
          <div v-if="docsResults.length" class="copilot-docs-results">
            <details v-for="(doc, i) in docsResults" :key="i">
              <summary>{{ doc.title }}</summary>
              <p>{{ doc.excerpt }}</p>
            </details>
          </div>
        </div>

        <p v-if="error" class="copilot-error">{{ error }}</p>

        <footer class="copilot-foot">
          <input
            v-model="input"
            class="copilot-input"
            placeholder="向 Copilot 提问或下指令（如：存档 / 查状态 / 怎么开局）"
            :disabled="sending"
            @keydown.enter.prevent="send"
          />
          <button type="button" class="copilot-send" :disabled="sending || !input.trim()" @click="send">
            <LoaderCircle v-if="sending" :size="14" class="animate-spin" />
            <Send v-else :size="14" />
          </button>
        </footer>
      </section>
    </aside>
  </Transition>
</template>

<style scoped>
.copilot-overlay {
  position: fixed; inset: 0; z-index: 60;
  background: color-mix(in srgb, var(--fe-ink) 22%, transparent);
  display: flex; justify-content: flex-end;
}
.copilot-panel {
  display: flex; flex-direction: column; min-height: 0;
  width: min(420px, 94vw); height: 100%;
  background: var(--fe-panel); border-left: 1px solid var(--fe-border);
  box-shadow: -12px 0 32px color-mix(in srgb, var(--fe-ink) 14%, transparent);
}
.copilot-head {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px; border-bottom: 1px solid var(--fe-border);
}
.copilot-head h2 { font-size: 13px; font-weight: 700; color: var(--fe-ink); }
.copilot-status {
  font-size: 10.5px; color: var(--fe-ink-3); margin-top: 1px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.copilot-status.on { color: color-mix(in srgb, var(--fe-ok) 78%, var(--fe-ink)); }
.copilot-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: 6px; color: var(--fe-ink-3);
  border: none; background: transparent; cursor: pointer;
}
.copilot-icon:hover { background: var(--fe-panel-2); color: var(--fe-ink); }
.copilot-entries { padding: 8px 12px 0; }
.copilot-toggle {
  display: flex; align-items: center; gap: 6px;
  font-size: 10.5px; font-weight: 700; color: var(--fe-ink-3);
  background: none; border: none; padding: 0; cursor: pointer;
}
.copilot-chev { transition: transform 0.15s ease; }
.copilot-chev.open { transform: rotate(180deg); }
.copilot-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.copilot-chip {
  font-size: 11px; padding: 3px 9px; border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--fe-border) 80%, var(--fe-panel));
  background: var(--fe-panel-2); color: var(--fe-ink-2); cursor: pointer;
}
.copilot-chip:hover { border-color: var(--fe-accent); color: var(--fe-accent); }
.copilot-chat { flex: 1; min-height: 0; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.copilot-msg { display: flex; flex-direction: column; }
.copilot-msg.user { align-items: flex-end; }
.copilot-bubble {
  max-width: 88%; white-space: pre-wrap; word-break: break-word;
  font-size: 12px; line-height: 1.6; border-radius: 10px; padding: 7px 10px;
}
.copilot-msg.user .copilot-bubble {
  background: color-mix(in srgb, var(--fe-accent) 14%, var(--fe-panel));
  border: 1px solid color-mix(in srgb, var(--fe-accent) 30%, var(--fe-panel));
}
.copilot-msg.assistant .copilot-bubble {
  background: var(--fe-panel-2); border: 1px solid var(--fe-border);
}
.copilot-thinking { display: inline-flex; align-items: center; gap: 6px; color: var(--fe-ink-3); }
.copilot-actions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.copilot-action {
  font-size: 10px; padding: 2px 7px; border-radius: 999px;
  border: 1px solid var(--fe-border); color: var(--fe-ink-3);
  font-family: var(--fe-mono, monospace);
}
.copilot-action.ok { color: color-mix(in srgb, var(--fe-ok) 80%, var(--fe-ink)); }
.copilot-action.fail { color: var(--fe-danger); }
.copilot-entry-btn {
  font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--fe-accent) 40%, var(--fe-panel));
  background: color-mix(in srgb, var(--fe-accent) 10%, var(--fe-panel));
  color: var(--fe-accent); cursor: pointer;
}
.copilot-docs { padding: 0 12px 6px; border-top: 1px solid var(--fe-border); }
.copilot-docs-row { display: flex; gap: 6px; padding-top: 8px; }
.copilot-docs-btn {
  display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  font-size: 11px; font-weight: 700; padding: 0 10px; height: 30px; border-radius: 6px;
  border: 1px solid var(--fe-border); background: var(--fe-panel-2);
  color: var(--fe-ink-2); cursor: pointer;
}
.copilot-docs-btn:hover:not(:disabled) { border-color: var(--fe-accent); color: var(--fe-accent); }
.copilot-docs-results { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; max-height: 140px; overflow-y: auto; }
.copilot-docs-results details {
  font-size: 11px; border: 1px solid var(--fe-border); border-radius: 6px;
  background: var(--fe-panel-2); padding: 4px 8px;
}
.copilot-docs-results summary { font-weight: 700; cursor: pointer; color: var(--fe-ink-2); }
.copilot-docs-results p { margin: 4px 0 0; color: var(--fe-ink-3); line-height: 1.5; }
.copilot-error {
  margin: 0 12px 6px; font-size: 10.5px; color: var(--fe-danger);
  border: 1px solid color-mix(in srgb, var(--fe-danger) 28%, var(--fe-panel));
  background: color-mix(in srgb, var(--fe-danger) 6%, var(--fe-panel));
  border-radius: 6px; padding: 4px 8px;
}
.copilot-foot { display: flex; gap: 6px; padding: 8px 12px 12px; border-top: 1px solid var(--fe-border); }
.copilot-input {
  flex: 1; min-width: 0; height: 32px; border-radius: 6px;
  border: 1px solid var(--fe-border); background: var(--fe-panel-2);
  color: var(--fe-ink); font-size: 12px; padding: 0 10px;
}
.copilot-input:focus { outline: none; border-color: var(--fe-accent); }
.copilot-send {
  display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
  width: 34px; height: 32px; border-radius: 6px; border: none;
  background: var(--fe-accent); color: var(--fe-accent-ink); cursor: pointer;
}
.copilot-send:disabled { background: var(--fe-panel-3); color: var(--fe-ink-3); }
.copilot-slide-enter-active, .copilot-slide-leave-active { transition: transform 0.2s ease, opacity 0.2s ease; }
.copilot-slide-enter-from, .copilot-slide-leave-to { transform: translateX(24px); opacity: 0; }
</style>
