import { apiClient } from './kernel/apiClient'
import type {
  BootstrapData,
  CharacterDesignerGenerateResult,
  CharacterDesignerSaveResult,
  CharacterDesignerSchema,
  CharacterLibraryCard,
  CharacterLibraryListResult,
  CharacterLibraryUpsertPayload,
  CharacterPoolSlot,
  CharacterGender,
  DesignerCorpusItem,
  DesignerIdentity,
  DistillProgress,
  GoldenFingerProposal,
  GoldenFingerRecommendation,
  GoldenFingerSpecPayload,
  LoadSaveResult,
  ModelFetchResult,
  ModelTestResult,
  NovelExportResult,
  BreakAnchorResult,
  QuestKind,
  QuestOfferResult,
  QuestResult,
  SaveMeta,
  SaveResult,
  StreamEvent,
  UploadInfo,
  UserBookMeta,
  UserBookDetail,
  UserBookChapter,
} from './types'

function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  return fetch(apiClient.url(input), init)
}

async function responseError(response: Response): Promise<Error> {
  try {
    const body = await response.json() as { detail?: string }
    return new Error(body.detail || `请求失败（${response.status}）`)
  } catch {
    return new Error(`请求失败（${response.status}）`)
  }
}

export async function getBootstrap(): Promise<BootstrapData> {
  const response = await apiFetch('/api/bootstrap')
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<BootstrapData>
}

export interface LanInfo {
  addresses: string[]
  urls?: Array<{ address: string; url: string }>
  port: number
  url: string | null
  session_id: string | null
  listening_lan: boolean
  hint: string
}

export async function fetchLanInfo(sessionId?: string | null): Promise<LanInfo> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const response = await apiFetch(`/api/lan-info${query}`)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<LanInfo>
}

async function postJson<T>(url: string, body: unknown, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    method: init?.method ?? 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

export function recommendGoldenFingers(
  world: string,
  persona: string,
  difficulty: string,
  nemesisD?: number,
): Promise<GoldenFingerRecommendation> {
  return postJson('/api/golden-fingers/recommend', {
    world, persona, difficulty,
    ...(nemesisD != null ? { nemesis_d: nemesisD } : {}),
  })
}

export function proposeGoldenFinger(
  text: string,
  world: string,
  persona: string,
  difficulty: string,
  attempt: number,
  nemesisD?: number,
): Promise<GoldenFingerProposal> {
  return postJson('/api/golden-fingers/propose', {
    text, world, persona, difficulty, attempt,
    ...(nemesisD != null ? { nemesis_d: nemesisD } : {}),
  })
}

export function confirmGoldenFinger(proposal: GoldenFingerProposal): Promise<GoldenFingerProposal> {
  return postJson('/api/golden-fingers/confirm', { proposal })
}

export async function uploadFile(
  file: File,
  sessionId: string | null,
  kind: 'novel' | 'roster-skill' | 'persona' | 'nemesis' = 'novel',
): Promise<{ session_id: string; upload: UploadInfo }> {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  if (sessionId) form.append('session_id', sessionId)
  const response = await apiFetch('/api/uploads', { method: 'POST', body: form })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<{ session_id: string; upload: UploadInfo }>
}

export function uploadTxt(file: File, sessionId: string | null): Promise<{ session_id: string; upload: UploadInfo }> {
  return uploadFile(file, sessionId, 'novel')
}

export function uploadSkill(file: File, sessionId: string | null): Promise<{ session_id: string; upload: UploadInfo }> {
  return uploadFile(file, sessionId, 'roster-skill')
}

export function uploadPersona(file: File, sessionId: string | null): Promise<{ session_id: string; upload: UploadInfo }> {
  return uploadFile(file, sessionId, 'persona')
}

export function uploadNemesis(file: File, sessionId: string | null): Promise<{ session_id: string; upload: UploadInfo }> {
  return uploadFile(file, sessionId, 'nemesis')
}

export interface ModelConnectionParams {
  provider: string
  base_url: string
  api_key: string
  model?: string
}

export function fetchModels(params: ModelConnectionParams): Promise<ModelFetchResult> {
  return postJson('/api/models/fetch', params)
}

export function testModelConnection(params: ModelConnectionParams): Promise<ModelTestResult> {
  return postJson('/api/models/test', params)
}

export async function readNdjson(
  url: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await apiFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error('响应不支持流式读取')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as StreamEvent)
    }
    if (done) break
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as StreamEvent)
}

export async function listUserBooks(): Promise<UserBookMeta[]> {
  const response = await apiFetch('/api/books')
  if (!response.ok) throw await responseError(response)
  const data = await response.json() as { books: UserBookMeta[] }
  return data.books ?? []
}

export async function fetchUserBook(bookId: string): Promise<UserBookDetail> {
  const response = await apiFetch(`/api/books/${encodeURIComponent(bookId)}`)
  if (!response.ok) throw await responseError(response)
  const data = await response.json() as { book: UserBookDetail }
  return data.book
}

export async function fetchUserBookChapter(bookId: string, chapterIndex: number): Promise<UserBookChapter> {
  const response = await apiFetch(`/api/books/${encodeURIComponent(bookId)}/chapters/${chapterIndex}`)
  if (!response.ok) throw await responseError(response)
  const data = await response.json() as { chapter: UserBookChapter }
  return data.chapter
}

export function askQuestion(sessionId: string, question: string): Promise<{ answer: string }> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/ask`, { question })
}

export function offerQuest(sessionId: string, kind: QuestKind, difficulty: number): Promise<QuestOfferResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/quests/offer`, { kind, difficulty })
}

export function acceptQuest(sessionId: string): Promise<QuestResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/quests/accept`, {})
}

export function declineQuest(sessionId: string): Promise<QuestResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/quests/decline`, {})
}

export function breakAnchorOffer(sessionId: string): Promise<BreakAnchorResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/break-anchor/offer`, {})
}

export function breakAnchorAccept(sessionId: string): Promise<BreakAnchorResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/break-anchor/accept`, {})
}

export function breakAnchorDecline(sessionId: string): Promise<BreakAnchorResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/break-anchor/decline`, {})
}

export function autoplayChoice(sessionId: string): Promise<{ choice: string; reason: string }> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/autoplay-choice`, {})
}

export async function saveSession(sessionId: string, saveId: string): Promise<SaveResult> {
  const response = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ save_id: saveId }),
  })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<SaveResult>
}

export async function listSaves(): Promise<SaveMeta[]> {
  const response = await apiFetch('/api/saves')
  if (!response.ok) throw await responseError(response)
  const body = await response.json() as { saves: SaveMeta[] }
  return body.saves
}

export function loadAnySave(saveId: string): Promise<LoadSaveResult> {
  return postJson('/api/saves/load', { save_id: saveId })
}

export async function loadSession(sessionId: string, saveId: string): Promise<Record<string, unknown>> {
  const response = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ save_id: saveId }),
  })
  if (!response.ok) throw await responseError(response)
  const body = await response.json() as { state: Record<string, unknown> }
  return body.state
}

export async function getCharacterDesignerSchema(): Promise<CharacterDesignerSchema> {
  const response = await apiFetch('/api/character-designer/schema')
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<CharacterDesignerSchema>
}

export interface DesignerGeneratePayload {
  identity: DesignerIdentity
  corpus: DesignerCorpusItem[]
  answers: Record<string, string>
  session_id: string | null
  provider: string
  base_url: string
  api_key: string
  model?: string
}

export function generateCharacterDesign(payload: DesignerGeneratePayload): Promise<CharacterDesignerGenerateResult> {
  return postJson('/api/character-designer/generate', payload)
}

export interface DesignerSavePayload {
  filename: string
  persona_markdown: string
}

export function saveCharacterDesign(payload: DesignerSavePayload): Promise<CharacterDesignerSaveResult> {
  return postJson('/api/character-designer/save', payload)
}

// ---------------------------------------------------------------------------
// 四栏角色池（规格 §2/§9）：主角栏 / 伴侣栏 / 伙伴栏 / 宿敌栏
// ---------------------------------------------------------------------------

export interface PoolCardEntry {
  id: string
  name: string
  gender: CharacterGender
  protagonist_gender?: CharacterGender
  work: string
  source_medium: string
  source_region: string
  archetype: string
  original_position: string
  protagonist_type: string[]
  companion_type: string[]
  /** 历史别名，等同 companion_type */
  mainline_type?: string[]
  partner_type: string[]
  nemesis_type: string[]
  // 悬停/选中简介（由后端蒸馏卡补充，缺失时前端安全兜底）
  one_line?: string
  background?: string
  desire?: string
  abilities?: string[]
}

export interface PoolGroup {
  /** 第一级分组：来源（主角/男主/女主/配角/反派/未标注） */
  key: string
  /** 第二级分组：该来源下的栏位分类（slot_keys 对应值） */
  sub_groups: PoolSubGroup[]
}

export interface PoolSubGroup {
  key: string
  cards: PoolCardEntry[]
}

export interface PoolResult {
  slot: CharacterPoolSlot | string
  total?: number
  keys: PoolGroup[]
}

export async function fetchCharacterPool(slot: CharacterPoolSlot, gender?: CharacterGender): Promise<PoolResult> {
  const params = new URLSearchParams({ slot })
  if (gender) params.set('gender', gender)
  const response = await apiFetch(`/api/characters/pool?${params.toString()}`)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<PoolResult>
}

// 单卡完整简介：悬停/选中时渲染角色简介卡（信息比列表项更完整）。
export async function fetchCharacterPoolDetail(cardId: string, slot: CharacterPoolSlot): Promise<PoolCardEntry> {
  const params = new URLSearchParams({ slot })
  const response = await apiFetch(`/api/characters/pool/${encodeURIComponent(cardId)}/detail?${params.toString()}`)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<PoolCardEntry>
}

// ---------------------------------------------------------------------------
// 角色库（用户本地扩充 / 替换内置 / 导出导入）
// 角色库管理页已删除；保留 createCharacterCard 供角色设计器「保存入库」使用。
// ---------------------------------------------------------------------------

export function createCharacterCard(
  payload: CharacterLibraryUpsertPayload,
  replaceBuiltIn: boolean,
): Promise<{ card: CharacterLibraryCard }> {
  return postJson(`/api/character-library?replace_built_in=${replaceBuiltIn}`, payload)
}

export async function listCharacterLibrary(): Promise<CharacterLibraryListResult> {
  const response = await apiFetch('/api/character-library')
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<CharacterLibraryListResult>
}

export function updateCharacterCard(cardId: string, payload: CharacterLibraryUpsertPayload): Promise<{ card: CharacterLibraryCard }> {
  return postJson(`/api/character-library/${encodeURIComponent(cardId)}`, payload, { method: 'PUT' })
}

export async function deleteCharacterCard(cardId: string): Promise<{ id: string; removed: string }> {
  const response = await apiFetch(`/api/character-library/${encodeURIComponent(cardId)}`, { method: 'DELETE' })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<{ id: string; removed: string }>
}

export function exportSessionNovel(sessionId: string, style: string): Promise<NovelExportResult> {
  return postJson(`/api/sessions/${encodeURIComponent(sessionId)}/export-novel`, { style })
}

export function exportSaveNovel(saveId: string, style: string, connection: ModelConnectionParams): Promise<NovelExportResult> {
  return postJson(`/api/saves/${encodeURIComponent(saveId)}/export-novel`, { style, ...connection })
}

export async function fetchDistillProgress(sessionId: string): Promise<DistillProgress> {
  const response = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/distill/progress`)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<DistillProgress>
}

export async function fetchSessionState(sessionId: string): Promise<{ session_id: string; state: Record<string, unknown> }> {
  const response = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/state`)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<{ session_id: string; state: Record<string, unknown> }>
}
