import { ref, onMounted, onBeforeUnmount } from 'vue'

export interface GenerationSnapshot {
  sessionId: string
  status: string
  phase: string
  text: string
  round: number
  chapter: number
  updatedAt: number
  resumable: boolean
}

const KEY = 'novelborne-generation-snapshot'

export function useGenerationSnapshot(sessionId: () => string | null) {
  const snapshot = ref<GenerationSnapshot | null>(null)
  const persist = () => {
    try {
      if (snapshot.value?.resumable) sessionStorage.setItem(KEY, JSON.stringify(snapshot.value))
      else sessionStorage.removeItem(KEY)
    } catch { /* storage unavailable: server state remains authoritative */ }
  }
  const begin = (phase = 'starting') => {
    snapshot.value = { sessionId: sessionId() || '', status: 'waiting', phase, text: '', round: 0, chapter: 1, updatedAt: Date.now(), resumable: true }
    persist()
  }
  const update = (patch: Partial<GenerationSnapshot>) => {
    snapshot.value = { ...(snapshot.value || { sessionId: sessionId() || '', status: 'waiting', phase: '恢复中', text: '', round: 0, chapter: 1, resumable: true }), ...patch, updatedAt: Date.now() }
    persist()
  }
  const complete = () => { if (snapshot.value) { snapshot.value.resumable = false; persist(); snapshot.value = null } }
  onMounted(() => {
    try {
      const raw = sessionStorage.getItem(KEY)
      const parsed = raw ? JSON.parse(raw) as GenerationSnapshot : null
      if (parsed && parsed.resumable && parsed.sessionId === (sessionId() || '')) snapshot.value = parsed
    } catch { /* ignore malformed browser storage */ }
  })
  onBeforeUnmount(persist)
  return { snapshot, begin, update, complete, persist }
}
