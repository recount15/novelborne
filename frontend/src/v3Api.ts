import { apiClient } from './kernel/apiClient'

export interface ModelCredentials { provider: string; base_url: string; api_key: string; model: string }
export interface PreparationJob {
  job_id: string; status: string; stage: string; sequence: number
  completed_units: number; total_units: number | null; unit_kind: string
  source_hash: string; result_id?: string | null; retryable: boolean; needs_credentials: boolean
  coverage?: { verified_blocks?: number; expected_blocks?: number; complete?: boolean } | null
  /** 章级进度（全书蒸馏）：已完成章数、总章数与正在蒸馏的章号 */
  chapters_done?: number | null
  chapters_total?: number | null
  current_chapter?: number | null
  character_counts?: Record<string, number | null>
  error?: { message?: string; code?: string } | null
  gap_report?: Record<string, string>
}
export interface ReaderBoundary { chapter_no: number; offset: number; source_hash: string }
export interface ReaderCharacter { character_id: string; card_revision: number; identity: { name: string }; mode: string; grounding: unknown; effective_state: unknown }
export interface ReaderRoster { status: string; book_id: string; cutoff: ReaderBoundary; characters: ReaderCharacter[] }
export interface ReaderThread {
  thread_id: string; mode: string; status: string
  context: { cutoff: ReaderBoundary; source_hash: string; identity: { name: string }; card_revision: number }
  messages: Array<{ message_id: number; role: string; content: string }>
}
function errorMessage(body: unknown): string | undefined {
  if (typeof body === 'string') return body.slice(0, 600)
  if (!body || typeof body !== 'object') return undefined
  const value = body as Record<string, unknown>
  return errorMessage(value.message) || errorMessage(value.detail) || errorMessage(value.error)
}
export async function v3Request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(apiClient.url(path), body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  let data: unknown
  try { data = await response.json() } catch {
    throw new Error(`服务返回非 JSON 响应（HTTP ${response.status}），请检查后端接口是否可用`)
  }
  if (!response.ok) throw new Error(errorMessage(data) || `请求失败（HTTP ${response.status}）`)
  return data as T
}
export const requestId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
export const createPreparationJob = (book: string, body: ModelCredentials & { mode: 'window' | 'fullbook'; target_chapter: number; idempotency_key: string }) =>
  v3Request<PreparationJob>(`/api/books/${encodeURIComponent(book)}/preparation-jobs`, { ...body, opening_chapters: 3 })
export const getPreparationJob = (id: string) => v3Request<PreparationJob>(`/api/preparation-jobs/${encodeURIComponent(id)}`)
export const cancelPreparationJob = (id: string) => v3Request<PreparationJob>(`/api/preparation-jobs/${encodeURIComponent(id)}/cancel`, {})
export const resumePreparationJob = (id: string, credentials: ModelCredentials) => v3Request<PreparationJob>(`/api/preparation-jobs/${encodeURIComponent(id)}/resume`, credentials)
export const getReaderRoster = (book: string, chapter: number) => v3Request<ReaderRoster>(`/api/books/${encodeURIComponent(book)}/reader-chat/roster?chapter_no=${chapter}`)
export const createReaderThread = (book: string, body: { character_id: string; chapter_no: number; source_hash: string; card_revision: number }) => v3Request<ReaderThread>(`/api/books/${encodeURIComponent(book)}/reader-chat/threads`, body)
export const getReaderThread = (id: string) => v3Request<ReaderThread>(`/api/reader-chat/threads/${encodeURIComponent(id)}`)
export const sendReaderMessage = (id: string, body: ModelCredentials & { message: string; request_id: string }) => v3Request<{ reply: string; saved: boolean }>(`/api/reader-chat/threads/${encodeURIComponent(id)}/messages`, body)
