<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Anchor, BookOpen, Bookmark, ChevronLeft, ChevronRight, LibraryBig, List, LoaderCircle, Pencil, Search, Settings2, SlidersHorizontal, Trash2, Users, X } from 'lucide-vue-next'
import { fetchUserBook, fetchUserBookChapter, fetchUserBookChapterInsight, listUserBooks } from '../api'
import type { UserBookAnchor, UserBookChapter, UserBookChapterInsight, UserBookDetail, UserBookMeta } from '../types'
import { restoreScrollRatio, scrollRatioFor, useChapterSearch, useReaderState, type ReaderBookmark } from '../composables/useReaderState'
import ReaderBookmarksPanel from './reader/ReaderBookmarksPanel.vue'
import ReaderAnchorsPanel from './reader/ReaderAnchorsPanel.vue'
import ReaderCharactersPanel from './reader/ReaderCharactersPanel.vue'

const emit = defineEmits<{ close: []; 'all-book-search': [query: string]; 'ai-qa': [question: string]; 'reader-event': [event: { type: string; bookId?: string; chapterIndex?: number }] }>()
type Drawer = 'books' | 'chapters' | 'bookmarks' | 'anchors' | 'characters' | null
const { settings, bookmarksFor, saveProgress, addBookmark, updateBookmark, removeBookmark, resetSettings } = useReaderState()
const books = ref<UserBookMeta[]>([]); const activeBook = ref<UserBookDetail | null>(null); const activeChapter = ref<UserBookChapter | null>(null)
const prevChapter = ref<UserBookChapter | null>(null); const nextChapter = ref<UserBookChapter | null>(null)
const chapterInsight = ref<UserBookChapterInsight | null>(null); const insightLoading = ref(false)
const drawer = ref<Drawer>(null); const settingsOpen = ref(false); const searchOpen = ref(false); const searchInput = ref(''); const booksLoading = ref(false); const bookLoading = ref(false); const chapterLoading = ref(false); const error = ref(''); const readerPage = ref<HTMLElement | null>(null); const readerStage = ref<HTMLElement | null>(null)
const bookRequest = ref(0); const listRequest = ref(0); const chapterRequest = ref(0); const chapterTransitionKey = ref(0); let bodyOverflow = ''; let saveTimer: ReturnType<typeof setTimeout> | undefined
const PREVIEW_CHARS = 800
const prevPreviewRef = ref<HTMLElement | null>(null); const currentContentRef = ref<HTMLElement | null>(null); const nextPreviewRef = ref<HTMLElement | null>(null)
function storedProgress(bookId: string): { chapterIndex?: number; scrollRatio?: number } | undefined { try { return (JSON.parse(localStorage.getItem('fate-engine-reader-progress-v1') || '{}') as Record<string, { chapterIndex?: number; scrollRatio?: number }>)[bookId] } catch { return undefined } }
const chapterText = computed(() => activeChapter.value?.text?.replace(/\r\n?/g, '\n') || '')
const prevChapterText = computed(() => prevChapter.value?.text?.replace(/\r\n?/g, '\n') || '')
const nextChapterText = computed(() => nextChapter.value?.text?.replace(/\r\n?/g, '\n') || '')
const search = useChapterSearch(() => chapterText.value)
function processChapterText(text: string, title?: string) { let lines = text.split('\n'); const first = lines.findIndex(line => line.trim()); if (first >= 0 && title && lines[first].trim() === title.trim()) lines.splice(first, 1); return lines.join('\n').replace(/^\s+/, '') }
const displayText = computed(() => processChapterText(chapterText.value, activeChapter.value?.title))
// 预览：上章末尾/下章开头，与当前章相同排版（章号标题+正文字数），不做灰化
const prevPreviewText = computed(() => { const text = processChapterText(prevChapterText.value, prevChapter.value?.title); return text.length > PREVIEW_CHARS ? text.slice(-PREVIEW_CHARS) : text })
const nextPreviewText = computed(() => { const text = processChapterText(nextChapterText.value, nextChapter.value?.title); return text.length > PREVIEW_CHARS ? text.slice(0, PREVIEW_CHARS) : text })
const highlightedText = computed(() => { const value = displayText.value; const q = search.query.value.trim(); if (!q) return [{ text: value, hit: false }]; const parts: Array<{ text: string; hit: boolean }> = []; let at = 0; const low = value.toLocaleLowerCase(); const needle = q.toLocaleLowerCase(); while (true) { const found = low.indexOf(needle, at); if (found < 0) { if (at < value.length) parts.push({ text: value.slice(at), hit: false }); break }; if (found > at) parts.push({ text: value.slice(at, found), hit: false }); parts.push({ text: value.slice(found, found + q.length), hit: true }); at = found + q.length }; return parts })
const chapterPosition = computed(() => activeBook.value && activeChapter.value ? activeBook.value.chapters.findIndex(row => row.index === activeChapter.value?.index) : -1)
const canGoPrevious = computed(() => chapterPosition.value > 0); const canGoNext = computed(() => chapterPosition.value >= 0 && chapterPosition.value < (activeBook.value?.chapters.length ?? 0) - 1)
const chapterProgress = computed(() => chapterPosition.value >= 0 && (activeBook.value?.chapters.length ?? 0) > 1 ? ((chapterPosition.value + 1) / activeBook.value!.chapters.length) * 100 : 0)
const readingProgress = computed(() => activeBook.value ? Math.round((saveRatioForBook(activeBook.value.book_id) || 0) * 100) : 0)
const readerStyle = computed(() => ({ '--reader-font-size': `${settings.value.fontSize}px`, '--reader-line-height': String(settings.value.lineHeight), '--reader-content-width': `${settings.value.contentWidth}px` }))
function saveRatioForBook(bookId: string, chapterIndex?: number): number {
  const progress = storedProgress(bookId)
  return progress && (chapterIndex == null || progress.chapterIndex === chapterIndex) ? progress.scrollRatio || 0 : 0
}
function toggleDrawer(kind: Exclude<Drawer, null>) { settingsOpen.value = false; drawer.value = drawer.value === kind ? null : kind }
function toggleSettings() { drawer.value = null; settingsOpen.value = !settingsOpen.value }
function currentRatio(): number {
  // 章内阅读比例：以当前章正文块的可滚动范围为准（0=章首，1=章末可见底部），
  // 与 scrollChapterTo 的 ratio 恢复公式互为逆运算，保证书签往返同一位置。
  const el = readerPage.value
  const current = currentContentRef.value
  if (!el || !current) return 0
  const top = current.offsetTop
  const scrollable = Math.max(1, current.offsetHeight - el.clientHeight)
  return Math.max(0, Math.min(1, (el.scrollTop - top) / scrollable))
}

// —— 滚动归一：readerPage 是唯一纵向滚动容器（CSS 已约束 stage 不滚动）——
// 章节切换后的定位目标：start=新当前章顶部，end=新当前章底部，ratio=章内比例恢复，
// textAnchor=精确文本锚点（窗口移动时"正在读的那行不动"；basis 指定锚点行落在
// 视口顶部还是底部，向下切章=bottom（阅读前沿），向上切章=top）
type ScrollTarget = 'start' | 'end' | { ratio: number } | { anchorText: string; basis?: 'top' | 'bottom' }
// 程序化定位/章节加载期间抑制滚动事件，防止恢复动作被误判为用户跨章滚动
const suppressScrollEvents = ref(false)
// 三章窗口移动锁：防止连续滚动事件重复触发切章
const windowMoving = ref(false)
// 上次 scrollTop：滚动方向判定基准（scrollChapterTo 定位后同步重置，防误判）
let lastScrollTop = 0
let suppressTimer: ReturnType<typeof setTimeout> | undefined
// 加载锁看门狗：防止任何异常路径永久锁死切章入口
let loadWatchdog: ReturnType<typeof setTimeout> | undefined

/** 视口坐标取文本插入点：caretRangeFromPoint 优先，Firefox/部分 WebView 只有
 *  caretPositionFromPoint，两者都没有时返回 null（调用方降级 start/end）。 */
function caretAt(x: number, y: number): { node: Node; offset: number } | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
  }
  try {
    if (typeof doc.caretRangeFromPoint === 'function') {
      const range = doc.caretRangeFromPoint(x, y)
      if (range) return { node: range.startContainer, offset: range.startOffset }
    }
    if (typeof doc.caretPositionFromPoint === 'function') {
      const pos = doc.caretPositionFromPoint(x, y)
      if (pos) return { node: pos.offsetNode, offset: pos.offset }
    }
  } catch {
    return null
  }
  return null
}

/** 提取滚动容器视口指定基准处的文本片段（精确锚定）。
 *  basis='bottom' 取阅读前沿（视口底部上方一行），basis='top' 取视口顶部。
 *  返回以锚点为中点、前后各取若干字符的文本，用于切章后在正文内做子串定位。 */
function viewportAnchorText(basis: 'top' | 'bottom' = 'bottom'): string | null {
  const el = readerPage.value
  if (!el) return null
  const rect = el.getBoundingClientRect()
  try {
    const x = rect.left + Math.min(rect.width / 2, el.clientWidth * 0.5)
    // bottom 基准：视口底部上方 20px（阅读前沿行）；top 基准：视口顶部下方 20px
    const y = basis === 'bottom'
      ? rect.top + el.clientHeight - 20
      : rect.top + 20
    const caret = caretAt(x, y)
    if (!caret) return null
    const node = caret.node
    if (!node.textContent) return null
    const text = node.textContent
    if (text.trim().length < 20) return null
    const offset = caret.offset
    return text.slice(Math.max(0, offset - 40), Math.min(text.length, offset + 40))
  } catch {
    return null
  }
}

async function scrollChapterTo(target: ScrollTarget) {
  if (!readerPage.value) return
  suppressScrollEvents.value = true
  // 双保险等待 DOM 就绪：nextTick 正常路径 + 超时兜底（组件生命周期异常时
  // nextTick 可能长时间不 resolve，导致 selectChapter 的 finally 永不执行、
  // chapterLoading 锁死，所有切章入口被静默拦截）。
  await Promise.race([
    nextTick(),
    new Promise<void>(resolve => setTimeout(resolve, 60)),
  ])
  const el = readerPage.value
  const current = currentContentRef.value
  if (current) {
    if (target === 'start') el.scrollTop = current.offsetTop
    else if (target === 'end') el.scrollTop = Math.max(0, current.offsetTop + current.offsetHeight - el.clientHeight)
    else if ('anchorText' in target) {
      // 精确文本锚定：在新当前章正文中定位锚点文本的字符偏移 → 像素位置，
      // 让锚点行落在视口底部（与触发切章时的阅读前沿一致，读的那行不动）。
      const textEl = current.querySelector('.reader-text')
      if (textEl && textEl.textContent) {
        const full = textEl.textContent
        const anchor = target.anchorText
        // 取锚点中段 32 字符做子串查找（首尾可能被截断）
        const needle = anchor.slice(24, 56) || anchor
        const at = full.indexOf(needle)
        if (at >= 0) {
          // Vue 高亮搜索会把正文拆成多个文本节点，不能把整段偏移量
          // 直接应用到 firstChild；空节点或拆分节点会触发 Range offset 异常。
          const walker = document.createTreeWalker(textEl, NodeFilter.SHOW_TEXT)
          let node: Node | null = walker.nextNode()
          let cursor = 0
          let anchorNode: Node | null = null
          let anchorOffset = 0
          while (node) {
            const length = node.textContent?.length ?? 0
            if (at >= cursor && at < cursor + length) {
              anchorNode = node
              anchorOffset = Math.max(0, Math.min(length - 1, at - cursor))
              break
            }
            cursor += length
            node = walker.nextNode()
          }
          if (anchorNode) {
            const range = document.createRange()
            range.setStart(anchorNode, anchorOffset)
            range.setEnd(anchorNode, Math.min((anchorNode.textContent?.length ?? 0), anchorOffset + 1))
            const anchorTop = range.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop
            // basis=bottom（向下切章）：锚点行落在视口底部=阅读前沿；
            // basis=top（向上切章）：锚点行落在视口顶部，避免回上一章时定位到下方再次触发边界。
            const basis = target.basis === 'top' ? 'top' : 'bottom'
            el.scrollTop = Math.max(0, basis === 'top'
              ? anchorTop - 40
              : anchorTop - el.clientHeight + 40)
          } else {
            el.scrollTop = current.offsetTop
          }
        } else {
          el.scrollTop = current.offsetTop
        }
      } else {
        el.scrollTop = current.offsetTop
      }
    }
    else {
      // ratio 是当前章内部阅读比例：与 currentRatio() 使用同一"可滚动范围"分母，
      // 保存/恢复互逆，书签往返不会漂移。
      const chapterTop = current.offsetTop
      const scrollable = Math.max(1, current.offsetHeight - el.clientHeight)
      el.scrollTop = Math.max(0, chapterTop + scrollable * Math.max(0, Math.min(1, Number.isFinite(target.ratio) ? target.ratio : 0)))
    }
  }
  // 定位后短暂保持抑制，让浏览器完成重排再放开滚动检测；
  // 同时把 lastScrollTop 同步为程序定位后的实际值，防止 suppress 期间
  // 的惯性/重排滚动事件用历史 scrollTop 误判方向导致连跳两章。
  lastScrollTop = el.scrollTop
  clearTimeout(suppressTimer)
  suppressTimer = setTimeout(() => { suppressScrollEvents.value = false }, 350)
}

function persistPosition() { if (!activeBook.value || !activeChapter.value || suppressScrollEvents.value) return; saveProgress(activeBook.value.book_id, activeChapter.value.index, currentRatio()); clearTimeout(saveTimer); saveTimer = setTimeout(() => emit('reader-event', { type: 'progress', bookId: activeBook.value?.book_id, chapterIndex: activeChapter.value?.index }), 250) }
async function loadBooks(preferredBookId?: string) { const request = ++listRequest.value; booksLoading.value = true; error.value = ''; try { const loaded = await listUserBooks(); if (request !== listRequest.value) return; books.value = loaded; const target = preferredBookId || activeBook.value?.book_id || loaded[0]?.book_id; if (target && loaded.some(b => b.book_id === target)) await selectBook(target) } catch (e) { if (request === listRequest.value) error.value = e instanceof Error ? e.message : '读取原著列表失败' } finally { if (request === listRequest.value) booksLoading.value = false } }
async function selectBook(bookId: string) { if (bookLoading.value || chapterLoading.value) return; persistPosition(); const request = ++bookRequest.value; bookLoading.value = true; error.value = ''; try { const book = await fetchUserBook(bookId); if (request !== bookRequest.value) return; activeBook.value = book; const saved = storedProgress(bookId); const index = saved?.chapterIndex ?? book.chapters[0]?.index; if (index != null) { // 恢复上次阅读：章节号 + 章内滚动比例
    await selectChapter(index, book, saved?.scrollRatio != null ? { ratio: saved.scrollRatio } : 'start') } emit('reader-event', { type: 'book-selected', bookId }) } catch (e) { if (request === bookRequest.value) error.value = e instanceof Error ? e.message : '读取章节目录失败' } finally { if (request === bookRequest.value) bookLoading.value = false } }

async function selectChapter(index: number, bookOverride?: UserBookDetail, scrollTarget: ScrollTarget | null = 'start') {
  const book = bookOverride || activeBook.value
  if (!book || (chapterLoading.value && !bookOverride)) return
  if (!bookOverride) persistPosition()
  const request = ++chapterRequest.value
  chapterLoading.value = true
  insightLoading.value = true
  error.value = ''
  suppressScrollEvents.value = true
  // 加载锁看门狗：任何路径卡死（DOM 等待挂起等）10 秒后强制复位，
  // 保证书签跳转/翻章/目录点击等入口不被永久静默拦截。
  // 只复位本请求的锁：若期间已被更新请求接管，旧看门狗不得解锁新请求。
  clearTimeout(loadWatchdog)
  loadWatchdog = setTimeout(() => {
    if (request !== chapterRequest.value) return
    chapterLoading.value = false
    insightLoading.value = false
    windowMoving.value = false
  }, 10000)
  try {
    const chapters = book.chapters
    const position = chapters.findIndex(ch => ch.index === index)
    const prevMeta = position > 0 ? chapters[position - 1] : null
    const nextMeta = position >= 0 && position < chapters.length - 1 ? chapters[position + 1] : null
    const [chapter, prev, next, insight] = await Promise.all([
      fetchUserBookChapter(book.book_id, index),
      prevMeta ? fetchUserBookChapter(book.book_id, prevMeta.index).catch(() => null) : null,
      nextMeta ? fetchUserBookChapter(book.book_id, nextMeta.index).catch(() => null) : null,
      fetchUserBookChapterInsight(book.book_id, index).catch(() => null),
    ])
    if (request !== chapterRequest.value || activeBook.value?.book_id !== book.book_id) return
    activeChapter.value = chapter
    prevChapter.value = prev
    nextChapter.value = next
    chapterInsight.value = insight
    drawer.value = null
    if (scrollTarget != null) await scrollChapterTo(scrollTarget)
    else { await nextTick(); clearTimeout(suppressTimer); suppressTimer = setTimeout(() => { suppressScrollEvents.value = false }, 350) }
    emit('reader-event', { type: 'chapter-selected', bookId: book.book_id, chapterIndex: index })
  } catch (e) {
    if (request === chapterRequest.value) {
      error.value = e instanceof Error ? e.message : '读取章节失败'
      suppressScrollEvents.value = false
    }
  } finally {
    // 请求序号守卫：旧请求的 finally 不得解锁/改写已被新请求接管的运行状态，
    // 否则旧请求晚返回会提前释放新请求的锁，造成并发切章与定位污染。
    if (request === chapterRequest.value) {
      clearTimeout(loadWatchdog)
      chapterLoading.value = false
      insightLoading.value = false
    }
  }
}

function turn(delta: number, scrollToEnd = false) {
  if (chapterLoading.value || windowMoving.value || !activeBook.value) return
  const position = chapterPosition.value
  if (position < 0) return
  const chapter = activeBook.value.chapters[position + delta]
  if (chapter) void selectChapter(chapter.index, undefined, scrollToEnd ? 'end' : 'start')
}

function pageScroll(direction: 'up' | 'down') {
  if (!readerPage.value || !currentContentRef.value) return
  const el = readerPage.value
  const currentTop = currentContentRef.value.offsetTop
  const currentBottom = currentTop + currentContentRef.value.offsetHeight
  const nextPreview = nextPreviewRef.value
  // 有邻章预览时，先把预览滚完，再切章，避免在当前章末尾直接跳过下文。
  const downBoundary = nextPreview
    ? nextPreview.offsetTop + nextPreview.offsetHeight
    : currentBottom
  const atCurrentTop = el.scrollTop <= currentTop + 10
  const atWindowBottom = el.scrollTop + el.clientHeight >= downBoundary - 10
  if (direction === 'down' && atWindowBottom) { if (canGoNext.value) turn(1); return }
  if (direction === 'up' && atCurrentTop) { if (canGoPrevious.value) turn(-1, true); return }
  el.scrollBy({ top: direction === 'down' ? el.clientHeight * 0.85 : -el.clientHeight * 0.85, behavior: 'smooth' })
}

// 书签创建/重命名/删除全部走书签面板的页面内 UI（bm-form/bm-edit/两步删除），
// 不用 window.prompt/confirm：嵌入式 webview 会静默吞掉原生弹窗（返回 null/false）。
const bookmarkCreating = ref(false)
watch(drawer, value => { if (value !== 'bookmarks') bookmarkCreating.value = false })
function createBookmark() { if (!activeBook.value || !activeChapter.value) return; bookmarkCreating.value = true; settingsOpen.value = false; if (drawer.value !== 'bookmarks') drawer.value = 'bookmarks' }
function commitBookmark(name: string) { if (!activeBook.value || !activeChapter.value) return; addBookmark(activeBook.value.book_id, activeChapter.value.index, currentRatio(), name); bookmarkCreating.value = false }
function renameBookmark(item: ReaderBookmark, name: string) { if (activeBook.value && name.trim()) updateBookmark(activeBook.value.book_id, item.id, { name: name.trim() }) }
function jumpBookmark(item: ReaderBookmark) { if (!activeBook.value || chapterLoading.value || windowMoving.value) return; void selectChapter(item.chapterIndex, undefined, { ratio: item.scrollRatio }) }
function deleteBookmark(item: ReaderBookmark) { if (activeBook.value) removeBookmark(activeBook.value.book_id, item.id) }
function runSearch() { if (search.query.value.trim()) search.nextMatch(1); emit('all-book-search', search.query.value) }
function onKeydown(event: KeyboardEvent) { const target = event.target as HTMLElement | null; const editable = target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName || ''); if (editable) return; if (event.key === 'Escape') { if (drawer.value || settingsOpen.value || searchOpen.value) { drawer.value = null; settingsOpen.value = false; searchOpen.value = false } else emit('close') } else if (event.key === 'ArrowLeft') { event.preventDefault(); turn(-1) } else if (event.key === 'ArrowRight') { event.preventDefault(); turn(1) } else if (event.key === 'ArrowUp') { event.preventDefault(); pageScroll('up') } else if (event.key === 'ArrowDown') { event.preventDefault(); pageScroll('down') } else if (event.key === ' ' || event.key === 'PageDown') { event.preventDefault(); pageScroll('down') } else if (event.key === 'PageUp') { event.preventDefault(); pageScroll('up') } else if (event.key === 'f' || event.key === '/') { event.preventDefault(); searchOpen.value = true; nextTick(() => document.querySelector<HTMLInputElement>('.reader-search input')?.focus()) } }

// —— 三章滑动窗口：滚动进入邻章预览主体后，窗口整体前移/后移 ——
// （lastScrollTop 已在滚动归一区声明，定位后由 scrollChapterTo 同步重置）
let scrollEndTimer: ReturnType<typeof setTimeout> | null = null

/** 按目录位置（而非章节号 ±1）取相邻章号：章节 index 不连续时滚动跨章
 *  不会 fetch 404 或跳错章。 */
function neighborChapterIndex(delta: number): number | null {
  const book = activeBook.value
  if (!book || !activeChapter.value) return null
  const position = book.chapters.findIndex(ch => ch.index === activeChapter.value?.index)
  if (position < 0) return null
  const meta = book.chapters[position + delta]
  return meta ? meta.index : null
}

function onScroll() {
  if (!readerPage.value || !currentContentRef.value) return
  const el = readerPage.value
  const scrollTop = el.scrollTop

  // 锁定期间不要写入 lastScrollTop：程序定位后的基准必须保留，
  // 否则惯性/回弹事件会把下一次方向判定污染成反向，误触发再上一章。
  if (suppressScrollEvents.value || chapterLoading.value || windowMoving.value) return

  const directionDown = scrollTop > lastScrollTop
  const directionUp = scrollTop < lastScrollTop
  lastScrollTop = scrollTop

  const currentTop = currentContentRef.value.offsetTop
  // 阅读前沿=视口底部：只有视口底部深入下章预览过半（快读完预览）才后移窗口，
  // 避免刚开始读预览就切章造成的回跳感。
  const readingFrontier = scrollTop + el.clientHeight
  const nextPreview = nextPreviewRef.value
  const prevPreview = prevPreviewRef.value
  const enteredNext = directionDown && nextPreview && (
    readingFrontier > nextPreview.offsetTop + nextPreview.offsetHeight * 0.6)
  // 向上：视口顶部进入上章预览过半、且已明显离开当前章顶部才前移窗口
  const enteredPrev = directionUp && prevPreview && (
    scrollTop < prevPreview.offsetTop + prevPreview.offsetHeight * 0.4 &&
    scrollTop < currentTop - el.clientHeight * 0.25)

  if (scrollEndTimer) clearTimeout(scrollEndTimer)
  if (!enteredPrev && !enteredNext) {
    scrollEndTimer = setTimeout(() => persistPosition(), 200)
    return
  }
  scrollEndTimer = setTimeout(() => {
    if (suppressScrollEvents.value || chapterLoading.value || windowMoving.value) return
    if (!readerPage.value || !currentContentRef.value || !activeChapter.value) return
    // 触发时复验：120ms 防抖窗口内视口可能已被用户滚回安全区，
    // 用捕获的旧条件切章会造成"我没滚过去却跳章"。
    const el2 = readerPage.value
    const currentTop2 = currentContentRef.value.offsetTop
    const frontier2 = el2.scrollTop + el2.clientHeight
    const nextPreview2 = nextPreviewRef.value
    const prevPreview2 = prevPreviewRef.value
    const stillNext = enteredNext && nextPreview2 && (
      frontier2 > nextPreview2.offsetTop + nextPreview2.offsetHeight * 0.6)
    const stillPrev = enteredPrev && prevPreview2 && (
      el2.scrollTop < prevPreview2.offsetTop + prevPreview2.offsetHeight * 0.4 &&
      el2.scrollTop < currentTop2 - el2.clientHeight * 0.25)
    if (stillNext && canGoNext.value) {
      const nextIndex = neighborChapterIndex(1)
      if (nextIndex == null) return
      // 窗口后移：下一章升为当前章。锚定视口底部正在读的文本，
      // 切章后在新章正文中定位同一段——读的那行保持不动。
      const anchor = viewportAnchorText('bottom')
      const target: ScrollTarget = anchor ? { anchorText: anchor, basis: 'bottom' } : 'start'
      windowMoving.value = true
      void selectChapter(nextIndex, undefined, target).finally(() => {
        windowMoving.value = false
        // 稳定期：切章完成后短暂抑制检测，让惯性滚动结束，
        // 防止视口已深入新下章预览立即再次触发连跳。
        suppressScrollEvents.value = true
        clearTimeout(suppressTimer)
        suppressTimer = setTimeout(() => { suppressScrollEvents.value = false }, 800)
      })
    } else if (stillPrev && canGoPrevious.value) {
      const prevIndex = neighborChapterIndex(-1)
      if (prevIndex == null) return
      // 窗口前移：上一章升为当前章。锚定视口顶部文本并让锚点行落在视口顶部，
      // 回到上一章时阅读位置在视口顶而不是被推到章尾再次触发边界。
      const anchor = viewportAnchorText('top')
      const target: ScrollTarget = anchor ? { anchorText: anchor, basis: 'top' } : 'end'
      windowMoving.value = true
      void selectChapter(prevIndex, undefined, target).finally(() => {
        windowMoving.value = false
        suppressScrollEvents.value = true
        clearTimeout(suppressTimer)
        suppressTimer = setTimeout(() => { suppressScrollEvents.value = false }, 800)
      })
    }
  }, 120)
}

watch(() => settings.value, () => persistPosition(), { deep: true })
onMounted(() => { bodyOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; window.addEventListener('keydown', onKeydown); void loadBooks() })
onBeforeUnmount(() => { persistPosition(); document.body.style.overflow = bodyOverflow; window.removeEventListener('keydown', onKeydown); if (scrollEndTimer) clearTimeout(scrollEndTimer); clearTimeout(suppressTimer) })
</script>

<template>
  <div class="reader-overlay" role="dialog" aria-modal="true" aria-label="我的原著阅读器">
    <section class="reader-shell" :class="`reader-tone-${settings.tone}`" :style="readerStyle">
      <header class="reader-head"><div class="reader-heading"><span class="reader-mark"><BookOpen :size="16" /></span><div class="min-w-0"><p class="reader-overline">我的原著 · {{ readingProgress }}%</p><h2>{{ activeBook?.name || '原著阅读' }}</h2></div></div><div class="reader-actions"><button class="reader-tool" :class="{ active: drawer === 'books' }" title="书库" aria-label="书库" @click="toggleDrawer('books')"><LibraryBig :size="17" /></button><button class="reader-tool" :class="{ active: drawer === 'chapters' }" title="目录" aria-label="目录" @click="toggleDrawer('chapters')"><List :size="18" /></button><button class="reader-tool" :class="{ active: drawer === 'bookmarks' }" title="书签" aria-label="书签" @click="toggleDrawer('bookmarks')"><Bookmark :size="17" /></button><button class="reader-tool" :class="{ active: drawer === 'anchors' }" title="本章锚点" aria-label="本章锚点" @click="toggleDrawer('anchors')"><Anchor :size="17" /></button><button class="reader-tool" :class="{ active: drawer === 'characters' }" title="本章活跃人物" aria-label="本章活跃人物" @click="toggleDrawer('characters')"><Users :size="17" /></button><button class="reader-tool" title="添加书签" aria-label="添加书签" @click="createBookmark"><Bookmark :size="16" /></button><button class="reader-tool" :class="{ active: searchOpen }" title="搜索本章" aria-label="搜索本章" @click="searchOpen = !searchOpen"><Search :size="17" /></button><button class="reader-tool" :class="{ active: settingsOpen }" title="阅读设置" aria-label="阅读设置" @click="toggleSettings"><SlidersHorizontal :size="17" /></button><button class="reader-tool reader-close" title="关闭阅读器" aria-label="关闭阅读器" @click="emit('close')"><X :size="18" /></button></div></header>
      <div v-if="searchOpen" class="reader-search"><Search :size="15" /><input v-model="search.query.value" placeholder="搜索当前章节…" @keydown.enter="runSearch" /><span>{{ search.matches.value.length ? `${search.matchIndex.value + 1} / ${search.matches.value.length}` : '无结果' }}</span><button @click="search.nextMatch(-1)"><ChevronLeft :size="15" /></button><button @click="search.nextMatch(1)"><ChevronRight :size="15" /></button><button title="提交全书搜索" @click="emit('all-book-search', search.query.value)">全书</button></div>
      <div ref="readerStage" class="reader-stage"><Transition name="reader-drawer"><aside v-if="drawer" class="reader-drawer scrollbar" :aria-label="drawer"><template v-if="drawer === 'books'"><div class="drawer-head"><strong>书库</strong><span>{{ books.length }} 本</span></div><div v-if="booksLoading" class="drawer-loading"><LoaderCircle class="animate-spin" :size="18" /> 正在整理书库</div><button v-for="book in books" :key="book.book_id" class="reader-book" :class="{ active: activeBook?.book_id === book.book_id }" @click="selectBook(book.book_id)"><BookOpen :size="15" /><span><strong>{{ book.name }}</strong><small>{{ book.chapter_count }} 章 · {{ book.source_chars.toLocaleString() }} 字</small></span></button></template><template v-else-if="drawer === 'chapters'"><div class="drawer-head"><strong>目录</strong><span>{{ activeBook?.chapter_count ?? 0 }} 章</span></div><button v-for="chapter in activeBook?.chapters ?? []" :key="chapter.index" class="reader-chapter" :class="{ active: activeChapter?.index === chapter.index }" @click="selectChapter(chapter.index)"><span>{{ String(chapter.index).padStart(2, '0') }}</span><strong>{{ chapter.title }}</strong></button></template><template v-else-if="drawer === 'bookmarks'"><ReaderBookmarksPanel :items="activeBook ? bookmarksFor(activeBook.book_id) : []" :creating="bookmarkCreating" :default-name="activeChapter ? `第 ${activeChapter.index} 章` : '书签'" @create="createBookmark" @confirm-create="commitBookmark" @cancel-create="bookmarkCreating = false" @jump="jumpBookmark" @rename="renameBookmark" @remove="deleteBookmark" /></template><template v-else-if="drawer === 'anchors'"><ReaderAnchorsPanel :anchor="chapterInsight?.anchor ?? null" :loading="insightLoading" :current-chapter-index="activeChapter?.index ?? 0" /></template><template v-else><ReaderCharactersPanel :characters="chapterInsight?.characters ?? []" :loading="insightLoading" :current-chapter-index="activeChapter?.index ?? 0" /></template></aside></Transition><Transition name="reader-settings"><aside v-if="settingsOpen" class="reader-settings"><div class="drawer-head"><strong><Settings2 :size="15" /> 阅读设置</strong><button @click="resetSettings">恢复默认</button></div><label>字号 <output>{{ settings.fontSize }} px</output><input v-model.number="settings.fontSize" type="range" min="15" max="26" /></label><label>行距 <output>{{ settings.lineHeight.toFixed(1) }}</output><input v-model.number="settings.lineHeight" type="range" min="1.5" max="2.5" step=".1" /></label><label>版心宽度 <output>{{ settings.contentWidth }} px</output><input v-model.number="settings.contentWidth" type="range" min="580" max="880" step="20" /></label><div class="tone-options"><button v-for="tone in ['paper', 'warm', 'night']" :key="tone" :class="{ active: settings.tone === tone }" @click="settings.tone = tone as typeof settings.tone">{{ tone === 'paper' ? '纸白' : tone === 'warm' ? '护眼' : '夜读' }}</button></div></aside></Transition><main ref="readerPage" class="reader-page scrollbar" @scroll.passive="onScroll"><div class="reader-page-inner"><div v-if="bookLoading && !activeBook" class="reader-state"><LoaderCircle class="animate-spin" :size="24" /> 正在打开原著</div><template v-else-if="activeChapter"><div v-if="prevChapter && prevPreviewText" ref="prevPreviewRef" class="reader-paper reader-preview-prev"><div class="reader-page-title"><small>第 {{ prevChapter.index }} 章</small><h3>{{ prevChapter.title }}</h3><span>{{ prevChapter.chars.toLocaleString() }} 字</span></div><div class="reader-text">{{ prevPreviewText }}</div><div class="reader-end">本章完</div></div><div ref="currentContentRef" class="reader-paper" :class="{ loading: chapterLoading }"><div class="reader-page-title"><small>第 {{ activeChapter.index }} 章</small><h3>{{ activeChapter.title }}</h3><span>{{ activeChapter.chars.toLocaleString() }} 字</span></div><div class="reader-text"> <template v-for="(part, i) in highlightedText" :key="i"><mark v-if="part.hit">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></div><div class="reader-end">本章完</div></div><div v-if="nextChapter" ref="nextPreviewRef" class="reader-paper reader-preview-next"><div class="reader-page-title"><small>第 {{ nextChapter.index }} 章</small><h3>{{ nextChapter.title }}</h3><span>{{ nextChapter.chars.toLocaleString() }} 字</span></div><div class="reader-text">{{ nextPreviewText }}</div><div class="reader-end">本章完</div></div></template><div v-else class="reader-state"><BookOpen :size="26" /><strong>从书库打开原著</strong></div><p v-if="error" class="reader-error">{{ error }}</p></div></main><button v-if="activeChapter" class="page-scroll page-scroll-up" aria-label="向上翻页" title="向上翻页 (↑ / PageUp)" @click="pageScroll('up')"><ChevronLeft :size="20" style="transform: rotate(90deg)" /></button><button v-if="activeChapter" class="page-scroll page-scroll-down" aria-label="向下翻页" title="向下翻页 (↓ / Space / PageDown)" @click="pageScroll('down')"><ChevronRight :size="20" style="transform: rotate(90deg)" /></button><button v-if="canGoPrevious" class="page-turn page-turn-prev" aria-label="上一章" :disabled="chapterLoading" @click="turn(-1)"><ChevronLeft :size="22" /></button><button v-if="canGoNext" class="page-turn page-turn-next" aria-label="下一章" :disabled="chapterLoading" @click="turn(1)"><ChevronRight :size="22" /></button></div>
      <footer class="reader-footer"><span>第 {{ activeChapter?.index ?? '—' }} 章</span><div class="reader-progress"><span :style="{ width: `${chapterProgress}%` }" /></div><span>{{ chapterPosition >= 0 ? `${chapterPosition + 1} / ${activeBook?.chapters.length}` : '' }}</span><button :disabled="!canGoPrevious || chapterLoading" @click="turn(-1)"><ChevronLeft :size="16" /></button><button :disabled="!canGoNext || chapterLoading" @click="turn(1)"><ChevronRight :size="16" /></button></footer>
    </section>
  </div>
</template>

<style scoped>
.reader-overlay{position:fixed;inset:0;z-index:90;background:var(--fe-bg);color:var(--fe-ink)}.reader-shell{display:flex;height:100dvh;flex-direction:column;overflow:hidden;background:var(--reader-stage-bg,var(--fe-bg));--reader-stage-bg:color-mix(in srgb,var(--fe-bg) 88%,white)}.reader-tone-warm{--reader-stage-bg:#e9e1c9;--reader-paper-ink:#443e31}.reader-tone-night{--reader-stage-bg:#15191a;--reader-paper-ink:#d8d8cc}.reader-tone-paper{--reader-paper-ink:var(--fe-ink)}.reader-head,.reader-footer{display:flex;min-height:54px;align-items:center;gap:10px;border-bottom:1px solid var(--fe-border);padding:7px clamp(11px,3vw,34px);background:color-mix(in srgb,var(--fe-panel) 88%,transparent)}.reader-footer{min-height:42px;border-top:1px solid var(--fe-border);border-bottom:0;color:var(--fe-ink-3);font-size:10px}.reader-heading{display:flex;min-width:0;align-items:center;gap:10px;flex:1}.reader-mark{display:grid;width:31px;height:31px;place-items:center;border-radius:50%;background:var(--fe-accent);color:var(--fe-accent-ink)}.reader-overline{margin:0;color:var(--fe-ink-3);font-size:10px}.reader-head h2{margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--fe-font-serif);font-size:15px}.reader-actions{display:flex;gap:3px}.reader-tool,.reader-footer button,.bookmark-row>button{display:grid;width:32px;height:32px;place-items:center;border:1px solid transparent;border-radius:50%;color:var(--fe-ink-2);background:none}.reader-tool:hover,.reader-tool.active,.reader-footer button:hover:not(:disabled){background:var(--fe-accent-soft);color:var(--fe-accent-strong)}.reader-stage{position:relative;min-height:0;flex:1;overflow:hidden}.reader-page{height:100%;padding:clamp(25px,5vh,62px) 82px;overflow-y:auto;overscroll-behavior:contain}.reader-page-inner{width:min(100%,var(--reader-content-width));margin:auto}.reader-paper{color:var(--reader-paper-ink)}.reader-paper:not(.reader-preview-prev):not(.reader-preview-next){animation:page-in 260ms ease both}.reader-paper.loading{opacity:.58}.reader-page-title{text-align:center;padding-bottom:27px}.reader-page-title h3{margin:10px 0 8px;font-family:var(--fe-font-serif);font-size:clamp(22px,3vw,29px)}.reader-page-title small,.reader-page-title span{color:color-mix(in srgb,var(--reader-paper-ink) 56%,transparent);font-size:11px}.reader-text{white-space:pre-wrap;overflow-wrap:break-word;font-family:var(--fe-font-serif);font-size:var(--reader-font-size);line-height:var(--reader-line-height);letter-spacing:.015em}.reader-text mark{border-radius:3px;background:#f4d35e;color:inherit;padding:0 1px}.reader-end{text-align:center;margin:64px 0 16px;color:var(--fe-ink-3)}.reader-preview-prev{margin-bottom:0}.reader-preview-next{margin-top:0}.reader-drawer,.reader-settings{position:absolute;z-index:3;top:16px;bottom:16px;left:max(14px,calc((100% - 1280px)/2));width:min(312px,calc(100% - 28px));overflow-y:auto;border:1px solid var(--fe-border);border-radius:12px;background:color-mix(in srgb,var(--fe-panel) 94%,transparent);box-shadow:var(--fe-shadow-2);backdrop-filter:blur(15px)}.reader-settings{right:max(14px,calc((100% - 1280px)/2));left:auto;padding:14px}.drawer-head{display:flex;min-height:42px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--fe-border);padding:0 14px;font-size:12px}.drawer-head button{border:0;background:none;color:var(--fe-accent-strong);font-size:11px}.reader-book,.reader-chapter{display:flex;width:calc(100% - 16px);align-items:center;gap:9px;margin:5px 8px;border:0;border-radius:8px;padding:9px;text-align:left;background:none;color:var(--fe-ink-2)}.reader-book:hover,.reader-chapter:hover,.reader-book.active,.reader-chapter.active{background:var(--fe-accent-soft)}.reader-book span,.reader-chapter strong{min-width:0}.reader-book strong,.reader-book small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.reader-book small{font-size:10px;color:var(--fe-ink-3)}.reader-chapter>span{display:grid;width:26px;height:26px;place-items:center;border:1px solid var(--fe-border);border-radius:50%;font-size:9px}.reader-settings label{display:grid;grid-template-columns:1fr auto;gap:9px;margin:18px 0;font-size:12px}.reader-settings input{grid-column:1/-1;width:100%;accent-color:var(--fe-accent)}.tone-options{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.tone-options button{height:31px;border:1px solid var(--fe-border);border-radius:7px;background:none}.tone-options .active{border-color:var(--fe-accent);background:var(--fe-accent-soft)}.reader-search{display:flex;align-items:center;gap:6px;border-bottom:1px solid var(--fe-border);padding:7px 14px;background:var(--fe-panel)}.reader-search input{min-width:0;flex:1;border:1px solid var(--fe-border);border-radius:7px;padding:6px 8px;background:transparent;color:inherit}.reader-search button{border:0;background:none;color:var(--fe-ink-2);font-size:11px}.page-turn{position:fixed;z-index:94;top:50%;display:grid;width:39px;height:64px;place-items:center;border:1px solid var(--fe-border);border-radius:9px;background:var(--fe-panel);color:var(--fe-ink-2);opacity:.25;transform:translateY(-50%);box-shadow:var(--fe-shadow-1);transition:opacity 180ms ease}.page-turn:hover,.page-turn:focus-visible{opacity:1}.page-turn-prev{left:18px}.page-turn-next{right:18px}.page-scroll{position:fixed;z-index:94;right:calc(50% - 640px + 22px);display:grid;width:42px;height:42px;place-items:center;border:1px solid var(--fe-border);border-radius:50%;background:var(--fe-panel);color:var(--fe-ink-2);opacity:.4;box-shadow:var(--fe-shadow-1);transition:opacity 180ms ease,transform 120ms ease}.page-scroll:hover{opacity:1;transform:scale(1.08)}.page-scroll:active{transform:scale(0.96)}.page-scroll-up{top:calc(54px + 82px)}.page-scroll-down{bottom:calc(42px + 82px)}@media(max-width:1320px){.page-scroll{right:22px}}.reader-progress{height:2px;min-width:30px;flex:1;background:var(--fe-border)}.reader-progress span{display:block;height:100%;background:var(--fe-accent);transition:width 220ms ease}.reader-footer button{width:28px;height:28px}.reader-state{display:grid;min-height:48vh;place-content:center;justify-items:center;gap:12px;color:var(--fe-ink-3)}.reader-error{text-align:center;color:var(--fe-danger);font-size:12px}.reader-drawer-enter-active,.reader-drawer-leave-active,.reader-settings-enter-active,.reader-settings-leave-active{transition:opacity 150ms ease,transform 150ms ease}.reader-drawer-enter-from,.reader-drawer-leave-to{opacity:0;transform:translateX(-12px)}.reader-settings-enter-from,.reader-settings-leave-to{opacity:0;transform:translateX(12px)}@keyframes page-in{from{opacity:0;transform:perspective(1000px) rotateY(-2deg);transform-origin:left center}to{opacity:1;transform:none}}@media (prefers-reduced-motion:reduce){.reader-paper{animation:none}}@media(max-width:700px){.reader-actions{gap:0}.reader-tool{width:30px;height:30px}.reader-page{padding:27px 23px 38px}.reader-drawer,.reader-settings{top:10px;right:10px;bottom:10px;left:10px;width:auto}.page-turn{display:none}.reader-footer{padding-bottom:max(6px,env(safe-area-inset-bottom));min-height:40px}.reader-mark{width:28px;height:28px}.reader-head h2{max-width:130px;font-size:13px}.reader-search{flex-wrap:wrap}.reader-search input{flex-basis:calc(100% - 28px)}}
</style>
