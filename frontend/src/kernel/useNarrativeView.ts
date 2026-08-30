import { computed, type Ref } from 'vue'
import type { ChatMessage } from '../types'

export interface NarrativeBlock {
  kind: 'chapter' | 'para'
  text: string
  dropCap: boolean
  cap: string
  rest: string
}

export interface NarrativeViewItem {
  role: string
  content: string
  blocks: NarrativeBlock[]
  distance: number
  isFocus: boolean
  style: Record<string, string>
}

const CHAPTER_HEADING = /^第[零〇一二三四五六七八九十百千万0-9０-９]+[章节卷回部]/
const cache = new Map<string, NarrativeBlock[]>()
const CACHE_MAX = 200

function blocksFor(content: string): NarrativeBlock[] {
  const cached = cache.get(content)
  if (cached) return cached
  const lines = content.split(/\n+/).map(line => line.trim()).filter(Boolean)
  const blocks: NarrativeBlock[] = []
  let firstPara = true
  for (const line of lines) {
    if (line.length <= 42 && CHAPTER_HEADING.test(line)) {
      blocks.push({ kind: 'chapter', text: line, dropCap: false, cap: '', rest: '' })
      continue
    }
    const chars = Array.from(line)
    const dropCap = firstPara && chars.length > 12
    firstPara = false
    blocks.push({ kind: 'para', text: line, dropCap, cap: dropCap ? chars[0] ?? '' : '', rest: dropCap ? chars.slice(1).join('') : '' })
  }
  if (cache.size >= CACHE_MAX) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(content, blocks)
  return blocks
}

function fadeOpacity(distance: number): number {
  if (distance <= 0) return 1
  const steps = [0.72, 0.52, 0.36, 0.24, 0.15]
  return steps[Math.min(distance - 1, steps.length - 1)] ?? 0.15
}

export function useNarrativeView(chat: Ref<ChatMessage[]>) {
  const chatView = computed<NarrativeViewItem[]>(() => {
    const items = chat.value
    const distances = new Array<number>(items.length)
    let seenAssistant = 0
    for (let index = items.length - 1; index >= 0; index--) {
      distances[index] = seenAssistant
      if (items[index].role !== 'user') seenAssistant++
    }
    return items.map((item, index) => {
      const isUser = item.role === 'user'
      const distance = distances[index]
      const opacity = isUser ? Math.max(0.55, fadeOpacity(distance)) : fadeOpacity(distance)
      const scale = distance > 0 ? Math.max(0.96, 1 - distance * 0.012) : 1
      return {
        role: item.role,
        content: item.content,
        blocks: isUser ? [] : blocksFor(item.content),
        distance,
        isFocus: !isUser && distance === 0,
        style: { opacity: String(opacity), transform: distance > 0 ? `scale(${scale})` : 'none' },
      }
    })
  })

  return { chatView }
}
