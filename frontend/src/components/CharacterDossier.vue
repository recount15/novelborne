<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Search, UserRound, X } from 'lucide-vue-next'
import { listCharacterLibrary } from '../api'
import type { CharacterLibraryCard } from '../types'

const props = defineProps<{ cardId?: string }>()
const emit = defineEmits<{ close: []; create: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const cards = ref<CharacterLibraryCard[]>([])
const selectedId = ref(props.cardId ?? '')
const query = ref('')
const loading = ref(false)
const error = ref('')
const previousFocus = ref<HTMLElement | null>(null)
const selected = computed(() => cards.value.find(card => card.id === selectedId.value))
const matches = computed(() => cards.value.filter(card => `${card.name} ${card.work ?? ''} ${card.id}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const text = (value: unknown): string => {
  if (Array.isArray(value)) return value.length ? value.map(text).join('、') : '未提供'
  if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}：${text(item)}`).join('\n') || '未提供'
  return value === undefined || value === null || value === '' ? '未提供' : String(value)
}
const originLabel = (origin: string) => ({ built_in: '内置角色', user: '用户角色', override: '用户覆盖版本' }[origin] ?? origin)
async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await listCharacterLibrary()
    cards.value = result.cards
    if (!selectedId.value && cards.value.length) selectedId.value = cards.value[0]!.id
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '人物档案加载失败，请稍后重试。' }
  finally { loading.value = false }
}
watch(() => props.cardId, value => { selectedId.value = value ?? '' })
onMounted(async () => {
  previousFocus.value = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  dialog.value?.showModal()
  void load()
})
onBeforeUnmount(() => { dialog.value?.close(); previousFocus.value?.focus() })
</script>

<template>
  <dialog ref="dialog" class="character-dossier" aria-labelledby="dossier-title" @cancel.prevent="emit('close')" @click="event => { if (event.target === dialog) emit('close') }">
    <div class="dossier-surface">
      <header class="dossier-header"><div><span class="dossier-eyebrow">CHARACTER ARCHIVE · 只读档案</span><h1 id="dossier-title">人物档案</h1></div><button type="button" aria-label="关闭人物档案" autofocus @click="emit('close')"><X :size="20" /></button></header>
      <div class="dossier-actions"><button type="button" @click="emit('create')">设计人物</button><button type="button" :disabled="loading" @click="load">{{ loading ? '读取中…' : '刷新' }}</button></div>
      <div class="dossier-layout" :class="{ 'dossier-layout--empty': !cards.length }">
        <aside v-if="cards.length" class="dossier-index" aria-label="角色目录"><label class="dossier-search"><Search :size="17" /><span class="sr-only">搜索人物、作品或 ID</span><input v-model="query" type="search" placeholder="搜索人物或作品" /></label><p class="dossier-count" role="status">{{ loading ? '正在读取角色库…' : `${matches.length} 位人物` }}</p><nav class="dossier-character-list" aria-label="选择人物"><button v-for="card in matches" :key="card.id" type="button" :aria-current="selectedId === card.id ? 'true' : undefined" @click="selectedId = card.id"><strong>{{ card.name }}</strong><span>{{ card.work || '作品未标注' }} · {{ originLabel(card.origin) }}</span></button></nav><div v-if="!loading && !matches.length" class="dossier-empty"><p>{{ error && !cards.length ? '人物目录暂不可用，请重试。' : cards.length ? '没有匹配的人物，试试其他关键词。' : '角色库暂无可展示的档案。' }}</p><button v-if="query" type="button" @click="query = ''">清除搜索</button></div></aside>
        <div class="dossier-content" :aria-busy="loading">
          <div v-if="error" class="dossier-error" role="alert"><p>{{ error }}</p><button type="button" :disabled="loading" @click="load">重新加载档案</button></div>
          <template v-if="selected">
            <section class="dossier-identity"><div class="dossier-monogram" aria-hidden="true">{{ selected.name.slice(0, 1) }}</div><div><span class="dossier-eyebrow">{{ selected.work || '作品未标注' }}</span><h2>{{ selected.name }}</h2><p>{{ selected.role }} · {{ selected.gender || '性别未标注' }} · {{ originLabel(selected.origin) }}</p></div><dl><dt>角色 ID</dt><dd><code>{{ selected.id }}</code></dd><dt>来源</dt><dd>{{ text(selected.source) }}</dd><dt>媒介 / 地域</dt><dd>{{ text(selected.source_medium) }} / {{ text(selected.source_region) }}</dd></dl></section>
            <section class="dossier-section dossier-facts"><h3>人物底稿 <span>01 / IDENTITY</span></h3><dl><div><dt>人物原型</dt><dd>{{ text(selected.archetype) }}</dd></div><div><dt>原作位置</dt><dd>{{ text(selected.original_position) }}</dd></div><div><dt>核心愿望</dt><dd>{{ text(selected.desire) }}</dd></div><div><dt>深层恐惧</dt><dd>{{ text(selected.fear) }}</dd></div><div><dt>能力</dt><dd>{{ text(selected.abilities) }}</dd></div><div><dt>行动底线</dt><dd>{{ text(selected.unacceptable_actions) }}</dd></div></dl></section>
            <section class="dossier-section dossier-relations"><h3>关系与认知 <span>02 / RELATIONS</span></h3><dl><div><dt>关系向量</dt><dd>{{ text(selected.relationship_vector) }}</dd></div><div><dt>知识范围</dt><dd>{{ text(selected.knowledge_scope) }}</dd></div></dl></section>
            <section class="dossier-section dossier-voice"><h3>说话方式 <span>03 / VOICE</span></h3><p>{{ text(selected.voice) }}</p></section>
            <section class="dossier-section dossier-tags"><h3>角色分类 <span>04 / DOMAINS</span></h3><dl><div><dt>主角类型</dt><dd>{{ text(selected.protagonist_type) }}</dd></div><div><dt>伙伴类型</dt><dd>{{ text(selected.mainline_type) }}</dd></div><div><dt>伴侣类型</dt><dd>{{ text(selected.partner_type) }}</dd></div><div><dt>宿敌类型</dt><dd>{{ text(selected.nemesis_type) }}</dd></div><div><dt>栏位分类</dt><dd>{{ text(selected.slot_keys) }}</dd></div><div><dt>技能 ID</dt><dd>{{ text(selected.skill_ids) }}</dd></div></dl></section>
            <section class="dossier-section dossier-evidence"><h3>档案来源与背景 <span>05 / SOURCE</span></h3><p class="dossier-disclaimer">以下是角色库返回的背景资料，不是原文章节证据。当前接口未提供章节引用或证据坐标。</p><details><summary>展开背景资料</summary><p>{{ text(selected.background) }}</p></details><details><summary>查看来源标识与版本</summary><dl><div><dt>来源标识</dt><dd>{{ text(selected.source) }}</dd></div><div><dt>数据来源</dt><dd>{{ originLabel(selected.origin) }}</dd></div><div><dt>覆盖内置</dt><dd>{{ selected.replaces_built_in ? '是' : '否' }}</dd></div></dl></details></section>
          </template>
          <section v-else-if="!loading && !error" class="dossier-empty"><UserRound :size="36" /><h2>{{ selectedId ? '此角色尚未出现在角色库中' : cards.length ? '选择一位人物，阅读他的故事' : '人物档案，等待你的第一位角色' }}</h2><p v-if="selectedId">请求的角色 ID：<code>{{ selectedId }}</code></p><p>档案只展示现有角色库资料；不会修改角色、生成内容或补造缺失信息。</p><button v-if="!cards.length" type="button" @click="emit('create')">前往设计人物</button></section>
          <p v-else-if="loading" role="status" class="dossier-empty">正在加载真实人物资料…</p>
        </div>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.character-dossier .dossier-header { flex-wrap: nowrap; }
.character-dossier .dossier-header > button { flex-shrink: 0; }
.character-dossier .dossier-actions { margin: 0; padding: 8px 18px; border-bottom: 1px solid var(--fe-border); }
.character-dossier .dossier-layout--empty { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); }
.character-dossier .dossier-empty { border: 0; background: transparent; box-shadow: none; }
.dossier-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.character-dossier { overflow: hidden; overflow-wrap: anywhere; }
.character-dossier button:disabled { opacity: .5; cursor: wait; }
.dossier-identity > div:not(.dossier-monogram) { min-width: 0; max-width: 100%; }
.dossier-identity > dl { grid-template-columns: 90px minmax(0, 1fr) !important; }
.dossier-character-list button strong, .dossier-character-list button span { max-width: 100%; }
.dossier-content { overscroll-behavior: contain; }
@media(max-width:650px) {
  .dossier-header { flex-wrap: wrap; }
  .dossier-actions { margin-left: auto; }
  .dossier-character-list button { max-width: 200px; }
  .dossier-index { min-width: 0; }
}
.character-dossier{padding:0;border:1px solid var(--fe-border);width:min(1060px,calc(100vw - 32px));height:min(840px,calc(100dvh - 40px));max-width:none;max-height:none;color:var(--fe-ink);background:var(--fe-panel);border-radius:var(--fe-radius,10px);margin:auto}.character-dossier::backdrop{background:rgb(0 0 0 / .55)}.dossier-surface{height:100%;display:flex;flex-direction:column}.dossier-header{padding:20px 26px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--fe-border);gap:16px}.dossier-header h1{margin:5px 0 0;font-family:serif;font-size:25px}.dossier-eyebrow{font-size:10px;letter-spacing:.12em;color:var(--fe-ink-3)}.character-dossier button{min-height:44px;min-width:44px;padding:8px 12px;border:1px solid var(--fe-border);border-radius:var(--fe-radius,6px);background:var(--fe-panel);color:inherit;cursor:pointer}.character-dossier button:focus-visible,.character-dossier summary:focus-visible,.dossier-search:focus-within{outline:2px solid var(--fe-accent);outline-offset:2px}.dossier-layout{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:0;flex:1}.dossier-index{padding:20px 16px;border-right:1px solid var(--fe-border);overflow:auto;background:var(--fe-panel-2)}.dossier-search{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--fe-border)}.dossier-search input{min-width:0;width:100%;min-height:44px;background:transparent;border:0;outline:none;color:inherit;font-size:12px}.dossier-count{font-size:11px;color:var(--fe-ink-3)}.dossier-character-list{display:flex;flex-direction:column;gap:6px}.dossier-character-list button{text-align:left;display:flex;flex-direction:column;gap:5px;overflow-wrap:anywhere}.dossier-character-list button[aria-current=true]{border-color:var(--fe-accent);border-left-width:4px}.dossier-character-list span{font-size:10px;color:var(--fe-ink-3)}.dossier-content{min-width:0;overflow:auto;padding:28px 32px}.dossier-identity{display:flex;align-items:center;flex-wrap:wrap;gap:20px}.dossier-monogram{display:grid;place-items:center;width:72px;height:88px;background:var(--fe-panel-2);border:1px solid var(--fe-border);font-family:serif;font-size:40px;color:var(--fe-accent)}.dossier-identity h2{font-family:serif;font-size:32px;margin:8px 0}.dossier-identity p{font-size:12px;color:var(--fe-ink-2)}.dossier-identity>dl{width:100%;font-size:12px;display:grid;grid-template-columns:90px 1fr;gap:8px;margin:0}.dossier-section{padding:22px 0;border-top:1px solid var(--fe-border);margin-top:22px}.dossier-section h3{font-size:15px;margin:0 0 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.dossier-section h3 span{font-size:9px;letter-spacing:.1em;color:var(--fe-ink-3);font-weight:normal}.dossier-section dl{margin:0}.dossier-section dl>div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:12px;margin:10px 0}.character-dossier dt{color:var(--fe-ink-3)}.character-dossier dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.dossier-section p,.dossier-section dl{font-size:13px;line-height:1.9}.dossier-section p{white-space:pre-wrap}.dossier-evidence details{border-bottom:1px solid var(--fe-border)}.dossier-evidence summary{min-height:44px;padding:12px 0;cursor:pointer;font-size:13px}.dossier-disclaimer{color:var(--fe-ink-3)}.dossier-empty{padding:30px 12px;color:var(--fe-ink-2);line-height:1.8;overflow-wrap:anywhere}.dossier-empty h2{font-size:18px}.dossier-error{padding:16px;border-left:3px solid var(--fe-danger);overflow-wrap:anywhere}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}@media(max-width:650px){.character-dossier{width:calc(100vw - 16px);height:calc(100dvh - 16px)}.dossier-header{padding:14px 18px}.dossier-layout{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.dossier-index{max-height:200px;padding:8px 16px;border-right:0;border-bottom:1px solid var(--fe-border)}.dossier-character-list{flex-direction:row;overflow-x:auto}.dossier-character-list button{flex:0 0 160px}.dossier-content{padding:22px 18px}.dossier-count{margin:5px 0}.dossier-section dl>div{grid-template-columns:72px minmax(0,1fr)}}
</style>
