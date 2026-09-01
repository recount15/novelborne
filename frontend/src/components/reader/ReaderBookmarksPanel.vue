<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Bookmark, BookmarkPlus, Check, Pencil, Trash2, X } from 'lucide-vue-next'
import type { ReaderBookmark } from '../../composables/useReaderState'

const props = defineProps<{
  items: ReaderBookmark[]
  creating: boolean
  defaultName: string
}>()

const emit = defineEmits<{
  create: []
  confirmCreate: [name: string]
  cancelCreate: []
  jump: [item: ReaderBookmark]
  rename: [item: ReaderBookmark, name: string]
  remove: [item: ReaderBookmark]
}>()

const createInput = ref(''); const createField = ref<HTMLInputElement | null>(null)
// 创建/重命名/删除全部走页面内 UI：嵌入式 webview（IAB/WebView2 宿主可配置）会静默吞掉
// window.prompt/confirm（返回 null/false），原生弹窗路径在那里等于按钮失灵。
const editingId = ref<string | null>(null); const editingName = ref(''); const editingField = ref<HTMLInputElement | null>(null)
const pendingRemoveId = ref<string | null>(null); let removeTimer: ReturnType<typeof setTimeout> | undefined

watch(() => props.creating, value => {
  if (value) { createInput.value = props.defaultName; void nextTick(() => createField.value?.focus()) }
})
watch(() => props.items, () => { editingId.value = null; pendingRemoveId.value = null })
onBeforeUnmount(() => clearTimeout(removeTimer))

function submitCreate(): void { emit('confirmCreate', createInput.value.trim() || props.defaultName) }
function startRename(item: ReaderBookmark): void { editingId.value = item.id; editingName.value = item.name; pendingRemoveId.value = null; void nextTick(() => editingField.value?.focus()) }
function submitRename(item: ReaderBookmark): void { const name = editingName.value.trim(); if (name) emit('rename', item, name); editingId.value = null }
function armRemove(item: ReaderBookmark): void {
  if (pendingRemoveId.value === item.id) { pendingRemoveId.value = null; clearTimeout(removeTimer); emit('remove', item); return }
  pendingRemoveId.value = item.id; clearTimeout(removeTimer)
  removeTimer = setTimeout(() => { pendingRemoveId.value = null }, 2600)
}
function formatRatio(ratio: number): string { return `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%` }
</script>

<template>
  <div class="reader-bookmarks-panel">
    <div class="drawer-head">
      <strong><Bookmark :size="14" /> 书签</strong>
      <button type="button" class="bm-create" :disabled="creating" @click="emit('create')">
        <BookmarkPlus :size="13" /> 新建
      </button>
    </div>

    <form v-if="creating" class="bm-form" @submit.prevent="submitCreate">
      <input ref="createField" v-model="createInput" maxlength="40" placeholder="书签名称" aria-label="书签名称" @keydown.esc="emit('cancelCreate')" />
      <button type="submit" class="bm-ok" title="保存书签" aria-label="保存书签"><Check :size="14" /></button>
      <button type="button" class="bm-cancel" title="取消" aria-label="取消新建书签" @click="emit('cancelCreate')"><X :size="14" /></button>
    </form>

    <div v-if="!items.length && !creating" class="bm-empty">
      <Bookmark :size="22" />
      <p>还没有书签</p>
      <small>在任意章节点「新建」，记录你的阅读位置</small>
    </div>

    <div v-else-if="items.length" class="bm-list">
      <div v-for="item in items" :key="item.id" class="bm-card" :class="{ danger: pendingRemoveId === item.id }">
        <button v-if="editingId !== item.id" type="button" class="bm-jump" @click="emit('jump', item)">
          <span class="bm-icon"><Bookmark :size="13" /></span>
          <span class="bm-info">
            <strong class="bm-name">{{ item.name }}</strong>
            <small class="bm-meta">
              <span class="bm-chapter">第 {{ item.chapterIndex }} 章</span>
              <span class="bm-dot">·</span>
              <span class="bm-pos">{{ formatRatio(item.scrollRatio) }}</span>
            </small>
          </span>
        </button>
        <form v-else class="bm-edit" @submit.prevent="submitRename(item)">
          <input ref="editingField" v-model="editingName" maxlength="40" aria-label="重命名书签" @keydown.esc="editingId = null" />
          <button type="submit" class="bm-ok" title="保存名称" aria-label="保存名称"><Check :size="14" /></button>
          <button type="button" class="bm-cancel" title="取消" aria-label="取消重命名" @click="editingId = null"><X :size="14" /></button>
        </form>
        <div v-if="editingId !== item.id" class="bm-actions">
          <button type="button" title="重命名" aria-label="重命名书签" @click="startRename(item)"><Pencil :size="13" /></button>
          <button type="button" :title="pendingRemoveId === item.id ? '再点一次确认删除' : '删除'" aria-label="删除书签" class="bm-remove" @click="armRemove(item)">
            <template v-if="pendingRemoveId === item.id">确认?</template>
            <Trash2 v-else :size="13" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reader-bookmarks-panel{display:flex;flex-direction:column;min-height:0}
.drawer-head{display:flex;min-height:42px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--fe-border);padding:0 14px;font-size:12px}
.drawer-head strong{display:inline-flex;align-items:center;gap:5px}
.bm-create{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--fe-accent);border-radius:7px;padding:4px 9px;background:var(--fe-accent-soft);color:var(--fe-accent-strong);font-size:11px;cursor:pointer;transition:filter 150ms ease}
.bm-create:hover{filter:brightness(1.08)}
.bm-create:disabled{opacity:.5;cursor:default}

.bm-form,.bm-edit{display:flex;align-items:center;gap:5px;border-bottom:1px solid var(--fe-border);padding:8px 12px;background:color-mix(in srgb,var(--fe-accent-soft) 30%,transparent)}
.bm-form input,.bm-edit input{min-width:0;flex:1;border:1px solid var(--fe-border);border-radius:7px;padding:6px 8px;background:var(--fe-panel);color:inherit;font-size:12px}
.bm-form input:focus,.bm-edit input:focus{outline:none;border-color:var(--fe-accent)}
.bm-ok,.bm-cancel{display:grid;width:28px;height:28px;flex-shrink:0;place-items:center;border:1px solid var(--fe-border);border-radius:7px;background:var(--fe-panel);color:var(--fe-ink-2);cursor:pointer;font-size:10px}
.bm-ok{border-color:var(--fe-accent);background:var(--fe-accent-soft);color:var(--fe-accent-strong)}
.bm-ok:hover,.bm-cancel:hover{filter:brightness(1.1)}

.bm-empty{display:grid;min-height:40vh;place-content:center;justify-items:center;gap:8px;color:var(--fe-ink-3);padding:0 20px}
.bm-empty p{margin:0;font-size:13px;font-weight:600}
.bm-empty small{font-size:10px;line-height:1.6;text-align:center}

.bm-list{display:flex;flex-direction:column;gap:6px;padding:10px 10px 14px}
.bm-card{display:flex;align-items:stretch;border:1px solid var(--fe-border);border-radius:10px;background:color-mix(in srgb,var(--fe-panel) 70%,transparent);overflow:hidden;transition:border-color 150ms ease,background 150ms ease}
.bm-card:hover{border-color:var(--fe-accent);background:color-mix(in srgb,var(--fe-accent-soft) 45%,var(--fe-panel))}
.bm-card.danger{border-color:var(--fe-danger)}
.bm-jump{display:flex;flex:1;min-width:0;align-items:center;gap:10px;border:0;padding:9px 10px;text-align:left;background:none;cursor:pointer}
.bm-icon{display:grid;width:28px;height:28px;flex-shrink:0;place-items:center;border-radius:8px;background:var(--fe-accent-soft);color:var(--fe-accent-strong)}
.bm-info{display:flex;flex-direction:column;min-width:0;gap:2px}
.bm-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--fe-ink)}
.bm-meta{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--fe-ink-3)}
.bm-chapter{font-weight:600}
.bm-dot{opacity:.5}

.bm-actions{display:flex;flex-direction:column;justify-content:center;gap:2px;border-left:1px solid var(--fe-border);padding:4px}
.bm-actions button{display:grid;width:26px;height:26px;place-items:center;border:0;border-radius:6px;background:none;color:var(--fe-ink-3);cursor:pointer;font-size:10px;font-weight:600;transition:background 120ms ease,color 120ms ease}
.bm-actions button:hover{background:var(--fe-accent-soft);color:var(--fe-accent-strong)}
.bm-actions .bm-remove:hover{background:color-mix(in srgb,var(--fe-danger) 12%,var(--fe-panel));color:var(--fe-danger)}
.bm-card.danger .bm-remove{background:color-mix(in srgb,var(--fe-danger) 14%,var(--fe-panel));color:var(--fe-danger)}
</style>
