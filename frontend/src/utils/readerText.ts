export interface TextRange { start: number; end: number }

export function readerChapterCharCount(chapter: { chars?: number; text?: string }): number {
  return typeof chapter.chars === 'number' && Number.isFinite(chapter.chars) && chapter.chars > 0
    ? chapter.chars : Array.from(chapter.text || '').length
}

export function readerOccurrenceLabel(hit: { chapter_no: number; start: number; excerpt: string }, ordinal: number): string {
  return `第 ${ordinal} 处匹配 · 第 ${hit.chapter_no} 章 · 原文第 ${hit.start + 1} 个字符：${hit.excerpt}`
}

/** Service coordinates are Python codepoints; rendered coordinates are UTF-16.
 * Keep every original boundary, including removed title/whitespace and CRLF. */
export function prepareReaderText(source: string, title?: string) {
  const points = Array.from(source)
  const sourceToDisplay: number[] = [0]
  let text = ''
  for (let i = 0; i < points.length; i++) {
    const point = points[i]!
    if (point === '\n' && points[i - 1] === '\r') {
      sourceToDisplay.push(text.length)
      continue
    }
    text += point === '\r' ? '\n' : point
    sourceToDisplay.push(text.length)
  }
  function remove(start: number, end: number) {
    text = text.slice(0, start) + text.slice(end)
    for (let i = 0; i < sourceToDisplay.length; i++) {
      const offset = sourceToDisplay[i]!
      sourceToDisplay[i] = offset <= start ? offset : offset < end ? start : offset - (end - start)
    }
  }
  const lines = text.split('\n')
  const first = lines.findIndex(line => line.trim())
  if (first >= 0 && title && lines[first]!.trim() === title.trim()) {
    const start = lines.slice(0, first).reduce((n, line) => n + line.length + 1, 0)
    remove(start, start + lines[first]!.length + (first < lines.length - 1 ? 1 : 0))
  }
  const leading = text.match(/^\s+/)?.[0].length ?? 0
  if (leading) remove(0, leading)
  return { text, sourceToDisplay }
}

export function originalRangeToDisplay(mapping: number[], start: number, end: number): TextRange | null {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end >= mapping.length) return null
  return { start: mapping[start]!, end: mapping[end]! }
}

/** Literal case-insensitive matches without lowercasing-induced offset drift. */
export function findReaderMatches(text: string, query: string): TextRange[] {
  const needle = query.trim()
  if (!needle) return []
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return Array.from(text.matchAll(new RegExp(escaped, 'giu')), match => ({ start: match.index!, end: match.index! + match[0].length }))
}

export function readerTextParts(text: string, ranges: TextRange[]) {
  const parts: Array<{ text: string; hit: boolean; matchIndex: number }> = []
  let cursor = 0
  ranges.forEach((range, matchIndex) => {
    if (range.start > cursor) parts.push({ text: text.slice(cursor, range.start), hit: false, matchIndex: -1 })
    if (range.end > range.start) parts.push({ text: text.slice(range.start, range.end), hit: true, matchIndex })
    cursor = range.end
  })
  if (cursor < text.length) parts.push({ text: text.slice(cursor), hit: false, matchIndex: -1 })
  return parts
}
