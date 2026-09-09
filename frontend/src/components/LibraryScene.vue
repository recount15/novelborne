<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BookOpen, RefreshCw, Search } from 'lucide-vue-next'
import { listPlayableBooks, listUserBooks } from '../api'
import type { PlayableBook, UserBookMeta } from '../types'

const emit = defineEmits<{ close: []; upload: []; 'open-book': [payload: { bookId: string }]; 'open-characters': [] }>()
const books = ref<UserBookMeta[]>([])
const playable = ref<PlayableBook[]>([])
const loading = ref(false)
const errors = ref<string[]>([])
const query = ref('')
const filter = ref('all')
const filters = [{ id: 'all', label: '全部原著' }, { id: 'ready', label: '已准备' }, { id: 'played', label: '已玩作品' }]
const entries = computed(() => {
  const merged = new Map<string, { id: string; title: string; chapters?: number; chars?: number; ready?: boolean; played?: boolean; mode?: string }>()
  for (const book of books.value) merged.set(book.book_id, { id: book.book_id, title: book.name, chapters: book.chapter_count, chars: book.source_chars })
  for (const book of playable.value) merged.set(book.book_id, { ...merged.get(book.book_id), id: book.book_id, title: merged.get(book.book_id)?.title || book.title, ready: book.ready, played: book.played, mode: book.mode })
  return [...merged.values()]
})
const visible = computed(() => entries.value.filter(book => (filter.value === 'all' || (filter.value === 'ready' ? book.ready : book.played)) && `${book.title} ${book.id}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
async function load() {
  if (loading.value) return
  loading.value = true
  errors.value = []
  const results = await Promise.allSettled([listUserBooks(), listPlayableBooks()])
  const [source, played] = results
  if (source.status === 'fulfilled') books.value = source.value
  else { books.value = []; errors.value.push(`原著目录加载失败：${source.reason instanceof Error ? source.reason.message : '请稍后重试'}`) }
  if (played.status === 'fulfilled') playable.value = played.value
  else { playable.value = []; errors.value.push(`已玩作品状态加载失败：${played.reason instanceof Error ? played.reason.message : '请稍后重试'}`) }
  loading.value = false
}
onMounted(load)
</script>

<template>
  <main class="library-scene" aria-labelledby="library-title" :aria-busy="loading">
    <header class="library-header">
      <div class="library-actions"><button type="button" @click="emit('upload')">上传原著</button><button type="button" :disabled="loading" @click="load"><RefreshCw :size="17" /> 刷新目录</button></div>
    </header>
    <div class="library-intro"><span class="library-eyebrow">YOUR READING ROOM · 私人藏书室</span><h1 id="library-title" class="library-title">书页之间，另一个世界。</h1><p>找回一部原著，或重访已经走入的故事。这里仅浏览藏书，不会启动准备或生成。</p></div>
    <div class="library-tools">
      <label class="library-search"><Search :size="18" /><span class="sr-only">搜索书名或作品 ID</span><input v-model="query" type="search" placeholder="搜索书名或作品 ID" /></label>
      <div class="library-filters" role="group" aria-label="藏书筛选"><button v-for="item in filters" :key="item.id" type="button" :aria-pressed="filter === item.id" @click="filter = item.id">{{ item.label }}</button></div>
    </div>
    <div class="library-status" role="status">{{ loading ? '正在读取藏书目录…' : `显示 ${visible.length} / ${entries.length} 部作品` }}<span>准备状态仅展示已玩作品接口已验证的数据；未返回不代表未准备。</span></div>
    <div v-if="errors.length" class="library-error" role="alert"><p v-for="message in errors" :key="message">{{ message }}</p><button type="button" :disabled="loading" @click="load">重新加载</button></div>
    <div v-if="visible.length" class="library-grid">
      <article v-for="(book, index) in visible" :key="book.id" class="library-book">
        <div class="library-book-page"><span class="library-book-number">{{ String(index + 1).padStart(2, '0') }} / 原著</span><BookOpen :size="25" aria-hidden="true" /><h2>{{ book.title }}</h2><span class="library-book-imprint">书中织梦 · 藏书</span></div>
        <div class="library-meta"><div class="library-book-badges"><span>{{ book.played ? '已玩' : '已收录' }}</span><span>{{ book.ready === true ? '准备已验证' : book.ready === false ? '准备未就绪' : '准备状态未提供' }}</span></div><p v-if="book.chapters !== undefined">{{ book.chapters }} 章 · {{ (book.chars ?? 0).toLocaleString() }} 字</p><p v-if="book.mode">{{ book.mode === 'fullbook' ? '全书准备' : '窗口准备' }}</p><code :title="book.id">{{ book.id }}</code></div>
        <div class="library-actions"><button type="button" :aria-label="`阅读《${book.title}》`" @click="emit('open-book', { bookId: book.id })"><BookOpen :size="17" /> 打开原著</button></div>
      </article>
    </div>
    <section v-else-if="!loading" class="library-empty"><BookOpen :size="36" /><h2>{{ errors.length && !entries.length ? '目录暂不可用' : entries.length ? '没有符合条件的作品' : '你的藏书室，等待第一部故事' }}</h2><p>{{ errors.length && !entries.length ? '未能取得完整目录，不代表书库为空。请重新加载，或返回工作台检查服务连接。' : entries.length ? '试试其他书名，或切换到全部原著。' : '上传自己的 TXT 原著，开启第一段阅读。这里只展示真实目录，不会创建示例藏书。' }}</p><button v-if="entries.length" type="button" @click="query = ''; filter = 'all'">清除筛选</button><button v-else-if="!errors.length" type="button" @click="emit('upload')">上传第一部原著</button><button v-else type="button" @click="emit('close')">返回工作台</button></section>
  </main>
</template>

<style scoped>
.library-scene .library-header { justify-content: flex-end; }
.library-scene .library-header .library-actions { flex-wrap: nowrap; }
.library-scene .library-intro { padding-top: 24px; }
@media(max-width:600px) {
  main.library-scene { padding-top: 12px; }
  .library-scene .library-intro { padding: 16px 0; }
  .library-scene .library-title { margin: 8px 0; font-size: 27px; }
  .library-scene .library-intro p { margin: 0; }
  .library-scene .library-status { gap: 4px; margin: 12px 0; }
  .library-scene .library-header .library-actions { width: 100%; }
  .library-scene .library-header button { flex: 1; }
}
.library-scene { overflow-wrap: anywhere; }
.library-search svg, .library-scene button svg { flex-shrink: 0; }
.library-grid { grid-template-columns: repeat(auto-fill, minmax(min(220px, 100%), 1fr)); }
@media(max-width:600px) { .library-scene .library-grid { grid-template-columns: repeat(auto-fill, minmax(min(180px, 100%), 1fr)); } }
.library-scene{height:100%;min-height:0;overflow:auto;padding:24px clamp(18px,5vw,76px) 64px;background:var(--fe-bg);color:var(--fe-ink)}
.library-header,.library-actions,.library-tools,.library-search,.library-filters,.library-book-badges{display:flex;align-items:center;gap:12px}.library-header,.library-tools{justify-content:space-between;flex-wrap:wrap}.library-scene button{min-height:44px;padding:8px 14px;display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid var(--fe-border);border-radius:var(--fe-radius,8px);background:var(--fe-panel);color:var(--fe-ink);cursor:pointer}.library-scene button:focus-visible,.library-search:focus-within{outline:2px solid var(--fe-accent);outline-offset:3px}.library-scene button:disabled{opacity:.5;cursor:wait}.library-intro{padding:48px 0 30px;max-width:760px}.library-eyebrow,.library-book-number,.library-book-imprint{font-size:11px;letter-spacing:.14em;color:var(--fe-ink-3)}.library-title{font-family:serif;font-size:clamp(28px,4vw,46px);margin:15px 0}.library-intro p,.library-status,.library-meta{color:var(--fe-ink-2);font-size:13px;line-height:1.8}.library-search{border:1px solid var(--fe-border);padding:0 12px;background:var(--fe-panel);flex:1;max-width:440px}.library-search input{min-width:0;width:100%;min-height:44px;background:transparent;border:0;color:inherit;outline:none}.library-filters{flex-wrap:wrap;gap:6px}.library-filters button[aria-pressed=true]{background:var(--fe-accent);color:var(--fe-accent-ink)}.library-status{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:20px 0}.library-status span{font-size:11px}.library-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:30px 24px}.library-book{min-width:0;border-bottom:1px solid var(--fe-border);padding-bottom:20px}.library-book-page{min-height:248px;padding:22px;display:flex;flex-direction:column;gap:22px;background:var(--fe-panel);border:1px solid var(--fe-border);border-left:5px solid var(--fe-accent);box-shadow:4px 4px 0 var(--fe-panel-2)}.library-book h2{font-family:serif;font-size:25px;line-height:1.5;overflow-wrap:anywhere;margin:0;flex:1}.library-meta{padding:16px 0}.library-meta p{margin:5px 0}.library-meta code{display:block;font-size:10px;overflow-wrap:anywhere;color:var(--fe-ink-3)}.library-book-badges{flex-wrap:wrap;gap:6px;font-size:11px}.library-book-badges span{background:var(--fe-panel-2);padding:2px 7px}.library-empty{padding:60px 20px;text-align:center;border-top:1px solid var(--fe-border)}.library-empty>svg{margin:auto;color:var(--fe-accent)}.library-empty p{color:var(--fe-ink-2);line-height:1.8}.library-error{padding:16px;border-left:3px solid var(--fe-danger);background:var(--fe-panel);margin:16px 0;overflow-wrap:anywhere}.library-error p{margin:0 0 8px}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}@media(max-width:600px){.library-intro{padding-top:28px}.library-search{max-width:none;flex-basis:100%}.library-grid{grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}.library-header{align-items:flex-start}.library-actions{flex-wrap:wrap}.library-book-page{min-height:220px}}
</style>
