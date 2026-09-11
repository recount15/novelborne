import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { projectStreamEvent } from '../src/composables/streamState.ts'

const base = () => ({ committed: { revision: 4, save_stage: 'committed', round: 2 }, chat: [{ role: 'assistant', content: '正式正文' }], draft: '', phase: '', committedSeen: false })

test('streaming frames cannot replace committed facts or narrative', () => {
  const prior = base()
  const next = projectStreamEvent(prior, { type: 'state', data: { state: { save_stage: 'streaming', revision: 99, round: 3 }, chat: [{ role: 'assistant', content: '草稿' }], status: '生成中' } })
  assert.equal(next.committed, prior.committed)
  assert.equal(next.chat, prior.chat)
  assert.equal(next.draft, '草稿')
  assert.equal(next.committedSeen, false)
})

test('durable frames replace facts and clear draft together', () => {
  const next = projectStreamEvent({ ...base(), draft: '草稿' }, { type: 'state', data: { state: { save_stage: 'committed', revision: 5 }, chat: [{ role: 'assistant', content: '完成' }] } })
  assert.equal(next.committed.revision, 5)
  assert.equal(next.chat[0].content, '完成')
  assert.equal(next.draft, '')
  assert.equal(next.committedSeen, true)
})

test('stale or unversioned frames cannot roll back a versioned state', () => {
  for (const revision of [3, undefined]) {
    const prior = base()
    assert.equal(projectStreamEvent(prior, { type: 'state', data: { state: { save_stage: 'committed', revision } } }), prior)
  }
})

test('opening is durable but progress and error never imply success', () => {
  const prior = base()
  assert.equal(projectStreamEvent(prior, { type: 'error', data: { message: '失败' } }), prior)
  assert.equal(projectStreamEvent(prior, { type: 'state', data: { status: '准备中' } }).committedSeen, false)
  assert.equal(projectStreamEvent(prior, { type: 'state', data: { state: { save_stage: 'opening', revision: 5 } } }).committedSeen, true)
})

test('character query edits remain slot-local and mode does not clamp gameplay', () => {
  const pools = readFileSync(new URL('../src/composables/useCharacterPools.ts', import.meta.url), 'utf8')
  const queryHandler = pools.match(/function onPoolQueryInput[\s\S]*?\n}/)[0]
  assert.match(queryHandler, /poolSlots.value\[slot\].query/)
  assert.doesNotMatch(queryHandler, /forEach|other|POOL_SLOT_KEYS/)
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(app, /enhanced\.value && form\.value\.story_agent_mode|paperTiers\.value\.filter|v-if="enableNemesis && !enhanced"/)
  const modeHandler = app.match(/function onModeChanged[\s\S]*?\n}/)[0]
  assert.doesNotMatch(modeHandler, /form\.value\.(work|timepoint|paper_tier)|enableNemesis/)
})

test('reader chat entries stay explicitly labeled by view and session wiring', () => {
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const chatHandler = app.match(/function onReaderChat[\s\S]*?\n}/)[0]
  assert.match(chatHandler, /view\?/)
  assert.match(app, /:view="readerChatTarget\.view \|\| 'original'"/)
  assert.match(app, /:session-id="sessionId"/)
  // 原著阅读器（原著域）的对话入口必须显式声明原著视图，不得默认落入本局。
  const modal = readFileSync(new URL('../src/components/OriginalReaderModal.vue', import.meta.url), 'utf8')
  assert.match(modal, /'chat-with-character': \[payload: \{ bookId: string; chapterNo: number; view: 'original'; characterId\?: string }\]/)
})
