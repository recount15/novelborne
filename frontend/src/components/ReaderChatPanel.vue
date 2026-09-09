<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { createReaderThread, getReaderRoster, getReaderThread, requestId, sendReaderMessage, type ModelCredentials, type ReaderRoster, type ReaderThread } from '../v3Api'
const props = defineProps<{ bookId: string; chapterNo: number; credentials: ModelCredentials }>()
const emit = defineEmits<{ close: [] }>()
const roster = ref<ReaderRoster | null>(null)
const thread = ref<ReaderThread | null>(null)
const selected = ref('')
const busy = ref(false)
const error = ref('')
const input = ref('')
const messageList = ref<HTMLElement | null>(null)
const composer = ref<HTMLTextAreaElement | null>(null)
let generation = 0
let pending: { message: string; request_id: string } | null = null
async function loadRoster() {
  const request = ++generation
  busy.value = true; error.value = ''; roster.value = null; thread.value = null; selected.value = ''; input.value = ''; pending = null
  try { const loaded = await getReaderRoster(props.bookId, props.chapterNo); if (request === generation) roster.value = loaded }
  catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '人物列表加载失败' }
  finally { if (request === generation) busy.value = false }
}
async function openThread() {
  const character = roster.value?.characters.find(item => item.character_id === selected.value)
  if (!character || !roster.value || busy.value) return
  const request = ++generation
  busy.value = true; error.value = ''; thread.value = null; pending = null; input.value = ''
  try {
    const loaded = await createReaderThread(props.bookId, { character_id: character.character_id, chapter_no: props.chapterNo, source_hash: roster.value.cutoff.source_hash, card_revision: character.card_revision })
    if (request === generation) thread.value = loaded
  }
  catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '阅读对话打开失败' }
  finally { if (request === generation) { busy.value = false; await nextTick(); composer.value?.focus() } }
}
async function send() {
  if (!thread.value || !input.value.trim() || busy.value) return
  const request = generation
  const threadId = thread.value.thread_id
  const message = input.value.trim()
  if (!pending || pending.message !== message) pending = { message, request_id: requestId() }
  busy.value = true; error.value = ''
  try {
    await sendReaderMessage(threadId, { ...props.credentials, ...pending })
    if (request !== generation) return
    const loaded = await getReaderThread(threadId)
    if (request !== generation) return
    thread.value = loaded
    input.value = ''; pending = null
    await nextTick()
    if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
  } catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '发送失败；可重试同一条消息' }
  finally { if (request === generation) { busy.value = false; await nextTick(); composer.value?.focus() } }
}
watch(() => [props.bookId, props.chapterNo], loadRoster, { immediate: true })
onBeforeUnmount(() => { ++generation })
</script>

<template>
  <div class="reader-chat-backdrop" @click.self="emit('close')">
    <section class="reader-chat" role="dialog" aria-modal="true" aria-labelledby="reader-chat-title">
      <header><div><p class="eyebrow">原著阅读 · 独立对话</p><h2 id="reader-chat-title">与书中人物聊天</h2></div><button aria-label="关闭阅读对话" @click="emit('close')">关闭</button></header>
      <p>阅读边界：第 {{ chapterNo }} 章末。只使用此边界内的可验证事实，不推进或修改当前游戏。</p>
      <details v-if="roster"><summary>来源与边界</summary><p>{{ bookId }} · 第 {{ roster.cutoff.chapter_no }} 章 · 字符 {{ roster.cutoff.offset }}</p><p class="identifier">{{ roster.cutoff.source_hash }}</p></details>
      <p v-if="roster?.status === 'preparation_required'" class="notice">当前边界没有可验证的人物卡。请先完成有证据的人物准备；全书证据就绪不等同于人物卡就绪。</p>
      <label v-if="roster?.characters.length">选择人物<select v-model="selected" :disabled="busy" @change="openThread"><option value="" disabled>请选择人物</option><option v-for="character in roster.characters" :key="character.character_id" :value="character.character_id">{{ character.identity.name }} · {{ character.mode === 'interview' ? '最后已知事实访谈' : '阅读对话' }}</option></select></label>
      <p v-if="thread?.mode === 'interview'" class="notice">这是基于已故人物最后已知事实的访谈，不代表复活，也没有死后知识。</p>
      <p v-if="thread">{{ thread.context.identity.name }} · 人物卡修订 {{ thread.context.card_revision }} · 对话由服务端独立保存</p>
      <p v-if="roster && !busy && !roster.characters.length && roster.status !== 'preparation_required'" class="notice">当前阅读边界没有可对话人物。可返回阅读器选择其他章节。</p>
      <p v-if="roster?.characters.length && !selected && !busy" class="empty-state">先选择人物，再开始独立对话。</p>
      <div ref="messageList" class="messages" role="log" aria-label="阅读对话记录" aria-live="polite" :aria-busy="busy"><p v-if="busy" role="status">{{ thread ? '正在等待人物回复并同步记录…' : selected ? '正在打开人物对话…' : '正在加载人物列表…' }}</p><article v-for="message in thread?.messages || []" :key="message.message_id" :class="message.role"><strong>{{ message.role === 'user' ? '你' : thread?.context.identity.name }}</strong><p>{{ message.content }}</p></article><p v-if="thread && !thread.messages.length">可以开始提问；资料不足时，人物会明确说明未知。</p></div>
      <p v-if="error" role="alert" class="notice">{{ error }}</p>
      <button v-if="!roster && !busy" @click="loadRoster">重新加载人物</button>
      <button v-if="roster && selected && !thread && !busy" @click="openThread">重试打开对话</button>
      <form v-if="thread" class="composer" @submit.prevent="send"><textarea ref="composer" v-model="input" maxlength="4000" :disabled="busy" placeholder="向人物提问（最多 4000 字）" aria-label="阅读对话消息" aria-describedby="composer-hint" /><div class="composer-actions"><small id="composer-hint">{{ input.length }} / 4000 · Enter 换行；点击发送</small><button type="submit" :disabled="busy || !input.trim()">{{ busy ? '正在回复…' : error ? '重试发送' : '发送' }}</button></div><p v-if="error" class="notice">输入已保留；重试同一内容沿用请求标识，避免重复提交。</p></form>
    </section>
  </div>
</template>

<style scoped>
.reader-chat-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: 20px; background: color-mix(in srgb, var(--fe-ink) 45%, transparent); }
.reader-chat { width: min(720px, 100%); max-height: 90dvh; overflow: auto; padding: 28px; border-radius: 14px; background: var(--fe-panel); color: var(--fe-ink); box-shadow: 0 16px 60px #0003; font-size: 13px; line-height: 1.8; }
header { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
h2 { margin: 0; font-size: 24px; }
.eyebrow, summary { color: var(--fe-ink-3); }
p { margin: 10px 0; }
.identifier { overflow-wrap: anywhere; font-size: 11px; }
select, textarea { display: block; width: 100%; margin: 10px 0; padding: 10px; background: var(--fe-panel-2); border: 1px solid var(--fe-border); border-radius: 8px; color: var(--fe-ink); }
textarea { min-height: 80px; }
button { padding: 7px 14px; border-radius: 7px; background: var(--fe-accent); color: var(--fe-accent-ink); }
button:disabled { opacity: .5; }
.messages { min-height: 100px; max-height: 35dvh; overflow: auto; margin: 18px 0; }
article { padding: 12px 16px; margin: 12px 0; border-radius: 8px; background: var(--fe-panel-2); white-space: pre-wrap; }
article.user { margin-left: 36px; }
.notice { color: var(--fe-warn); }
.reader-chat{display:flex;flex-direction:column;max-height:calc(100dvh - 40px);min-height:0;overscroll-behavior:contain}.reader-chat>*{flex-shrink:0}.messages{flex:1 1 auto;min-height:100px;overscroll-behavior:contain;overflow-wrap:anywhere}.composer{position:sticky;bottom:-28px;background:var(--fe-panel);padding:10px 0;margin-top:auto;border-top:1px solid var(--fe-border)}.composer textarea{box-sizing:border-box;resize:vertical;max-height:22dvh;margin:0 0 8px}.composer-actions{display:flex;align-items:center;justify-content:space-between;gap:12px}.composer-actions small,.empty-state{color:var(--fe-ink-3)}button{min-height:40px;flex-shrink:0;border:1px solid transparent}button:disabled{cursor:not-allowed}button:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--fe-accent);outline-offset:2px}select{box-sizing:border-box}article p{overflow-wrap:anywhere}
@media(max-width:600px){.reader-chat-backdrop{padding:0}.reader-chat{width:100%;height:100dvh;max-height:100dvh;border-radius:0;padding:16px;box-sizing:border-box;padding-bottom:max(16px,env(safe-area-inset-bottom))}h2{font-size:20px}.composer{bottom:-16px}.composer-actions{flex-wrap:wrap}.composer-actions button{margin-left:auto;min-width:88px}.messages{max-height:none}article.user{margin-left:20px}}
</style>
