export interface ProviderOption {
  id: string
  label: string
  base_url: string
  models: string[]
}

export type CharacterGender = 'male' | 'female' | 'other' | string

export type CharacterPoolSlot = '主角栏' | '伴侣栏' | '伙伴栏' | '宿敌栏'

export interface CharacterPool {
  id: string
  role: string
  name: string
  gender: CharacterGender
  protagonist_gender?: CharacterGender
  archetype: string
  desire: string
  fear: string
  abilities: string[]
  relationship_vector: Record<string, unknown>
  knowledge_scope: string[]
  voice: string
  unacceptable_actions: string[]
  background: string
  skill_ids: string[]
  source: string
}

export interface CharacterModelOption {
  label: string
  path?: string
}

export interface SkillEntry {
  id: string
  role: string
  name: string
  summary: string
  capabilities: string[]
  limits: string[]
  tags: string[]
  source: string
}

export interface RichnessTier {
  upper: number
  label: string
  note: string
}

export interface StoryRichnessConfig {
  min: number
  max: number
  step: number
  default: number
  tiers: RichnessTier[]
}

/** 剧情丰度（重构 M4）：双族六档；普通模式仅 basic_ok 档可选，agent_required 档需联动类 Agent 开关。 */
export interface PaperTierInfo {
  tier: number
  label: string
  family: 'small' | 'large' | string
  target_chars: number
  segments: number
  basic_ok: boolean
  agent_required: boolean
  agent_recommended: boolean
}

export interface BootstrapData {
  providers: ProviderOption[]
  works: string[]
  skills?: SkillEntry[]
  character_pools: CharacterPool[]
  custom_character_ids?: string[]
  character_models: CharacterModelOption[]
  personas: string[]
  modes: string[]
  difficulties: string[]
  golden_fingers: string[]
  golden_finger_library?: GoldenFingerLibraryItem[]
  story_richness?: StoryRichnessConfig
  paper_tiers?: PaperTierInfo[]
  paper_tier_default?: number
  story_agent_mode?: { label: string; note: string }
  counts?: { works: number; character_pools: number; personas: number; character_models: number }
}

export interface GoldenFingerLibraryItem {
  id: string
  label: string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | string
  content: string
}

export interface GameOption {
  key: 'A' | 'B' | 'C' | 'D' | 'E' | 'F'
  text: string
  preview?: string
  factor?: '金手指' | '性格' | '剧情' | string
  factors?: string[]
}

export interface AnchorNode {
  chapter: number
  title: string
  status?: string
  summary?: string
}

export interface RosterEntry {
  role_type: '伙伴' | '主线'
  gender?: CharacterGender
  protagonist_gender?: CharacterGender
  name: string
  background: string
  participation: number
  skill: string
  custom_skill: string
  skill_upload_id: string | null
  character_model: string
  character_model_source: string
  character_card: {
    goal: string
    fear: string
    abilities: string[]
    relationship_vector: Record<string, unknown>
    knowledge_scope: string[]
    speech_style: string
    unacceptable_behaviors: string[]
  }
}

export interface UploadInfo {
  upload_id: string
  filename?: string
  size?: number
  bytes?: number
  [key: string]: unknown
}

export interface StreamEvent {
  type: 'state' | 'message' | 'error' | 'done' | string
  data: {
    session_id?: string
    chat?: ChatMessage[]
    state?: Record<string, unknown>
    status?: string
    delta?: string
    message?: string
    content?: string
  }
}

export interface GoldenFingerRecommendation {
  choices: string[]
  specs: Array<Record<string, string>>
  none_label: string
  custom_label: string
  max_attempts: number
}

export interface GoldenFingerProposal {
  status: 'await_confirmation' | 'confirmed' | 'rejected' | string
  attempt: number
  remaining: number
  spec: Record<string, string>
}

export interface StartPayload {
  session_id: string | null
  provider: string
  base_url: string
  api_key: string
  model: string
  thinking_mode: string
  thinking_param: string
  mode: string
  work: string | null
  novel_upload_id: string | null
  fragment: string
  role: string
  timepoint: string
  difficulty: string
  convergence: string
  story_richness: number
  paper_tier?: number
  story_agent_mode: boolean
  golden_finger: string | null
  golden_finger_proposal: Record<string, unknown>
  persona_preset: string
  persona_custom: string
  persona_upload_id: string | null
  distill_enabled: boolean
  companion_roster: RosterEntry[]
  heroine_roster: RosterEntry[]
  companion_count: number
  heroine_count: number
  /** 数量即事实：伴侣 >1 时自动按多女主提交，对齐后端单女主校验 */
  heroine_mode?: string
  /** 性别栏杆已破除：前端不再采集，缺省时后端按 unknown 处理 */
  protagonist_gender?: 'male' | 'female'
  enable_nemesis: boolean
  nemesis_select: string
  nemesis_upload_id: string | null
  roster_card_ids?: Array<{ slot: string; card_id: string }>
}

export interface SaveMeta {
  session_id: string | null
  save_id: string
  saved_at: string
  mode: string | null
  work: string | null
  novel: string | null
  role: string | null
  persona: string | null
  difficulty: string | null
  round: number | null
  chapter: number | null
}

export interface SaveResult {
  session_id: string
  save_id: string
  saved: boolean
  metadata: SaveMeta | null
}

export interface LoadSaveResult {
  session_id: string
  save_id: string
  metadata: SaveMeta | null
  state: Record<string, unknown>
}

export interface ModelFetchResult {
  models: string[]
  message?: string
}

export interface ModelTestResult {
  ok: boolean
  message: string
}

export type QuestStatus = 'none' | 'offered' | 'active' | 'completed' | 'failed'
export type QuestKind = 'short' | 'medium' | 'long'

/** 增补通路（relay）：public_state 透传的增补相关状态 */
export interface RelayState {
  relay_active?: boolean
  wish_remaining?: number
}

export interface QuestOffer {
  title?: string
  requirements?: string[]
  goal?: string
  plot_hook?: string
}

export interface QuestSettlement {
  status?: string
  changed?: unknown
  reward?: unknown
}

export interface QuestState {
  status: QuestStatus
  kind?: QuestKind
  difficulty?: number
  offer?: QuestOffer
  reward?: Record<string, unknown> | null
  accepted_round?: number
  deadline_round?: number
  last_settlement?: QuestSettlement
  progress?: unknown[]
}

export interface QuestEstimate {
  coefficient: number
  level: number
  label: string
  range_lo: number
  range_hi: number
  range_label: string
  kind: string
  deadline_span: number
}

export interface QuestOfferResult {
  quest: QuestState
  reward?: Record<string, unknown> | null
  estimated?: QuestEstimate
}

export interface QuestResult {
  quest: QuestState
}

export type BreakAnchorStatus = 'idle' | 'ready' | 'offered' | 'active' | 'completed' | 'failed' | 'cooldown'

export interface MomentumBar {
  total: number
  threshold: number
  ratio: number
  ready: boolean
  tier: string
}

export interface BreakAnchorStage {
  id?: number
  title?: string
  requirement?: string
  status?: 'pending' | 'done' | string
}

export interface BreakAnchorState {
  status: BreakAnchorStatus | string
  target_anchor?: { chapter?: number; title?: string; summary?: string }
  stages?: BreakAnchorStage[]
  current_stage?: number
  momentum_spent?: number
  offered_round?: number
  accepted_round?: number
  deadline_round?: number
  cooldown_until?: number
  in_cooldown?: boolean
  momentum_bar?: MomentumBar
  broken_anchors?: number[]
  hint_only?: boolean
  can_offer?: boolean
}

export interface BreakAnchorResult {
  break_anchor: BreakAnchorState
  momentum_bar?: MomentumBar
  broken_anchors?: number[]
}

export interface DesignerField {
  key: string
  label?: string
  required?: boolean
  options?: string[]
  placeholder?: string
  multiline?: boolean
}

export interface DesignerQuestionOption {
  key: string
  text: string
}

export interface DesignerQuestion {
  id: string
  question: string
  options: DesignerQuestionOption[]
}

export interface DesignerCorpusKind {
  id: string
  label: string
  hint?: string
  priority?: number
}

export interface CharacterDesignerSchema {
  identity_fields: DesignerField[]
  corpus_kinds: Array<string | DesignerCorpusKind>
  questions: DesignerQuestion[]
}

export interface DesignerIdentity {
  name: string
  work?: string
  role_type?: string
  archetype?: string
  one_line?: string
  [key: string]: unknown
}

export interface DesignerCorpusItem {
  kind: string
  text: string
}

export interface DesignerQuality {
  level?: 'flat' | 'playable' | 'soulful' | string
  label?: string
  score?: number
  missing?: string[]
}

export interface CharacterDesignerGenerateResult {
  card?: Record<string, unknown>
  character_card?: Record<string, unknown>
  persona_markdown?: string
  persona?: string
  quality?: DesignerQuality
  suggested_filename?: string
  [key: string]: unknown
}

export interface CharacterDesignerSaveResult {
  label: string
  path: string
  message?: string
}

export interface CharacterLibraryCard {
  id: string
  role: string
  name: string
  gender: CharacterGender
  protagonist_gender?: CharacterGender
  original_position?: string
  source_medium?: string
  source_region?: string
  slot_keys?: Record<string, string[]>
  protagonist_type?: string[]
  mainline_type?: string[]
  partner_type?: string[]
  nemesis_type?: string[]
  work?: string
  archetype: string
  desire: string
  fear: string
  abilities: string[]
  relationship_vector: Record<string, string> | string
  knowledge_scope: string[] | string
  voice: string
  unacceptable_actions: string[]
  background: string
  skill_ids: string[]
  source: string
  origin: 'built_in' | 'user' | 'override'
  editable: boolean
  deletable: boolean
  replaces_built_in?: boolean
}

export interface CharacterLibraryListResult {
  cards: CharacterLibraryCard[]
  shadowed_built_in: string[]
}

export interface CharacterLibraryUpsertPayload {
  name: string
  role: string
  gender?: CharacterGender
  protagonist_gender?: CharacterGender
  original_position?: string
  source_medium?: string
  source_region?: string
  slot_keys?: Record<string, string[]>
  protagonist_type?: string[]
  mainline_type?: string[]
  partner_type?: string[]
  nemesis_type?: string[]
  work?: string
  archetype?: string
  desire?: string
  fear?: string
  abilities?: string[] | string
  relationship_vector?: Record<string, string> | string
  knowledge_scope?: string[] | string
  voice?: string
  unacceptable_actions?: string[] | string
  background?: string
  skill_ids?: string[] | string
  source?: string
  target_id?: string
}

export interface CharacterLibraryImportResult {
  imported: string[]
  replaced: string[]
  failed: Array<{ row: number; name: string; error: string }>
  total: number
}

export interface UserBookMeta {
  book_id: string
  name: string
  chapter_count: number
  source_chars: number
  updated_at: number
}

export interface UserBookChapterMeta {
  index: number
  title: string
  chars: number
}

export interface UserBookDetail extends UserBookMeta {
  chapters: UserBookChapterMeta[]
}

export interface UserBookChapter extends UserBookChapterMeta {
  text: string
}

export interface NovelChapter {
  index: number
  title: string
  text: string
}

export interface NovelExportManifest {
  title?: string
  style?: string
  chapter_count?: number
  source?: Record<string, unknown>
  [key: string]: unknown
}

export interface NovelExportResult {
  manifest: NovelExportManifest
  chapters: NovelChapter[]
  full_text: string
  tokens_est: number
  failed?: Array<Record<string, unknown>>
}

export interface ConvergenceState {
  base: string
  position: number
  effective: string
  last_settled_position: number
  history?: unknown[]
}

export interface NemesisSummary {
  round: number
  text: string
  distortion: number
}

export interface CompressionRecord {
  round: number
  compressed_at: string
  kept_messages: number
  fidelity?: string
}

export type ResourceKind = 'item' | 'technique' | 'faction' | 'taboo' | 'organization' | 'power_system'

export interface ResourceBundleItem {
  id?: string
  kind?: ResourceKind | string
  name: string
  summary?: string
  tags?: string[]
  rarity?: string
  source_span?: string
}

export interface ResourceItem {
  id: string
  kind: ResourceKind | string
  name: string
  summary: string
  tags: string[]
  rarity: string
  source_span?: string
}

export interface ResourceExtractResult {
  items: ResourceItem[]
  source: 'model' | 'heuristic' | string
}

export interface ResourceBundle {
  id: string
  name?: string
  items?: ResourceBundleItem[]
  item_count?: number
  saved_at?: string
}

export interface ResourceBundleSaveResult {
  id: string
  name: string
  items: ResourceItem[]
  saved_at?: string
  item_count?: number
}

export interface GoldenFingerSpecPayload {
  id: string
  name: string
  effect: string
  scope: string
  cost: string
  cooldown: string
  limits: string
  fit: string
  source: string
  label?: string
}

export interface DistillChapterProgress {
  chapter: number
  status: string
  status_zh: string
  current: boolean
}

export interface DistillProgress {
  enabled: boolean
  summary: string
  done?: number
  total?: number
  chapters?: DistillChapterProgress[]
}
