<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BookOpen, ChevronLeft, ChevronRight, LibraryBig, List, LoaderCircle, RefreshCw, Settings2, SlidersHorizontal, X } from 'lucide-vue-next'
import { fetchUserBook, fetchUserBookChapter, listUserBooks } from '../api'
import type { UserBookChapter, UserBookDetail, UserBookMeta } from '../types'

const emit = defineEmits<{ close: [] }>()
type Drawer = 'books' | 'chapters' | null
type Tone = 'paper' | 'warm' | 'night'
interface Settings { fontSize: number; lineHeight: number; contentWidth: number; tone: Tone }
interface Bookmark { chapterIndex: number; scrollRatio: number }
const SETTINGS_KEY = 'fate-engine-reader-settings-v1'
const BOOKMARK_KEY = 'fate-engine-reader-bookmarks-v1'
const defaults: Settings = { fontSize: 18, lineHeight: 2, contentWidth: 720, tone: 'paper' }
function stored<T>(key: string, fallback: T): T {
  try { return JSON.parse(window.localStorage.getItem(key) || JSON.stringify(fallback)) as T } catch { return fallback }
}
const settings = ref<Settings>({ ...defaults, ...stored<Partial<Settings>>(SETTINGS_KEY, {}) })
const bookmarks = ref<Record<string, Bookmark>>(stored<Record<string, Bookmark>>(BOOKMARK_KEY, {}))
const books = ref<UserBookMeta[]>([])
const activeBook = ref<UserBookDetail | null>(null)
const activeChapter = ref<UserBookChapter | null>(null)
const drawer = ref<Drawer>(null)
const settingsOpen = ref(false)
const booksLoading = ref(false)
const bookLoading = ref(false)
const chapterLoading = ref(false)
const error = ref('')
const readerPage = ref<HTMLElement | null>(null)
let bookRequest = 0
let chapterRequest = 0
let bodyOverflow = ''

const chapterPosition = computed(() => activeBook.value && activeChapter.value ? activeBook.value.chapters.findIndex(row => row.index === activeChapter.value?.index) : -1)
const canGoPrevious = computed(() => chapterPosition.value > 0)
const canGoNext = computed(() => chapterPosition.value >= 0 && chapterPosition.value < (activeBook.value?.chapters.length ?? 0) - 1)
const chapterProgress = computed(() => chapterPosition.value >= 0 && (activeBook.value?.chapters.length ?? 0) > 1 ? ((chapterPosition.value + 1) / (activeBook.value?.chapters.length ?? 1)) * 100 : 0)
const readerStyle = computed(() => ({ '--reader-font-size': `${settings.value.fontSize}px`, '--reader-line-height': String(settings.value.lineHeight), '--reader-content-width': `${settings.value.contentWidth}px` }))
const displayText = computed(() => {
  if (!activeChapter.value) return ''
  const lines = activeChapter.value.text.replace(/\r\n?/g, '\n').split('\n')
  const first = lines.findIndex(line => line.trim())
  if (first >= 0 && lines[first].trim() === activeChapter.value.title.trim()) lines.splice(first, 1)
  return lines.join('\n').replace(/^\s+/, '')
})

watch(settings, value => { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(value)) } catch { /* storage may be unavailable */ } }, { deep: true })
watch(bookmarks, value => { try { localStorage.setItem(BOOKMARK_KEY, JSON.stringify(value)) } catch { /* storage may be unavailable */ } }, { deep: true })
function toggleDrawer(kind: Exclude<Drawer, null>): void { settingsOpen.value = false; drawer.value = drawer.value === kind ? null : kind }
function toggleSettings(): void { drawer.value = null; settingsOpen.value = !settingsOpen.value }
function saveScrollPosition(): void {
  const page = readerPage.value
  if (!page || !activeBook.value || !activeChapter.value) return
  const height = page.scrollHeight - page.clientHeight
  bookmarks.value = { ...bookmarks.value, [activeBook.value.book_id]: { chapterIndex: activeChapter.value.index, scrollRatio: height > 0 ? page.scrollTop / height : 0 } }
}
async function restoreScroll(bookId: string, chapterIndex: number): Promise<void> {
  const mark = bookmarks.value[bookId]
  if (!mark || mark.chapterIndex !== chapterIndex) return
  await nextTick()
  const page = readerPage.value
  if (page) page.scrollTop = Math.max(0, (page.scrollHeight - page.clientHeight) * mark.scrollRatio)
}
async function loadBooks(preferredBookId?: string): Promise<void> {
  const request = ++bookRequest
  booksLoading.value = true
  error.value = ''
  try {
    const loaded = await listUserBooks()
    if (request !== bookRequest) return
    books.value = loaded
    const target = preferredBookId ?? activeBook.value?.book_id ?? Object.keys(bookmarks.value)[0] ?? loaded[0]?.book_id
    if (target && loaded.some(book => book.book_id === target)) await selectBook(target)
    else if (!loaded.length) { activeBook.value = null; activeChapter.value = null }
  } catch (cause) { if (request === bookRequest) error.value = cause instanceof Error ? cause.message : '读取原著列表失败' }
  finally { if (request === bookRequest) booksLoading.value = false }
}
async function selectBook(bookId: string): Promise<void> {
  const request = ++bookRequest
  bookLoading.value = true
  error.value = ''
  try {
    const book = await fetchUserBook(bookId)
    if (request !== bookRequest) return
    activeBook.value = book
    const target = book.chapters.find(row => row.index === bookmarks.value[bookId]?.chapterIndex) ?? book.chapters[0]
    if (target) await selectChapter(target.index, book)
    else activeChapter.value = null
  } catch (cause) { if (request === bookRequest) error.value = cause instanceof Error ? cause.message : '读取章节目录失败' }
  finally { if (request === bookRequest) bookLoading.value = false }
}
async function selectChapter(index: number, bookOverride?: UserBookDetail): Promise<void> {
  const book = bookOverride ?? activeBook.value
  if (!book) return
  saveScrollPosition()
  const request = ++chapterRequest
  chapterLoading.value = true
  error.value = ''
  try {
    const chapter = await fetchUserBookChapter(book.book_id, index)
    if (request !== chapterRequest || activeBook.value?.book_id !== book.book_id) return
    activeChapter.value = chapter
    drawer.value = null
    if (readerPage.value) readerPage.value.scrollTop = 0
    await restoreScroll(book.book_id, index)
  } catch (cause) { if (request === chapterRequest) error.value = cause instanceof Error ? cause.message : '读取章节失败' }
  finally { if (request === chapterRequest) chapterLoading.value = false }
}
function turn(delta: number): void {
  if (!activeBook.value) return
  const chapter = activeBook.value.chapters[chapterPosition.value + delta]
  if (chapter) void selectChapter(chapter.index)
}
function onKeydown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (target?.matches('input, select, textarea, button')) return
  if (event.key === 'Escape') {
    if (drawer.value || settingsOpen.value) { drawer.value = null; settingsOpen.value = false } else emit('close')
  } else if (event.key === 'ArrowLeft') { event.preventDefault(); turn(-1) }
  else if (event.key === 'ArrowRight') { event.preventDefault(); turn(1) }
}
onMounted(() => { bodyOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; window.addEventListener('keydown', onKeydown); void loadBooks() })
onBeforeUnmount(() => { saveScrollPosition(); document.body.style.overflow = bodyOverflow; window.removeEventListener('keydown', onKeydown) })
</script>

<template>
  <div class="reader-overlay" role="dialog" aria-modal="true" aria-label="我的原著阅读器">
    <section class="reader-shell" :class="`reader-tone-${settings.tone}`" :style="readerStyle">
      <header class="reader-head">
        <div class="reader-heading"><span class="reader-mark"><BookOpen :size="16" /></span><div class="min-w-0"><p class="reader-overline">我的原著</p><h2 id="reader-title">{{ activeBook?.name || '原著阅读' }}</h2></div></div>
        <div class="reader-actions" aria-label="阅读器工具栏">
          <button class="reader-tool" :class="{ active: drawer === 'books' }" title="书库" aria-label="书库" @click="toggleDrawer('books')"><LibraryBig :size="17" /></button>
          <button class="reader-tool" :class="{ active: drawer === 'chapters' }" title="目录" aria-label="目录" @click="toggleDrawer('chapters')"><List :size="18" /></button>
          <button class="reader-tool" :class="{ active: settingsOpen }" title="阅读设置" aria-label="阅读设置" @click="toggleSettings"><SlidersHorizontal :size="17" /></button>
          <button class="reader-tool" title="刷新书库" aria-label="刷新书库" :disabled="booksLoading" @click="loadBooks(activeBook?.book_id)"><RefreshCw :size="16" :class="{ 'animate-spin': booksLoading }" /></button>
          <button class="reader-tool reader-close" title="关闭阅读器" aria-label="关闭阅读器" @click="emit('close')"><X :size="18" /></button>
        </div>
      </header>
      <div class="reader-stage">
        <Transition name="reader-drawer"><aside v-if="drawer" class="reader-drawer scrollbar" :aria-label="drawer === 'books' ? '书库' : '章节目录'">
          <template v-if="drawer === 'books'"><div class="drawer-head"><strong>书库</strong><span>{{ books.length }} 本</span></div><div v-if="booksLoading && !books.length" class="drawer-loading"><LoaderCircle class="animate-spin" :size="18" /> 正在整理书库</div><button v-for="book in books" :key="book.book_id" class="reader-book" :class="{ active: activeBook?.book_id === book.book_id }" :aria-current="activeBook?.book_id === book.book_id ? 'true' : undefined" @click="selectBook(book.book_id)"><BookOpen :size="15" /><span><strong>{{ book.name }}</strong><small>{{ book.chapter_count }} 章 · {{ book.source_chars.toLocaleString() }} 字</small></span></button><p v-if="!books.length && !booksLoading" class="drawer-empty">尚无已切章原著。请先上传可识别章节的 TXT 文本。</p></template>
          <template v-else><div class="drawer-head"><strong>目录</strong><span>{{ activeBook?.chapter_count ?? 0 }} 章</span></div><div v-if="bookLoading" class="drawer-loading"><LoaderCircle class="animate-spin" :size="18" /> 正在载入目录</div><button v-for="chapter in activeBook?.chapters ?? []" :key="chapter.index" class="reader-chapter" :class="{ active: activeChapter?.index === chapter.index }" :aria-current="activeChapter?.index === chapter.index ? 'page' : undefined" @click="selectChapter(chapter.index)"><span>{{ String(chapter.index).padStart(2, '0') }}</span><strong>{{ chapter.title }}</strong></button><p v-if="activeBook && !activeBook.chapters.length && !bookLoading" class="drawer-empty">这本原著没有可阅读章节。</p><p v-if="!activeBook && !bookLoading" class="drawer-empty">先从书库选择一本原著。</p></template>
        </aside></Transition>
        <Transition name="reader-settings"><aside v-if="settingsOpen" class="reader-settings" aria-label="阅读设置"><div class="drawer-head"><strong><Settings2 :size="15" /> 阅读设置</strong><button @click="settings = { ...defaults }">恢复默认</button></div><label>字号 <output>{{ settings.fontSize }} px</output><input v-model.number="settings.fontSize" type="range" min="15" max="26" step="1" /></label><label>行距 <output>{{ settings.lineHeight.toFixed(1) }}</output><input v-model.number="settings.lineHeight" type="range" min="1.5" max="2.5" step="0.1" /></label><label>版心宽度 <output>{{ settings.contentWidth }} px</output><input v-model.number="settings.contentWidth" type="range" min="580" max="880" step="20" /></label><fieldset><legend>阅读背景</legend><div class="tone-options"><button :class="{ active: settings.tone === 'paper' }" @click="settings.tone = 'paper'">纸白</button><button :class="{ active: settings.tone === 'warm' }" @click="settings.tone = 'warm'">护眼</button><button :class="{ active: settings.tone === 'night' }" @click="settings.tone = 'night'">夜读</button></div></fieldset></aside></Transition>
        <main ref="readerPage" class="reader-page scrollbar" @scroll.passive="saveScrollPosition"><div class="reader-page-inner"><div v-if="bookLoading && !activeBook" class="reader-state"><LoaderCircle class="animate-spin" :size="24" /> 正在打开原著</div><div v-else-if="activeChapter" class="reader-paper" :class="{ loading: chapterLoading }"><div class="reader-page-title"><small>第 {{ activeChapter.index }} 章</small><h3>{{ activeChapter.title }}</h3><span>{{ activeChapter.chars.toLocaleString() }} 字</span></div><div class="reader-text">{{ displayText }}</div><div class="reader-end">本章完</div></div><div v-else-if="booksLoading" class="reader-state"><LoaderCircle class="animate-spin" :size="24" /> 正在整理书库</div><div v-else class="reader-state"><BookOpen :size="26" /><strong>从书库打开原著</strong><span>已上传并成功切章的文本会出现在这里。</span><button @click="toggleDrawer('books')">打开书库</button></div><p v-if="error" class="reader-error"><span>{{ error }}</span><button @click="loadBooks(activeBook?.book_id)">重试</button></p></div></main>
        <button v-if="canGoPrevious" class="page-turn page-turn-prev" title="上一章（左方向键）" aria-label="上一章" :disabled="chapterLoading" @click="turn(-1)"><ChevronLeft :size="22" /></button><button v-if="canGoNext" class="page-turn page-turn-next" title="下一章（右方向键）" aria-label="下一章" :disabled="chapterLoading" @click="turn(1)"><ChevronRight :size="22" /></button>
      </div>
      <footer class="reader-footer"><span class="reader-footer-chapter">{{ activeChapter ? `第 ${activeChapter.index} 章` : '尚未选章' }}</span><div class="reader-progress" aria-label="章节进度"><span :style="{ width: `${chapterProgress}%` }" /></div><span class="reader-footer-progress">{{ chapterPosition >= 0 ? `${chapterPosition + 1} / ${activeBook?.chapters.length ?? 0}` : '' }}</span><div class="reader-footer-nav"><button title="上一章" aria-label="上一章" :disabled="!canGoPrevious || chapterLoading" @click="turn(-1)"><ChevronLeft :size="16" /></button><button title="下一章" aria-label="下一章" :disabled="!canGoNext || chapterLoading" @click="turn(1)"><ChevronRight :size="16" /></button></div></footer>
    </section>
  </div>
</template>

<style scoped>
.reader-overlay { position: fixed; inset: 0; z-index: 90; background: var(--fe-bg); color: var(--fe-ink); }.reader-shell { position: relative; display: flex; min-height: 100dvh; flex-direction: column; overflow: hidden; background: var(--reader-stage-bg, var(--fe-bg)); }.reader-tone-paper { --reader-stage-bg: color-mix(in srgb, var(--fe-bg) 88%, white); --reader-paper-ink: var(--fe-ink); }.reader-tone-warm { --reader-stage-bg: #e9e1c9; --reader-paper-ink: #443e31; }.reader-tone-night { --reader-stage-bg: #15191a; --reader-paper-ink: #d8d8cc; }
.reader-head { display: flex; min-height: 58px; flex: 0 0 auto; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid color-mix(in srgb, var(--fe-border) 75%, transparent); padding: 7px clamp(14px, 3vw, 34px); background: color-mix(in srgb, var(--fe-panel) 88%, transparent); backdrop-filter: blur(12px); }.reader-heading { display: flex; min-width: 0; align-items: center; gap: 10px; }.reader-mark { display: grid; width: 31px; height: 31px; flex: 0 0 auto; place-items: center; border-radius: 50%; background: var(--fe-accent); color: var(--fe-accent-ink); }.reader-overline { margin: 0 0 1px; color: var(--fe-ink-3); font-size: 10px; font-weight: 700; letter-spacing: .08em; }.reader-head h2 { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--fe-font-serif); font-size: 15px; }.reader-actions { display: flex; flex: 0 0 auto; gap: 3px; }.reader-tool, .reader-footer-nav button { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid transparent; border-radius: 50%; color: var(--fe-ink-2); }.reader-tool:hover, .reader-tool.active, .reader-footer-nav button:hover:not(:disabled) { border-color: color-mix(in srgb, var(--fe-accent) 42%, transparent); background: var(--fe-accent-soft); color: var(--fe-accent-strong); }.reader-tool:disabled, .reader-footer-nav button:disabled { cursor: not-allowed; opacity: .42; }.reader-close:hover { background: color-mix(in srgb, var(--fe-danger) 12%, transparent); color: var(--fe-danger); }
.reader-stage { position: relative; min-height: 0; flex: 1; overflow: hidden; }.reader-page { height: 100%; overflow-y: auto; overscroll-behavior: contain; padding: clamp(25px, 5vh, 62px) 82px; }.reader-page-inner { width: min(100%, var(--reader-content-width)); margin: 0 auto; }.reader-paper { position: relative; min-height: calc(100vh - 180px); color: var(--reader-paper-ink); }.reader-paper.loading { opacity: .58; }.reader-page-title { padding-bottom: 27px; text-align: center; }.reader-page-title small, .reader-page-title span { color: color-mix(in srgb, var(--reader-paper-ink) 56%, transparent); font-size: 11px; }.reader-page-title h3 { margin: 10px 0 8px; font-family: var(--fe-font-serif); font-size: clamp(22px, 3vw, 29px); line-height: 1.35; }.reader-text { margin-top: 24px; white-space: pre-wrap; overflow-wrap: break-word; font-family: var(--fe-font-serif); font-size: var(--reader-font-size); line-height: var(--reader-line-height); letter-spacing: .015em; text-wrap: pretty; }.reader-end { margin: 64px 0 16px; color: color-mix(in srgb, var(--reader-paper-ink) 46%, transparent); text-align: center; font-family: var(--fe-font-serif); font-size: 13px; letter-spacing: .18em; }.reader-state { display: grid; min-height: 48vh; place-content: center; justify-items: center; gap: 12px; color: var(--fe-ink-3); text-align: center; font-size: 13px; }.reader-state strong { color: var(--fe-ink-2); font-family: var(--fe-font-serif); font-size: 18px; }.reader-state button, .reader-error button { border: 1px solid var(--fe-border); border-radius: var(--fe-radius); padding: 7px 11px; color: var(--fe-accent-strong); font-size: 12px; }.reader-state button:hover, .reader-error button:hover { border-color: var(--fe-accent); background: var(--fe-accent-soft); }.reader-error { display: flex; justify-content: center; gap: 10px; margin: 22px 0; color: var(--fe-danger); font-size: 12px; }
.reader-drawer, .reader-settings { position: absolute; z-index: 3; top: 16px; bottom: 16px; left: max(14px, calc((100% - 1280px) / 2)); width: min(312px, calc(100% - 28px)); overflow-y: auto; border: 1px solid var(--fe-border); border-radius: calc(var(--fe-radius) + 3px); background: color-mix(in srgb, var(--fe-panel) 94%, transparent); box-shadow: var(--fe-shadow-2); backdrop-filter: blur(15px); }.reader-settings { right: max(14px, calc((100% - 1280px) / 2)); left: auto; padding: 14px; }.drawer-head { display: flex; min-height: 42px; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--fe-border); padding: 0 14px; color: var(--fe-ink-2); font-size: 12px; }.drawer-head strong { display: flex; align-items: center; gap: 6px; }.drawer-head span, .drawer-head button { color: var(--fe-ink-3); font-size: 10px; }.reader-book, .reader-chapter { display: flex; width: calc(100% - 16px); min-width: 0; align-items: center; gap: 9px; margin: 5px 8px; border-radius: var(--fe-radius); padding: 9px; text-align: left; color: var(--fe-ink-2); }.reader-book:hover, .reader-chapter:hover { background: color-mix(in srgb, var(--fe-accent-soft) 58%, transparent); }.reader-book.active, .reader-chapter.active { background: var(--fe-accent-soft); color: var(--fe-accent-strong); }.reader-book > span { min-width: 0; }.reader-book strong, .reader-book small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.reader-book strong { font-size: 12px; }.reader-book small { margin-top: 3px; color: var(--fe-ink-3); font-size: 10px; }.reader-chapter > span { display: grid; width: 26px; height: 26px; flex: 0 0 auto; place-items: center; border: 1px solid var(--fe-border); border-radius: 50%; font-size: 9px; font-weight: 800; }.reader-chapter strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }.drawer-loading, .drawer-empty { display: grid; justify-items: center; gap: 8px; padding: 34px 20px; color: var(--fe-ink-3); text-align: center; font-size: 11px; line-height: 1.7; }.reader-settings label { display: grid; grid-template-columns: 1fr auto; gap: 9px; margin: 18px 0; color: var(--fe-ink-2); font-size: 12px; }.reader-settings output { color: var(--fe-accent-strong); font-size: 11px; }.reader-settings input { grid-column: 1 / -1; width: 100%; accent-color: var(--fe-accent); }.reader-settings fieldset { margin: 18px 0 4px; border: 0; padding: 0; }.reader-settings legend { margin-bottom: 9px; color: var(--fe-ink-2); font-size: 12px; }.tone-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }.tone-options button { height: 31px; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); color: var(--fe-ink-2); font-size: 11px; }.tone-options button.active { border-color: var(--fe-accent); background: var(--fe-accent-soft); color: var(--fe-accent-strong); }
.page-turn { position: absolute; z-index: 2; top: 50%; display: grid; width: 39px; height: 64px; place-items: center; border: 1px solid color-mix(in srgb, var(--fe-border) 72%, transparent); border-radius: 9px; background: color-mix(in srgb, var(--fe-panel) 82%, transparent); color: var(--fe-ink-2); opacity: .2; transform: translateY(-50%); }.reader-stage:hover .page-turn, .page-turn:focus-visible { opacity: 1; }.page-turn:hover { background: var(--fe-accent-soft); color: var(--fe-accent-strong); }.page-turn:disabled { opacity: .2; }.page-turn-prev { left: 18px; }.page-turn-next { right: 18px; }.reader-footer { display: flex; min-height: 42px; flex: 0 0 auto; align-items: center; gap: 12px; border-top: 1px solid color-mix(in srgb, var(--fe-border) 75%, transparent); padding: 0 clamp(14px, 3vw, 34px); background: color-mix(in srgb, var(--fe-panel) 88%, transparent); color: var(--fe-ink-3); font-size: 10px; }.reader-footer-chapter { min-width: 52px; }.reader-progress { height: 2px; min-width: 30px; flex: 1; background: color-mix(in srgb, var(--fe-border) 70%, transparent); }.reader-progress span { display: block; height: 100%; background: var(--fe-accent); transition: width 220ms ease; }.reader-footer-progress { min-width: 36px; text-align: right; }.reader-footer-nav { display: flex; gap: 2px; }.reader-footer-nav button { width: 28px; height: 28px; }.reader-drawer-enter-active, .reader-drawer-leave-active, .reader-settings-enter-active, .reader-settings-leave-active { transition: opacity 150ms ease, transform 150ms ease; }.reader-drawer-enter-from, .reader-drawer-leave-to { opacity: 0; transform: translateX(-12px); }.reader-settings-enter-from, .reader-settings-leave-to { opacity: 0; transform: translateX(12px); }
@media (max-width: 700px) { .reader-head { min-height: 54px; padding: 6px 11px; }.reader-mark { width: 28px; height: 28px; }.reader-head h2 { max-width: 120px; font-size: 13px; }.reader-overline { font-size: 9px; }.reader-tool { width: 31px; height: 31px; }.reader-page { padding: 27px 23px 38px; }.reader-paper { min-height: calc(100dvh - 150px); }.reader-page-title h3 { font-size: 23px; }.reader-text { margin-top: 20px; font-size: calc(var(--reader-font-size) - 1px); }.reader-drawer, .reader-settings { top: 10px; right: 10px; bottom: 10px; left: 10px; width: auto; }.page-turn { display: none; }.reader-footer { min-height: 40px; gap: 8px; padding: 0 11px; }.reader-footer-chapter { display: none; } }
@media (max-width: 380px) { .reader-tool { width: 29px; }.reader-page { padding-right: 19px; padding-left: 19px; }.reader-mark { display: none; } }
</style>
