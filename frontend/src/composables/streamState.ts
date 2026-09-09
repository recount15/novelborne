import type { ChatMessage, StreamEvent } from '../types'

export interface StreamProjection {
  committed: Record<string, unknown>
  chat: ChatMessage[]
  draft: string
  phase: string
  committedSeen: boolean
}

/** Only backend durable save stages can replace authoritative UI state. */
export function projectStreamEvent(current: StreamProjection, event: StreamEvent): StreamProjection {
  if (event.type !== 'state') return current
  const incoming = event.data.state
  const phase = event.data.status || current.phase
  const latest = event.data.chat?.filter(item => item.role === 'assistant').at(-1)
  if (!incoming || !['opening', 'committed'].includes(String(incoming.save_stage))) {
    return { ...current, phase, draft: latest?.content || event.data.content || current.draft }
  }
  const revision = Number(incoming.revision)
  const previous = Number(current.committed.revision)
  if (Number.isFinite(previous) && (!Number.isFinite(revision) || revision < previous)) return current
  return {
    committed: incoming,
    chat: event.data.chat ?? current.chat,
    draft: '', phase, committedSeen: true,
  }
}
