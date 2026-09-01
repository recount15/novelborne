import { computed, nextTick, ref, watch } from 'vue'

export type ReaderTone = 'paper' | 'warm' | 'night'
export interface ReaderSettings { fontSize: number; lineHeight: number; contentWidth: number; tone: ReaderTone }
export interface ReaderBookmark { id: string; name: string; chapterIndex: number; scrollRatio: number; createdAt: number; updatedAt: number }
export type BookmarksByBook = Record<string, ReaderBookmark[]>
export type ProgressByBook = Record<string, { chapterIndex: number; scrollRatio: number; updatedAt: number }>

export const READER_SETTINGS_KEY = 'fate-engine-reader-settings-v2'
export const READER_BOOKMARKS_KEY = 'fate-engine-reader-bookmarks-v2'
export const READER_PROGRESS_KEY = 'fate-engine-reader-progress-v1'
export const defaultReaderSettings: ReaderSettings = { fontSize: 18, lineHeight: 2, contentWidth: 720, tone: 'paper' }

function readStorage<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback
  try { const raw = window.localStorage.getItem(key); return raw ? JSON.parse(raw) as T : fallback } catch { return fallback }
}
function writeStorage(key: string, value: unknown): void { try { window.localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage can be unavailable */ } }
/** 把任意输入安全钳制到 [0,1]；NaN/Infinity/字符串数字以外一律归 0，
 *  防止坏书签数据把 NaN 传进滚动定位。 */
function clampRatio(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0
}

function normalizeBookmarks(raw: unknown): BookmarksByBook {
  if (!raw || typeof raw !== 'object') return {}
  const result: BookmarksByBook = {}
  for (const [bookId, value] of Object.entries(raw as Record<string, unknown>)) {
    const list = Array.isArray(value) ? value : [value]
    result[bookId] = list.flatMap((item, index) => {
      if (!item || typeof item !== 'object') return []
      const old = item as Partial<ReaderBookmark> & { chapterIndex?: number; scrollRatio?: number }
      if (typeof old.chapterIndex !== 'number' || !Number.isFinite(old.chapterIndex)) return []
      return [{ id: old.id || `migrated-${bookId}-${index}`, name: old.name || `书签 ${index + 1}`, chapterIndex: Math.trunc(old.chapterIndex), scrollRatio: clampRatio(old.scrollRatio), createdAt: Number.isFinite(old.createdAt) ? old.createdAt as number : Date.now(), updatedAt: Number.isFinite(old.updatedAt) ? old.updatedAt as number : Date.now() }]
    })
  }
  return result
}

export function useReaderState() {
  const settings = ref<ReaderSettings>({ ...defaultReaderSettings, ...readStorage<Partial<ReaderSettings>>(READER_SETTINGS_KEY, {}) })
  // 书签以 localStorage 为单一真源：每次访问时重读，避免 modal 重开后
  // 内存旧状态覆盖外部写入（手写/另一实例）的书签数据。
  const bookmarks = ref<BookmarksByBook>(normalizeBookmarks(readStorage(READER_BOOKMARKS_KEY, {})))
  const progress = ref<ProgressByBook>(readStorage(READER_PROGRESS_KEY, {}))
  watch(settings, value => writeStorage(READER_SETTINGS_KEY, value), { deep: true })
  watch(bookmarks, value => writeStorage(READER_BOOKMARKS_KEY, value), { deep: true })
  watch(progress, value => writeStorage(READER_PROGRESS_KEY, value), { deep: true })

  /** 读取书签快照：本实例内存优先（写入后立刻可读，删除/重命名不会被
   *  尚未落盘的旧 localStorage 快照回读覆盖）；内存为空（刚挂载）时读
   *  localStorage，外部预置数据在首次打开时生效。 */
  function freshBookmarks(): BookmarksByBook {
    const memoryCount = Object.values(bookmarks.value).reduce((s, list) => s + list.length, 0)
    if (memoryCount > 0) return bookmarks.value
    return normalizeBookmarks(readStorage(READER_BOOKMARKS_KEY, {}))
  }
  function bookmarksFor(bookId: string): ReaderBookmark[] {
    return freshBookmarks()[bookId] || []
  }
  function saveProgress(bookId: string, chapterIndex: number, scrollRatio: number): void {
    progress.value = { ...progress.value, [bookId]: { chapterIndex, scrollRatio: clampRatio(scrollRatio), updatedAt: Date.now() } }
  }
  function addBookmark(bookId: string, chapterIndex: number, scrollRatio: number, name?: string): ReaderBookmark {
    bookmarks.value = freshBookmarks()
    const now = Date.now(); const bookmark: ReaderBookmark = { id: `${now}-${Math.random().toString(36).slice(2, 8)}`, name: name?.trim() || `第 ${chapterIndex} 章 · ${Math.round(clampRatio(scrollRatio) * 100)}%`, chapterIndex, scrollRatio: clampRatio(scrollRatio), createdAt: now, updatedAt: now }
    bookmarks.value = { ...bookmarks.value, [bookId]: [...(bookmarks.value[bookId] || []), bookmark] }; return bookmark
  }
  function updateBookmark(bookId: string, id: string, patch: Partial<Pick<ReaderBookmark, 'name' | 'chapterIndex' | 'scrollRatio'>>): void {
    bookmarks.value = freshBookmarks()
    bookmarks.value = { ...bookmarks.value, [bookId]: (bookmarks.value[bookId] || []).map(item => item.id === id ? { ...item, ...patch, updatedAt: Date.now() } : item) }
  }
  function removeBookmark(bookId: string, id: string): void {
    bookmarks.value = freshBookmarks()
    bookmarks.value = { ...bookmarks.value, [bookId]: (bookmarks.value[bookId] || []).filter(item => item.id !== id) }
  }
  return { settings, bookmarks, progress, bookmarksFor, saveProgress, addBookmark, updateBookmark, removeBookmark, resetSettings: () => { settings.value = { ...defaultReaderSettings } } }
}

export function scrollRatioFor(element: HTMLElement): number { const max = element.scrollHeight - element.clientHeight; return max > 0 ? element.scrollTop / max : 0 }
export async function restoreScrollRatio(element: HTMLElement | null, ratio: number): Promise<void> { await nextTick(); if (element) element.scrollTop = Math.max(0, (element.scrollHeight - element.clientHeight) * ratio) }

export function useChapterSearch(text: () => string) {
  const query = ref(''); const matchIndex = ref(0)
  const matches = computed(() => { const q = query.value.trim(); if (!q) return []; const source = text(); const found: number[] = []; let at = 0; while ((at = source.toLocaleLowerCase().indexOf(q.toLocaleLowerCase(), at)) >= 0) { found.push(at); at += Math.max(1, q.length) } return found })
  watch(matches, () => { matchIndex.value = 0 })
  function nextMatch(delta = 1): number { if (!matches.value.length) return -1; matchIndex.value = (matchIndex.value + delta + matches.value.length) % matches.value.length; return matches.value[matchIndex.value] }
  return { query, matches, matchIndex, nextMatch }
}
