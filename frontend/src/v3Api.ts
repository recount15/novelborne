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
  /** 蒸馏块级结构化错误（code/field/block_id/chapter_no/retryable），最多展示 10 条 */
  error?: {
    message?: string; code?: string
    issues?: Array<{ block_id?: string | null; chapter_no?: number | null; code?: string; field?: string | null; message?: string; retryable?: boolean }>
  } | null
  gap_report?: Record<string, string>
}
export type ReaderView = 'original' | 'game'
export interface ReaderBoundary { chapter_no: number; offset: number; source_hash: string }
// 本局视图（C07/C08）：花名册无原著截点（cutoff=null）、人物只带稳定 ID（无 name）、卡片修订为空。
export interface ReaderCharacter { character_id: string; card_revision: number | null; identity: { name?: string; character_id?: string }; mode: string; grounding: unknown; effective_state: unknown }
export interface ReaderRoster { status: string; book_id: string; branch_id?: string | null; cutoff: ReaderBoundary | null; characters: ReaderCharacter[] }
export interface ReaderThread {
  thread_id: string; mode: string; status: string; scope?: string
  context: { cutoff: ReaderBoundary | null; source_hash: string | null; identity: { name?: string; character_id?: string }; card_revision: number | null }
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
// C08：view=game 时携带 session_id（后端校验会话与书目锚定）；original 永不触碰会话。
export const getReaderRoster = (book: string, chapter: number, opts?: { view?: ReaderView; sessionId?: string | null }) => {
  const view: ReaderView = opts?.view === 'game' ? 'game' : 'original'
  const query = new URLSearchParams({ chapter_no: String(chapter), view })
  if (view === 'game' && opts?.sessionId) query.set('session_id', opts.sessionId)
  return v3Request<ReaderRoster>(`/api/books/${encodeURIComponent(book)}/reader-chat/roster?${query.toString()}`)
}
export const createReaderThread = (book: string, body: { character_id: string; chapter_no: number; source_hash?: string; card_revision?: number; view?: ReaderView; session_id?: string | null }) => v3Request<ReaderThread>(`/api/books/${encodeURIComponent(book)}/reader-chat/threads`, body)
export const getReaderThread = (id: string) => v3Request<ReaderThread>(`/api/reader-chat/threads/${encodeURIComponent(id)}`)
export const sendReaderMessage = (id: string, body: ModelCredentials & { message: string; request_id: string }) => v3Request<{ reply: string; saved: boolean; grounding?: string; scope?: string; mode?: string }>(`/api/reader-chat/threads/${encodeURIComponent(id)}/messages`, body)
