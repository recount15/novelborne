import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as Vue from 'vue'

// Execute actual SFC setup in a Vue lifecycle, with only API boundaries stubbed.
function mountSetup(path, props, api = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: 'panel-test' }).content
  const code = ts.transpileModule(compiled, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '')
    .replace('export default', 'return')
  const imported = [...compiled.matchAll(/import\s*\{([^}]+)\}\s*from/g)].flatMap(match => match[1].split(',').map(name => name.trim().split(/\s+as\s+/).at(-1)).filter(name => /^[A-Za-z_$][\w$]*$/.test(name)))
  imported.push(...[...compiled.matchAll(/import\s+([A-Za-z_$][\w$]*)\s+from/g)].map(match => match[1]))
  const bindings = Object.fromEntries(imported.map(name => [name, api[name] ?? Vue[name] ?? (name === '_defineComponent' ? Vue.defineComponent : () => {})]))
  const component = new Function(...Object.keys(bindings), code)(...Object.values(bindings))
  let state
  const events = []
  const originalSetup = component.setup
  component.setup = (p, context) => {
    state = originalSetup(p, { ...context, emit: (...event) => events.push(event) })
    return () => null
  }
  const renderer = Vue.createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null,
  })
  const app = renderer.createApp(component, props)
  app.mount({})
  return { state, events, unmount: () => app.unmount(), source }
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve() }
const credentials = { provider: '', base_url: '', api_key: '', model: '' }
const baseJob = { job_id: 'test-job', status: 'VALIDATING', stage: 'VALIDATING', sequence: 1, completed_units: 2, total_units: 2, unit_kind: 'blocks', source_hash: 'test-hash', retryable: true, needs_credentials: false }

test('reader initial book hint selects existing IDs and invalid hints retain old-entry fallback', async () => {
  for (const [preferred, expected] of [['book-b', 'book-b'], ['missing-book', 'book-a'], [undefined, 'book-a']]) {
    const selected = []
    const panel = mountSetup('../src/components/OriginalReaderModal.vue', { initialBookId: preferred }, {
      onMounted: () => {}, onBeforeUnmount: () => {},
      useReaderState: () => ({ settings: Vue.ref({ tone: 'paper', fontSize: 18, lineHeight: 1.8, contentWidth: 700 }), progress: Vue.ref({}) }),
      useChapterSearch: () => ({ query: Vue.ref(''), matches: Vue.ref([]), matchIndex: Vue.ref(0), ranges: Vue.ref([]) }),
      prepareReaderText: () => ({ text: '', sourceToDisplay: [] }),
      listUserBooks: async () => [{ book_id: 'book-a' }, { book_id: 'book-b' }],
      fetchUserBook: async id => { selected.push(id); return { book_id: id, chapters: [] } },
    })
    try {
      await panel.state.loadBooks(preferred)
      assert.deepEqual(selected, [expected])
      assert.equal(panel.state.activeBook.value.book_id, expected)
      assert.match(panel.source, /loadBooks\(props.initialBookId\)/)
    } finally { panel.unmount() }
  }
})

test('preparation progress reports counts, not fabricated readiness', () => {
  for (const [completed_units, total_units, expected] of [[2, 2, 100], [-2, 2, 0], [0, null, null], [0, 0, null], [NaN, 2, null]]) {
    const panel = mountSetup('../src/components/PreparationJobPanel.vue', { job: { ...baseJob, completed_units, total_units }, credentials, label: '测试任务' })
    try {
      assert.equal(panel.state.progress.value, expected)
      assert.equal(panel.state.active.value, true)
      assert.deepEqual(panel.events, [])
      assert.match(panel.source, /阶段计数已完成，任务尚未就绪/)
    } finally { panel.unmount() }
  }
})

test('cancel invalidates an in-flight poll and preserves newer server sequence', async () => {
  const poll = deferred()
  const panel = mountSetup('../src/components/PreparationJobPanel.vue', { job: baseJob, credentials, label: '测试任务' }, {
    getPreparationJob: () => poll.promise,
    cancelPreparationJob: async () => ({ ...baseJob, status: 'CANCEL_REQUESTED', sequence: 3 }),
  })
  try {
    const refreshing = panel.state.refresh()
    await panel.state.act('cancel')
    poll.resolve({ ...baseJob, status: 'READY', sequence: 2 })
    await refreshing
    assert.deepEqual(panel.events.map(event => [event[0], event[1]?.status]), [['update', 'CANCEL_REQUESTED']])
  } finally { panel.unmount() }
})

test('failed send retains draft and reuses the same idempotency key on retry', async () => {
  const sent = []
  let fail = true
  const panel = mountSetup('../src/components/ReaderChatPanel.vue', { bookId: 'test-book', chapterNo: 1, credentials }, {
    getReaderRoster: async () => ({ status: 'ready', characters: [], cutoff: {} }),
    requestId: () => 'test-request',
    sendReaderMessage: async (id, body) => { sent.push({ id, ...body }); if (fail) throw new Error('test offline') },
    getReaderThread: async () => ({ thread_id: 'test-thread', messages: [] }),
  })
  try {
    await flush()
    panel.state.thread.value = { thread_id: 'test-thread', messages: [] }
    panel.state.input.value = '测试问题'
    await panel.state.send()
    assert.equal(panel.state.input.value, '测试问题')
    assert.equal(panel.state.error.value, 'test offline')
    fail = false
    await panel.state.send()
    assert.equal(sent.length, 2)
    assert.equal(sent[0].request_id, sent[1].request_id)
    assert.equal(panel.state.input.value, '')
    assert.equal(panel.state.busy.value, false)
  } finally { panel.unmount() }
})

test('chat does not apply a late roster after unmount', async () => {
  const pending = deferred()
  const panel = mountSetup('../src/components/ReaderChatPanel.vue', { bookId: 'test-book', chapterNo: 1, credentials }, { getReaderRoster: () => pending.promise })
  panel.unmount()
  pending.resolve({ characters: [], status: 'ready' })
  await flush()
  assert.equal(panel.state.roster.value, null)
})

test('game view chat is fully usable without original-work roster fields', async () => {
  const gameRoster = { status: 'ready', book_id: 'book-a', branch_id: 'branch-1', cutoff: null, characters: [{ character_id: 'char-a', card_revision: null, identity: { character_id: 'char-a' }, mode: 'game', grounding: 'partial_committed_branch', effective_state: { basis: 'committed_branch' } }] }
  const gameThread = { thread_id: 'thread-game', scope: 'game', mode: 'game', status: 'ready', context: { cutoff: null, source_hash: null, identity: { character_id: 'char-a' }, card_revision: null }, messages: [] }
  const rosterCalls = []
  const created = []
  const panel = mountSetup('../src/components/ReaderChatPanel.vue', { bookId: 'book-a', chapterNo: 2, view: 'game', sessionId: 'sess-1', credentials }, {
    getReaderRoster: async (...args) => { rosterCalls.push(args); return gameRoster },
    createReaderThread: async (_book, body) => { created.push(body); return gameThread },
    getReaderThread: async () => gameThread,
  })
  try {
    await flush()
    assert.deepEqual(rosterCalls, [['book-a', 2, { view: 'game', sessionId: 'sess-1' }]])
    assert.equal(panel.state.activeView.value, 'game')
    // 本局花名册没有原著姓名：人物标签必须回退到稳定 ID，不得渲染 undefined。
    assert.equal(panel.state.characterLabel(gameRoster.characters[0]), 'char-a')
    assert.equal(panel.state.characterLabel({ character_id: 'x', card_revision: 1, identity: { name: '乙' }, mode: 'reader' }), '乙')
    panel.state.selected.value = 'char-a'
    await panel.state.openThread()
    assert.equal(panel.state.thread.value.thread_id, 'thread-game')
    // 本局线程没有 cutoff/卡片修订：创建请求不得携带原著边界字段。
    assert.deepEqual(created, [{ character_id: 'char-a', chapter_no: 2, view: 'game', session_id: 'sess-1' }])
    assert.equal(panel.state.threadName.value, 'char-a')
    // 显式来源标识 + 有效愿望解释：本局与原著必须可区分，本局说明已生效愿望才算事实。
    assert.match(panel.source, /本局访谈/)
    assert.match(panel.source, /原著访谈|原著阅读/)
    assert.match(panel.source, /已生效/)
    assert.match(panel.source, /不.{0,6}当作原文|不得当作本局/)
  } finally { panel.unmount() }
})

test('fast view switching reloads rosters per view and a late stale roster cannot cross views', async () => {
  const gameRoster = { status: 'ready', book_id: 'book-a', branch_id: 'branch-1', cutoff: null, characters: [{ character_id: 'char-a', card_revision: null, identity: { character_id: 'char-a' }, mode: 'game', grounding: 'partial_committed_branch', effective_state: {} }] }
  const slowOriginal = deferred()
  const calls = []
  const panel = mountSetup('../src/components/ReaderChatPanel.vue', { bookId: 'book-a', chapterNo: 2, view: 'original', sessionId: 'sess-1', credentials }, {
    getReaderRoster: async (...args) => { calls.push(args); return calls.length === 1 ? slowOriginal.promise : Promise.resolve(gameRoster) },
  })
  try {
    await flush()
    assert.deepEqual(calls[0], ['book-a', 2, { view: 'original', sessionId: 'sess-1' }])
    panel.state.switchView('game')
    await flush()
    await flush()
    assert.equal(panel.state.activeView.value, 'game')
    assert.deepEqual(calls[1], ['book-a', 2, { view: 'game', sessionId: 'sess-1' }])
    assert.equal(panel.state.roster.value.branch_id, 'branch-1')
    // 切视图重置选择与线程，避免把原著线程带进本局。
    assert.equal(panel.state.selected.value, '')
    assert.equal(panel.state.thread.value, null)
  } finally {
    // finally 里补结算悬挂请求：断言失败也释放组件内部的有界等待定时器。
    slowOriginal.resolve({ status: 'ready', book_id: 'book-a', cutoff: { chapter_no: 2, offset: 9, source_hash: 'stale' }, characters: [] })
    await flush()
    assert.equal(panel.state.roster.value.branch_id, 'branch-1')
    panel.unmount()
  }
})

test('abstention replies settle busy state and surface a soft grounding hint', async () => {
  const replyText = '截至阅读边界，已知资料不足以回答这个问题。'
  const thread = { thread_id: 't1', mode: 'reader', status: 'ready', context: { cutoff: {}, source_hash: 'h', identity: { name: '甲' }, card_revision: 1 }, messages: [] }
  const panel = mountSetup('../src/components/ReaderChatPanel.vue', { bookId: 'book-a', chapterNo: 1, credentials }, {
    getReaderRoster: async () => ({ status: 'ready', book_id: 'book-a', cutoff: { chapter_no: 1, offset: 0, source_hash: 'h' }, characters: [] }),
    getReaderThread: async () => ({ ...thread, messages: [{ message_id: 2, role: 'assistant', content: replyText }] }),
    sendReaderMessage: async () => ({ reply: replyText, saved: true, grounding: 'abstention' }),
    requestId: () => 'r1',
  })
  try {
    await flush()
    panel.state.thread.value = thread
    panel.state.input.value = '这个问题原著里有答案吗'
    await panel.state.send()
    assert.equal(panel.state.busy.value, false)
    assert.equal(panel.state.input.value, '')
    assert.equal(panel.state.lastGrounding.value, 'abstention')
    assert.match(panel.source, /soft-hint/)
  } finally { panel.unmount() }
})

test('an unresponsive roster wait is bounded and releases busy instead of spinning forever', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const panel = mountSetup('../src/components/ReaderChatPanel.vue', { bookId: 'book-a', chapterNo: 1, credentials }, {
    getReaderRoster: () => new Promise(() => {}),
  })
  try {
    await flush()
    assert.equal(panel.state.busy.value, true)
    t.mock.timers.tick(46000)
    await flush()
    assert.equal(panel.state.busy.value, false)
    assert.match(panel.state.error.value, /超时/)
    assert.ok(panel.source.includes('REQUEST_BOUND_MS'))
  } finally { panel.unmount(); t.mock.timers.reset() }
})

test('anchors panel reports the server unavailable status verbatim instead of fabricating certainty', () => {
  const panelSource = readFileSync(new URL('../src/components/reader/ReaderAnchorsPanel.vue', import.meta.url), 'utf8')
  assert.match(panelSource, /'unavailable'/)
  assert.match(panelSource, /\{\{ detail/)
  const modal = readFileSync(new URL('../src/components/OriginalReaderModal.vue', import.meta.url), 'utf8')
  assert.match(modal, /:status="chapterInsight\?\.status"/)
  assert.match(modal, /:detail="chapterInsight\?\.detail"/)
})

test('designer displays committed save feedback and retries read without saving again', async () => {
  let saves = 0
  let failRead = true
  const panel = mountSetup('../src/views/CharacterDesigner.vue', { connection: credentials, sessionId: null }, {
    getCharacterDesignerSchema: async () => ({}),
    saveCharacterDesign: async () => { saves++; return { saved: true, character_id: 'test-character', revision: 2, card: { name: '测试角色', revision: 2 } } },
    loadCharacterDesign: async () => { if (failRead) throw new Error('test offline'); return { card: { name: '测试角色', revision: 2 } } },
  })
  try {
    await flush()
    panel.state.result.value = { card: { name: '测试角色' } }
    await panel.state.saveCardToCharacterLibrary()
    assert.match(panel.state.poolSaveDone.value, /修订 2/)
    assert.match(panel.state.poolSaveError.value, /已保存/)
    assert.equal(panel.state.reloadCharacterId.value, 'test-character')
    assert.match(panel.source, /v-if="poolSaveDone" role="status"/)
    assert.match(panel.source, /v-if="poolSaveError" role="alert"/)
    failRead = false
    await panel.state.retrySavedCard()
    assert.equal(saves, 1)
    assert.equal(panel.state.reloadCharacterId.value, '')
    assert.equal(panel.state.poolSaveError.value, '')
    assert.equal(panel.events.filter(event => event[0] === 'saved').length, 1)
  } finally { panel.unmount() }
})
