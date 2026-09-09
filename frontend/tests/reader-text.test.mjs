import test from 'node:test'
import assert from 'node:assert/strict'
import { prepareReaderText, originalRangeToDisplay, findReaderMatches, readerTextParts, readerChapterCharCount, readerOccurrenceLabel } from '../src/utils/readerText.ts'

test('maps Python codepoints through CRLF, title removal, whitespace and astral characters', () => {
  const source = '\r\n  标题\r\n\t😀同句。\r\n同句。'
  const prepared = prepareReaderText(source, '标题')
  assert.equal(prepared.text, '😀同句。\n同句。')
  const points = Array.from(source)
  const start = points.lastIndexOf('同')
  const range = originalRangeToDisplay(prepared.sourceToDisplay, start, start + 2)
  assert.deepEqual(range, { start: 6, end: 8 })
  assert.equal(prepared.text.slice(range.start, range.end), '同句')
  assert.equal(prepared.sourceToDisplay.length, points.length + 1)
  assert.equal(prepared.sourceToDisplay.at(-1), prepared.text.length)
})

test('removed title maps to chapter start instead of another identical substring', () => {
  const prepared = prepareReaderText('标题\n标题\n正文', '标题')
  assert.deepEqual(originalRangeToDisplay(prepared.sourceToDisplay, 0, 2), { start: 0, end: 0 })
  assert.equal(prepared.text, '标题\n正文')
})

test('invalid service offsets are rejected', () => {
  const { sourceToDisplay } = prepareReaderText('😀字')
  for (const [start, end] of [[-1, 1], [0, 4], [2, 1], [0.5, 1], [NaN, 1]]) {
    assert.equal(originalRangeToDisplay(sourceToDisplay, start, end), null)
  }
})

test('local count, highlight text and navigation coordinates use the same display string', () => {
  const { text } = prepareReaderText('标题\n\n 😀Test test TEST', '标题')
  const ranges = findReaderMatches(text, ' test ')
  assert.equal(ranges.length, 3)
  const parts = readerTextParts(text, ranges)
  assert.equal(parts.map(part => part.text).join(''), text)
  assert.equal(parts.filter(part => part.hit).length, ranges.length)
  assert.deepEqual(parts.filter(part => part.hit).map(part => part.matchIndex), [0, 1, 2])
  ranges.forEach(range => assert.equal(text.slice(range.start, range.end).toLowerCase(), 'test'))
})

test('literal regex characters and Unicode casing never drift offsets', () => {
  assert.deepEqual(findReaderMatches('İ 😀abc [a]+ [a]+', '[a]+'), [{ start: 8, end: 12 }, { start: 13, end: 17 }])
  assert.deepEqual(findReaderMatches('İabc abc', 'abc'), [{ start: 1, end: 4 }, { start: 5, end: 8 }])
  assert.deepEqual(findReaderMatches('aaa', 'aa'), [{ start: 0, end: 2 }])
  assert.deepEqual(findReaderMatches('abc', '  '), [])
})

test('processing preserves established chapter rendering', () => {
  const legacy = (text, title) => { const lines = text.replace(/\r\n?/g, '\n').split('\n'); const first = lines.findIndex(line => line.trim()); if (first >= 0 && title && lines[first].trim() === title.trim()) lines.splice(first, 1); return lines.join('\n').replace(/^\s+/, '') }
  for (const text of ['', '标题', '\n标题', '标题\r\n正文', '\r  标题\n\n 正文\n', '正文\n标题', ' \n😀正文']) {
    assert.equal(prepareReaderText(text, '标题').text, legacy(text, '标题'))
  }
})


test('chapter count falls back to Unicode codepoints for missing or zero metadata', () => {
  assert.equal(readerChapterCharCount({ text: '😀正文' }), 3)
  assert.equal(readerChapterCharCount({ chars: 0, text: '😀正文' }), 3)
  assert.equal(readerChapterCharCount({ chars: 12, text: '正文' }), 12)
  assert.equal(readerChapterCharCount({ chars: NaN, text: '字' }), 1)
  assert.equal(readerChapterCharCount({}), 0)
})

test('identical result excerpts have distinct accessible occurrence labels', () => {
  const first = readerOccurrenceLabel({ chapter_no: 2, start: 10, excerpt: '钥匙' }, 21)
  const second = readerOccurrenceLabel({ chapter_no: 2, start: 40, excerpt: '钥匙' }, 22)
  assert.notEqual(first, second)
  assert.match(first, /第 21 处匹配 · 第 2 章 · 原文第 11 个字符：钥匙/)
  assert.match(second, /第 22 处匹配 · 第 2 章 · 原文第 41 个字符：钥匙/)
})
