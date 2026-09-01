<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  BookOpen,
  ChevronDown,
  CircleAlert,
  Download,
  LoaderCircle,
  ScrollText,
  X,
} from 'lucide-vue-next'
import { exportSaveNovel, exportSessionNovel, type ModelConnectionParams } from '../api'
import type { NovelExportResult, SaveMeta } from '../types'

const props = defineProps<{
  sessionId: string | null
  saves: SaveMeta[]
  connection: ModelConnectionParams
}>()

const emit = defineEmits<{
  close: []
}>()

const STYLE_OPTIONS = [
  { value: 'webnovel', label: '网文热血' },
  { value: 'literary', label: '出版文学' },
  { value: 'light', label: '轻松白话' },
  { value: 'faithful', label: '忠实整理' },
] as const

const style = ref(STYLE_OPTIONS[0].value)
const source = ref<'session' | 'save'>(props.sessionId ? 'session' : 'save')
const saveId = ref(props.saves[0]?.save_id ?? '')
const exporting = ref(false)
const errorMessage = ref('')
const failedSegments = ref<string[]>([])
const result = ref<NovelExportResult | null>(null)
const openChapters = ref<Set<number>>(new Set([0]))

const canExport = computed(() => {
  if (exporting.value) return false
  if (source.value === 'session') return Boolean(props.sessionId)
  return Boolean(saveId.value)
})

function extractFailed(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  return raw.map((item, index) => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      const label = record.chapter ?? record.title ?? record.index ?? `第 ${index + 1} 段`
      const reason = record.error ?? record.reason ?? record.message ?? '导出失败'
      return `${label}：${reason}`
    }
    return `第 ${index + 1} 段导出失败`
  })
}

async function startExport(): Promise<void> {
  if (!canExport.value) return
  exporting.value = true
  errorMessage.value = ''
  failedSegments.value = []
  result.value = null
  try {
    const exported = source.value === 'session' && props.sessionId
      ? await exportSessionNovel(props.sessionId, style.value)
      : await exportSaveNovel(saveId.value, style.value, props.connection)
    result.value = exported
    openChapters.value = new Set(exported.chapters?.length ? [0] : [])
  } catch (cause) {
    const raw = cause instanceof Error ? cause.message : '导出失败'
    errorMessage.value = raw
    const failed = (cause as { failed?: unknown })?.failed
    failedSegments.value = extractFailed(failed)
  } finally {
    exporting.value = false
  }
}

const chapters = computed(() => (Array.isArray(result.value?.chapters) ? result.value.chapters : []))
const bookTitle = computed(() => result.value?.manifest?.title || '未命名小说')

function toggleChapter(index: number): void {
  const next = new Set(openChapters.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  openChapters.value = next
}

function downloadMarkdown(): void {
  if (!result.value) return
  const text = result.value.full_text || chapters.value.map((chapter) => `${chapter.title}\n\n${chapter.text}`).join('\n\n')
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${bookTitle.value}.md`
  anchor.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="export-overlay" @click.self="emit('close')">
    <div class="export-modal" role="dialog" aria-label="导出小说">
      <header class="export-header">
        <ScrollText :size="15" class="text-[var(--fe-accent)]" />
        <h2>{{ result ? '导出预览' : '导出小说' }}</h2>
        <button type="button" class="close-btn" title="关闭" @click="emit('close')"><X :size="16" /></button>
      </header>

      <!-- 配置阶段 -->
      <div v-if="!result" class="export-body">
        <label class="block">
          <span class="label">文风</span>
          <select v-model="style" class="field h-10 px-2.5 text-[13px]">
            <option v-for="item in STYLE_OPTIONS" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>

        <div class="mt-3">
          <span class="label">来源</span>
          <div class="flex gap-1.5">
            <button
              type="button"
              class="source-tab"
              :class="source === 'session' ? 'active' : ''"
              :disabled="!sessionId"
              :title="sessionId ? '' : '当前没有对局'"
              @click="source = 'session'"
            >当前对局</button>
            <button
              type="button"
              class="source-tab"
              :class="source === 'save' ? 'active' : ''"
              :disabled="!saves.length"
              :title="saves.length ? '' : '暂无存档'"
              @click="source = 'save'"
            >从存档</button>
          </div>
          <select v-if="source === 'save'" v-model="saveId" class="field mt-2 h-10 px-2.5 text-[13px]">
            <option v-for="meta in saves" :key="meta.save_id" :value="meta.save_id">
              {{ meta.save_id }} · {{ meta.work || meta.novel || '未命名作品' }}
            </option>
          </select>
        </div>

        <div v-if="exporting" class="exporting-box">
          <LoaderCircle class="animate-spin text-[var(--fe-accent)]" :size="22" />
          <p>正在还原情节与风格化…</p>
        </div>

        <div v-if="errorMessage" class="error-banner mt-3">
          <CircleAlert :size="15" class="mt-0.5 shrink-0" />
          <div class="min-w-0 flex-1">
            <p>{{ errorMessage }}</p>
            <ul v-if="failedSegments.length" class="mt-1 list-disc pl-4">
              <li v-for="(segment, index) in failedSegments" :key="index">{{ segment }}</li>
            </ul>
          </div>
        </div>
      </div>

      <!-- 预览阶段 -->
      <div v-else class="preview-body scrollbar">
        <div class="preview-book">
          <h3 class="preview-title"><BookOpen :size="15" class="mr-1.5 inline-block text-[var(--fe-accent)]" />{{ bookTitle }}</h3>
          <p class="preview-meta">{{ chapters.length }} 个章节 · {{ STYLE_OPTIONS.find((item) => item.value === style)?.label || style }}</p>
          <div class="chapter-list">
            <div v-for="(chapter, index) in chapters" :key="index" class="chapter-item">
              <button type="button" class="chapter-toggle" @click="toggleChapter(index)">
                <span class="min-w-0 flex-1 truncate text-left">{{ chapter.title || `第 ${index + 1} 章` }}</span>
                <ChevronDown :size="14" class="chevron shrink-0" :class="openChapters.has(index) ? 'rotate-180' : ''" />
              </button>
              <div v-if="openChapters.has(index)" class="chapter-content">{{ chapter.text }}</div>
            </div>
          </div>
        </div>
      </div>

      <footer class="export-footer">
        <template v-if="!result">
          <span class="mr-auto text-[10px] text-[var(--fe-ink-3)]">分段还原由后端完成，请耐心等待</span>
          <button type="button" class="btn" @click="emit('close')">取消</button>
          <button type="button" class="btn primary" :disabled="!canExport" @click="startExport">
            <LoaderCircle v-if="exporting" class="animate-spin" :size="13" />
            <ScrollText v-else :size="13" /> 开始导出
          </button>
        </template>
        <template v-else>
          <button type="button" class="btn mr-auto" @click="result = null">重新导出</button>
          <button type="button" class="btn" @click="emit('close')">关闭</button>
          <button type="button" class="btn primary" @click="downloadMarkdown">
            <Download :size="13" /> 下载 .md
          </button>
        </template>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.export-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--fe-ink) 45%, transparent);
  padding: 16px;
}
.export-modal {
  display: flex;
  width: min(760px, 100%);
  max-height: calc(100dvh - 48px);
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-bg);
  box-shadow: var(--fe-shadow-2);
}
.export-header {
  display: flex;
  height: 48px;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--fe-border);
  background: var(--fe-panel);
  padding: 0 14px;
}
.export-header h2 { font-size: 13px; font-weight: 800; }
.close-btn { display: grid; width: 28px; height: 28px; margin-left: auto; place-items: center; border-radius: var(--fe-radius); color: var(--fe-ink-3); }
.close-btn:hover { background: var(--fe-panel-2); color: var(--fe-ink); }

.export-body { padding: 18px; }
.source-tab {
  display: inline-flex;
  height: 26px;
  align-items: center;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel-2);
  padding: 0 10px;
  color: var(--fe-ink-2);
  font-size: 11px;
  font-weight: 700;
}
.source-tab.active { border-color: var(--fe-accent); background: var(--fe-panel); color: var(--fe-accent); }
.source-tab:disabled { opacity: .5; }
.exporting-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 36px 0 22px;
  color: var(--fe-ink-2);
  font-family: var(--font-serif);
  font-size: 14px;
  letter-spacing: .12em;
}
.error-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  border: 1px solid color-mix(in srgb, var(--fe-danger) 35%, var(--fe-border));
  border-radius: var(--fe-radius);
  background: color-mix(in srgb, var(--fe-danger) 8%, var(--fe-bg));
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--fe-danger);
}

.preview-body { flex: 1; overflow-y: auto; padding: 18px; }
.preview-book {
  border: 1px solid var(--fe-border);
  border-radius: 3px;
  background: var(--fe-panel);
  padding: 22px clamp(14px, 4vw, 36px);
  box-shadow: inset 20px 0 24px -24px color-mix(in srgb, var(--fe-ink) 25%, transparent);
}
.preview-title {
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: .08em;
  color: var(--fe-ink);
  text-align: center;
}
.preview-meta { margin-top: 4px; text-align: center; font-size: 11px; color: var(--fe-ink-3); }
.chapter-list { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.chapter-item { border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel-2); }
.chapter-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 8px 11px;
  font-family: var(--font-serif);
  font-size: 13px;
  font-weight: 700;
  color: var(--fe-ink);
}
.chapter-toggle .chevron { color: var(--fe-ink-3); transition: transform 180ms ease; }
.chapter-content {
  border-top: 1px solid var(--fe-border);
  padding: 12px 14px;
  font-family: var(--font-serif);
  font-size: 14.5px;
  line-height: 1.9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--fe-ink-2);
}

.export-footer {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid var(--fe-border);
  background: var(--fe-panel);
  padding: 10px 14px;
}
.btn {
  display: inline-flex;
  height: 32px;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel-2);
  padding: 0 12px;
  color: var(--fe-ink-2);
  font-size: 12px;
  font-weight: 700;
  transition: background-color 140ms ease, border-color 140ms ease, transform 100ms ease;
}
.btn:hover:not(:disabled) { border-color: var(--fe-border-strong); }
.btn:active:not(:disabled) { transform: scale(.97); }
.btn.primary { border-color: var(--fe-accent); background: var(--fe-accent); color: var(--fe-accent-ink); }
.btn.primary:hover:not(:disabled) { background: var(--fe-accent-strong); }
.btn:disabled { border-color: var(--fe-border); background: var(--fe-panel-3); color: var(--fe-ink-3); }
</style>
