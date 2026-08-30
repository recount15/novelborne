<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArchiveRestore,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  FileArchive,
  FileText,
  Gauge,
  HelpCircle,
  KeyRound,
  ListChecks,
  LoaderCircle,
  Menu,
  MessageSquareText,
  Minus,
  Palette,
  PanelLeft,
  PanelRight,
  Play,
  PlugZap,
  RotateCcw,
  Save,
  ScrollText,
  Search,
  Send,
  Settings2,
  Smartphone,
  Sparkles,
  Square,
  Swords,
  Upload,
  UserRound,
  UsersRound,
  Wand2,
  BookUser,
  X,
} from 'lucide-vue-next'
import CharacterDesigner from './views/CharacterDesigner.vue'
import NovelExportModal from './components/NovelExportModal.vue'
import OriginalReaderModal from './components/OriginalReaderModal.vue'
import SiameseCat from './components/SiameseCat.vue'
import ThemePicker from './components/ThemePicker.vue'
import LanQrModal from './components/LanQrModal.vue'
import { THEME_META, applyTheme, currentTheme, type ThemeId } from './themeSwitch'
import { platform } from './kernel/platform'
import { useNarrativeView } from './kernel/useNarrativeView'
import {
  acceptQuest,
  askQuestion,
  autoplayChoice,
  breakAnchorAccept,
  breakAnchorDecline,
  breakAnchorOffer,
  confirmGoldenFinger,
  declineQuest,
  fetchCharacterPool,
  fetchDistillProgress,
  fetchLanInfo,
  fetchModels,
  fetchSessionState,
  getBootstrap,
  listSaves,
  loadAnySave,
  loadSession,
  offerQuest,
  proposeGoldenFinger,
  readNdjson,
  recommendGoldenFingers,
  saveSession,
  testModelConnection,
  uploadNemesis,
  uploadSkill,
  uploadTxt,
  type LanInfo,
  type PoolCardEntry,
} from './api'
import type {
  AnchorNode,
  BootstrapData,
  CharacterPool,
  ChatMessage,
  DistillProgress,
  GameOption,
  GoldenFingerProposal,
  QuestEstimate,
  QuestKind,
  RelayState,
  RosterEntry,
  SaveMeta,
  StartPayload,
  StreamEvent,
  UploadInfo,
} from './types'

type MobilePanel = 'setup' | 'story' | 'state'
type WorkbenchVariant = 'web' | 'windows' | 'mobile-web' | 'android'
type RosterRole = '伙伴' | '主线'

const props = withDefaults(defineProps<{ variant?: WorkbenchVariant }>(), {
  variant: 'web',
})
type SkillMode = 'describe' | 'upload'
type PoolSlotKey = '主角栏' | '伴侣栏' | '伙伴栏' | '宿敌栏'

interface EditableRosterEntry extends RosterEntry {
  model_id: string
  skill_mode: SkillMode
  skill_upload: UploadInfo | null
  /** 通用性格预设名（空串=跟随设定）；开局打包进 member_pack */
  persona_preset: string
}

const bootstrap = ref<BootstrapData | null>(null)
const booting = ref(true)
const error = ref('')
const status = ref('等待配置')
const busy = ref(false)
const sessionId = ref<string | null>(null)
// 窗口壳由入口显式传入 variant；pywebview 仅由 PlatformAdapter 执行窗控。
const isWindowed = computed(() => props.variant === 'windows')
const isMobileShell = computed(() => props.variant === 'mobile-web' || props.variant === 'android')
const showsLanAccess = computed(() => props.variant === 'web' || props.variant === 'windows')
function winControl(action: 'minimize' | 'toggle' | 'close'): void {
  if (action === 'minimize') platform.minimizeWindow()
  else if (action === 'toggle') platform.toggleMaximize()
  else platform.closeWindow()
}
const novelUpload = ref<UploadInfo | null>(null)
const personaUpload = ref<UploadInfo | null>(null)
const state = ref<Record<string, unknown>>({})
const chat = ref<ChatMessage[]>([])
const selectedOption = ref<string | null>(null)
// —— 增补通路（relay）：激活后选项进入多选模式，勾选后合并发送 ——
const relayActive = computed(() => Boolean((state.value as Partial<RelayState>).relay_active))
const relaySelectedKeys = ref<string[]>([])
const relaySupplement = ref('')
const askOpen = ref(false)
const askInput = ref('')
const askBusy = ref(false)
const askError = ref('')
const askThread = ref<Array<{ question: string; answer: string }>>([])
const actionInput = ref('')
const autoplayBusy = ref(false)
const mobilePanel = ref<MobilePanel>('story')
const basicOpen = ref(true)
const savesOpen = ref(true)
const protagonistOpen = ref(true)
const rosterOpen = ref(false)
const nemesisOpen = ref(true)
const modelOpen = ref(false)
const uiOpen = ref(false)
const saveList = ref<SaveMeta[]>([])
const savesLoading = ref(false)
const saveBusy = ref(false)
const loadingSaveId = ref<string | null>(null)
const savePointName = ref('')
const highlightedSaveId = ref<string | null>(null)
let highlightTimer: number | undefined
const goldenFingerChoices = ref<string[]>([])
const customGoldenFingerLabel = ref('自定义（由系统正式化后确认）')
const goldenFingerText = ref('')
const goldenFingerProposal = ref<GoldenFingerProposal | null>(null)
// 金手指生成/选定状态：一旦生成即锁定人物与难度等设定（用户要求的新机制）
const gfGenerated = ref(false)
const gfOpen = ref(false)
// 人物与难度设定确认状态：用户点"确定"后才能生成/选择金手指。
// 初始/自动联动一律不触发生成——只有显式确认才进入金手指阶段。
const setupConfirmed = ref(false)
const storyScroll = ref<HTMLElement | null>(null)
const workPickerOpen = ref(false)
const workQuery = ref('')
const fetchedModels = ref<string[]>([])
const fetchingModels = ref(false)
const testingConnection = ref(false)
const connectionResult = ref<{ ok: boolean; message: string } | null>(null)
const enableNemesis = ref(false)
const fontSize = ref<'standard' | 'large'>('standard')
const reduceMotion = ref(false)
const dropCapEnabled = ref(true)
const currentView = ref<'main' | 'designer' | 'resources'>('main')
const activeTheme = ref<ThemeId>('classic')

function selectTheme(id: ThemeId): void {
  activeTheme.value = id
  applyTheme(id)
}
const exportOpen = ref(false)
const questKind = ref<QuestKind>('short')
const questDifficulty = ref(0.5)
const questEstimate = ref<QuestEstimate | null>(null)
// —— 小猫隐藏入口：连续点击 19 次后，作弊码面板从小猫上方浮现。 ——
const catTapCount = ref(0)
let catTapTimer: number | undefined
function tapCat(): void {
  catTapCount.value += 1
  if (catTapTimer) window.clearTimeout(catTapTimer)
  catTapTimer = window.setTimeout(() => { catTapCount.value = 0 }, 8000)
  if (catTapCount.value >= 19) {
    catTapCount.value = 0
    // 羊皮纸哥特体铭文页：新页面打开（Web/窗口版共用；窗口壳会交给
    // 系统浏览器）。浏览器若拦截弹窗，回退到当前页导航，入口仍可用。
    const opened = window.open('/lomsting.html', '_blank', 'noopener')
    if (!opened) window.location.assign('/lomsting.html')
  }
}

// —— 羽毛笔：应用内「我的原著」章节阅读器（Web/窗口版共用）。 ——
const readerOpen = ref(false)
const openOriginalReader = () => { readerOpen.value = true }
// 局域网远程使用：手机扫码访问本机服务。
const lanQrOpen = ref(false)
const lanInfo = ref<LanInfo | null>(null)
const lanBusy = ref(false)
const mobileThemeOpen = ref(false)
const openLanQr = async () => {
  lanQrOpen.value = true
  lanBusy.value = true
  try {
    lanInfo.value = await fetchLanInfo(sessionId.value)
  } catch (err) {
    error.value = `获取局域网信息失败：${(err as Error).message}`
    lanQrOpen.value = false
  } finally {
    lanBusy.value = false
  }
}
const questBusy = ref(false)
const questDoneDismissed = ref(false)
const questFlash = ref('')
let questFlashTimer: number | undefined
const breakAnchorBusy = ref(false)
const compressionToastVisible = ref(false)
let compressionToastTimer: number | undefined
let compressionSeenKey = ''
let abortController: AbortController | null = null

const form = ref({
  provider: 'deepseek',
  base_url: '',
  api_key: '',
  model: '',
  thinking_mode: 'auto',
  thinking_param: '',
  mode: '基础模式',
  work: '',
  fragment: '',
  role: '',
  timepoint: '故事开篇',
  difficulty: 'D4 普通',
  convergence: '较高',
  story_richness: 700,
  story_agent_mode: false,
  golden_finger: '',
  persona_preset: '',
  persona_custom: '',
  distill_enabled: true,
  companion_count: 0,
  heroine_count: 0,
})

const companionRoster = ref<EditableRosterEntry[]>([])
const heroineRoster = ref<EditableRosterEntry[]>([])

const activeProvider = computed(() =>
  bootstrap.value?.providers.find((item) => item.id === form.value.provider),
)
const availableModels = computed(() =>
  fetchedModels.value.length ? fetchedModels.value : (activeProvider.value?.models ?? []),
)
const enhanced = computed(() => form.value.mode.startsWith('强化'))
// 设定锁定：金手指生成后 或 设定已确认（进入金手指阶段）均锁人物/难度
const setupLocked = computed(() => inGame.value || busy.value || gfGenerated.value || setupConfirmed.value)
// 模型与参数不受金手指锁定影响（与人物/难度设定无关，开局仍可调整）
const modelLocked = computed(() => inGame.value || busy.value)
// 金手指生成前置校验：主角/伴侣/伙伴/宿敌/难度全部确定 + 用户已点"确定设定"。
// 数量即事实：定了几个伙伴/伴侣，名单就读取几行；每行选了池卡或手填了姓名即视为已配置
//（与后端一致：后端仅要求 name，空行静默丢弃）。数量选 0 的栏视为已确定。
// 宿敌：勾了才有——勾选后才要求选卡；不勾则完全跳过，不算"不齐备"。
const gfPrerequisites = computed(() => {
  const problems: string[] = []
  // 四栏角色均可留空：卡和性格都只是「魂」，空位的名字开局由模型分配——
  // 主角默认穿成原著主角，其余默认穿成性格最类似/最贴合的原著角色。
  if (form.value.heroine_count > 0 && heroineRoster.value.length !== form.value.heroine_count) problems.push('伴侣')
  if (form.value.companion_count > 0 && companionRoster.value.length !== form.value.companion_count) problems.push('伙伴')
  if (!form.value.difficulty) problems.push('难度')
  return { ok: problems.length === 0 && setupConfirmed.value, problems }
})
const gfLockHint = computed(() => {
  if (gfGenerated.value) return '金手指已生成，人物与难度设定已锁定；如需修改请刷新页面重新开局'
  if (setupConfirmed.value) return '设定已确认，正在选择金手指；如需修改人物请刷新页面重新开局'
  return ''
})
// 当前金手指的 GF 缩放值（GF(D)=D^1.15，与后端 engine.golden_finger.gf_scale 一致）
const gfValue = computed(() => {
  const d = displayNemesisDifficulty.value ?? (10 - playerDifficultyNum.value)
  return Math.round(Math.pow(d, 1.15) * 100) / 100
})
// 故事丰富度：刻度与档位由后端统一下发，前端只渲染滑块与说明。
const DEFAULT_RICHNESS_TIERS = [
  { upper: 450, label: '轻盈', note: '轻量模型也能稳定达成，适合快节奏推进' },
  { upper: 650, label: '适中', note: '主流模型可稳定达成，叙事与节奏平衡' },
  { upper: 820, label: '厚重', note: '建议使用带思考模式的模型，场景更完整' },
  { upper: 1000, label: '沉浸', note: '需要强模型并开启思考模式，否则容易触发门禁回滚' },
]
const richnessConfig = computed(() => ({
  min: bootstrap.value?.story_richness?.min ?? 300,
  max: bootstrap.value?.story_richness?.max ?? 1000,
  step: bootstrap.value?.story_richness?.step ?? 50,
  default: bootstrap.value?.story_richness?.default ?? 700,
}))
const richnessTiers = computed(() => bootstrap.value?.story_richness?.tiers ?? DEFAULT_RICHNESS_TIERS)
const richnessTier = computed(() => {
  const value = form.value.story_richness
  return richnessTiers.value.find((tier) => value <= tier.upper) ?? richnessTiers.value[richnessTiers.value.length - 1]
})
const richnessThinkingHint = computed(() => form.value.story_richness > richnessTiers.value[1].upper)
// 对局中的丰富度：后端归一化后的值与档位，回放旧存档时显示为「—」。
const stateRichnessValue = computed(() => {
  const raw = Number(state.value.story_richness)
  return Number.isFinite(raw) && raw > 0 ? Math.round(raw) : null
})
const stateRichnessLabel = computed(() => {
  if (!stateRichnessValue.value) return '—'
  const tier = richnessTiers.value.find((item) => stateRichnessValue.value! <= item.upper)
    ?? richnessTiers.value[richnessTiers.value.length - 1]
  return `${tier.label} · ${stateRichnessValue.value}`
})
const libraryGoldenFingers = computed(() => bootstrap.value?.golden_finger_library ?? [])
// 通用性格预设（莽夫/谋士等，无 IP）：与角色卡并列为主角性格候选，preset:: 前缀区别于卡 id
const PRESET_PREFIX = 'preset::'
const genericPersonas = computed(() => (bootstrap.value?.personas ?? []).filter((p) => !p.startsWith('自定义')))
const selectedPersonaPreset = computed(() => {
  const value = selectedPoolCards.value['主角栏']
  return value.startsWith(PRESET_PREFIX) ? value.slice(PRESET_PREFIX.length) : ''
})
const goldenFingerSelectOptions = computed(() => {
  const custom = customGoldenFingerLabel.value
  const none = goldenFingerChoices.value.find((item) => item.startsWith('无（')) ?? '无（凡人开局）'
  const recs = goldenFingerChoices.value.filter((item) => item !== custom && item !== none)
  const seen = new Set(recs)
  const library = libraryGoldenFingers.value
    .map((item) => item.label)
    .filter((label) => label && !seen.has(label) && label !== custom && label !== none)
  return [...recs, ...library, none, custom].filter(Boolean)
})
const customGoldenFinger = computed(() => form.value.golden_finger === customGoldenFingerLabel.value)
const customGoldenFingerReady = computed(() => !customGoldenFinger.value || goldenFingerProposal.value?.status === 'confirmed')
const worksCount = computed(() => bootstrap.value?.works.length ?? 0)
const poolsCount = computed(() => bootstrap.value?.character_pools.length ?? 0)
// 金手指推荐语境：优先用主角栏已选卡名。
const personaForRecommend = computed(() => {
  const protagonistCard = poolCardById('主角栏', selectedPoolCards.value['主角栏'])
  if (protagonistCard) return `${protagonistCard.name}（${protagonistCard.work || '原创'}）`
  return '未选择'
})
// 作品列表分页
const WORK_PAGE_SIZE = 50
const workPage = ref(1)
const filteredWorks = computed(() => {
  const works = bootstrap.value?.works ?? []
  const query = workQuery.value.trim().toLowerCase()
  return query ? works.filter((work) => work.toLowerCase().includes(query)) : works
})
const pagedWorks = computed(() => filteredWorks.value.slice(0, workPage.value * WORK_PAGE_SIZE))
const hasMoreWorks = computed(() => pagedWorks.value.length < filteredWorks.value.length)
function loadMoreWorks() { workPage.value++ }
watch(workQuery, () => { workPage.value = 1 })
const workValid = computed(() => Boolean(form.value.work) && (bootstrap.value?.works ?? []).includes(form.value.work))
const round = computed(() => numeric(state.value.round, 0))
const chapter = computed(() => numeric(state.value.current_chapter, 1))
const chapterRound = computed(() => numeric(state.value.chapter_round, 0))
const turnBudget = computed(() => numeric(state.value.turn_budget, 0))
const compatibility = computed(() => state.value.last_compatibility_k ?? '未计算')
const activeMembers = computed(() => arrayOfRecords(state.value.active_members))
const ripple = computed(() => recordOf(state.value.last_ripple))
const openingStep = computed<'gf' | 'opening' | null>(() => {
  if (!enhanced.value) return null
  const s = state.value
  if (s.game_ready !== true) return null
  if (s.gf_confirmed !== true) return 'gf'
  if (s.opening_confirmed !== true) return 'opening'
  return null
})
// 开局确认阶段输入栏可用；正式开局后禁用输入栏，改用 A–F 选项按钮。
const openingInputDisabled = computed(() => busy.value || !openingStep.value)
const openingInputPlaceholder = computed(() =>
  openingStep.value === 'gf'
    ? '输入「确认金手指」或点上方按钮'
    : openingStep.value === 'opening'
      ? '输入「确认开局」或点上方按钮'
      : '请使用下方选项按钮推进',
)

const QUEST_KIND_OPTIONS: Array<{ value: QuestKind; label: string }> = [
  { value: 'short', label: '短任务 · 约 1 章' },
  { value: 'medium', label: '中任务 · 约 3 章' },
  { value: 'long', label: '长任务 · 约 6 章' },
]
const quest = computed(() => recordOf(state.value.quest))
const questStatus = computed(() => String(quest.value.status ?? 'none'))
// 后端任务契约里 title/requirements/goal/plot_hook 平铺在 quest 顶层，而非 offer 子对象。
const questOffer = computed(() => quest.value)
const questRequirements = computed<string[]>(() =>
  Array.isArray(questOffer.value.requirements)
    ? questOffer.value.requirements.filter((item): item is string => typeof item === 'string')
    : [],
)
// 奖励以中文条目展示（type + amount + unit），而不是平铺 reward 的内部英文字段。
const questRewardEntries = computed<Array<[string, string]>>(() => {
  const reward = recordOf(quest.value.reward)
  const entries: Array<[string, string]> = []
  const items = reward.items
  if (Array.isArray(items)) {
    for (const item of items) {
      const rewardItem = recordOf(item)
      const type = text(rewardItem.type, '奖励')
      const amount = numeric(rewardItem.amount, Number.NaN)
      const unit = String(rewardItem.unit ?? '')
      const value = Number.isFinite(amount) ? `${amount}${unit}` : text(rewardItem.amount, '—')
      entries.push([type, value])
    }
  }
  const relief = numeric(reward.convergence_relief, Number.NaN)
  if (Number.isFinite(relief) && relief > 0) {
    entries.push(['收束松弛', relief.toFixed(2)])
  }
  return entries
})
const questAcceptedRound = computed(() => numeric(quest.value.accepted_round, Number.NaN))
const questDeadlineRound = computed(() => numeric(quest.value.deadline_round, Number.NaN))
const questRemaining = computed(() =>
  Number.isFinite(questDeadlineRound.value) ? Math.max(0, questDeadlineRound.value - round.value) : null,
)
const questElapsed = computed(() =>
  Number.isFinite(questAcceptedRound.value) ? Math.max(0, round.value - questAcceptedRound.value) : 0,
)
const questTotal = computed(() =>
  Number.isFinite(questAcceptedRound.value) && Number.isFinite(questDeadlineRound.value)
    ? Math.max(1, questDeadlineRound.value - questAcceptedRound.value)
    : 0,
)
const questProgressRatio = computed(() => (questTotal.value ? clamp01(questElapsed.value / questTotal.value) : 0))
const showQuestGenerator = computed(() => questStatus.value === 'none' || questDoneDismissed.value)
const questSettlementKey = computed(() => {
  const settlement = quest.value.last_settlement
  return settlement ? JSON.stringify(settlement) : ''
})

const convergenceState = computed(() => recordOf(state.value.convergence_state))
const convergenceAvailable = computed(() => Number.isFinite(Number(convergenceState.value.position)))
const convergencePosition = computed(() => clamp01(numeric(convergenceState.value.position, 0.5)))
const convergenceSettled = computed(() => clamp01(numeric(convergenceState.value.last_settled_position, 0.5)))
const convergenceEffective = computed(() =>
  typeof convergenceState.value.effective === 'string' ? convergenceState.value.effective : '',
)
const momentumBar = computed(() => {
  const fromState = recordOf(state.value.momentum_bar)
  if (Number.isFinite(Number(fromState.threshold))) return fromState
  return recordOf(recordOf(state.value.break_anchor).momentum_bar)
})
const momentumRatio = computed(() => clamp01(numeric(momentumBar.value.ratio, 0)))
const momentumReady = computed(() => Boolean(momentumBar.value.ready))
const breakAnchor = computed(() => recordOf(state.value.break_anchor))
const breakAnchorStatus = computed(() => String(breakAnchor.value.status ?? 'idle'))
const breakAnchorStage = computed(() => {
  const stages = Array.isArray(breakAnchor.value.stages) ? breakAnchor.value.stages : []
  const index = numeric(breakAnchor.value.current_stage, 0)
  const current = stages[index]
  return current && typeof current === 'object' ? recordOf(current) : {}
})
const breakAnchorDeadline = computed(() => numeric(breakAnchor.value.deadline_round, Number.NaN))
const breakAnchorRemaining = computed(() =>
  Number.isFinite(breakAnchorDeadline.value) ? Math.max(0, breakAnchorDeadline.value - round.value) : null,
)
const breakAnchorCanOffer = computed(() => Boolean(breakAnchor.value.can_offer) || momentumReady.value)
const chapterRatio = computed(() => (turnBudget.value > 0 ? clamp01(chapterRound.value / turnBudget.value) : 0))

const nemesisSummary = computed(() => {
  const raw = recordOf(state.value.nemesis_summary)
  return typeof raw.text === 'string' && raw.text.trim() ? raw : null
})
const nemesisDistortion = computed(() => clamp01(numeric(nemesisSummary.value?.distortion, 0)))
// 宿敌强度系数：开局后读 state；选卡时实时计算。
const playerDifficultyNum = computed(() => {
  const m = /D(\d)/.exec(form.value.difficulty)
  return m ? parseInt(m[1], 10) : 4
})
const nemesisCard = computed(() => poolCardById('宿敌栏', selectedPoolCards.value['宿敌栏']))
// 主角团综合评估（与后端 engine/faction.py 双团对抗模型一致）：
// effective = power^2/4 × scope × permanence；成员超 3 人按 15% 边际衰减；
// 主角本人按 power 2 折算计入。
function memberEffective(power: number, scope = 1, permanence = 1): number {
  return (power * power / 4) * scope * permanence
}
function factionAggregate(members: Array<{ power: number; scope?: number; permanence?: number }>): number {
  if (!members.length) return 0
  const total = members.reduce((sum, m) => sum + memberEffective(m.power, m.scope, m.permanence), 0)
  return total / (1 + 0.15 * (members.length - 1))
}
// 卡片来源 → 战力推断（与后端 _nemesis_card_power / infer_power 对齐）
function cardPower(card: PoolCardEntry | null): number {
  if (!card) return 2
  const pos = card.original_position || ''
  if (pos === '反派') return 4
  if (pos === '主角' || pos === '男主' || pos === '女主') return 3
  if (pos === '配角') return 2
  return 2
}
// 前端实时计算（浮点）：base = 10 - playerDifficulty，
// delta = 宿敌方综合 - 主角团综合（主角 + 伙伴 + 伴侣），非线性映射。
const liveNemesisDifficulty = computed(() => {
  // 宿敌选择存在即计算：角色卡按卡面定位推断战力；通用性格宿敌无卡面，
  // 按后端 assess_faction_gap 对无卡成员的默认战力 2 处理（与后端公式一致）。
  if (!selectedPoolCards.value['宿敌栏']) return null
  const pd = playerDifficultyNum.value
  const base = 10.0 - pd
  // 主角团：主角（power 2）+ 已选伙伴卡 + 已选伴侣卡
  const ourMembers = [
    ...companionRoster.value
      .filter((entry) => entry.model_id)
      .map((entry) => ({ power: cardPower(poolCardById('伙伴栏', entry.model_id)) })),
    ...heroineRoster.value
      .filter((entry) => entry.model_id)
      .map((entry) => ({ power: cardPower(poolCardById('伴侣栏', entry.model_id)) })),
  ].slice(0, 3)
  const ourTotal = memberEffective(2) + factionAggregate(ourMembers)
  // 宿敌方：宿敌卡本身
  const theirTotal = memberEffective(cardPower(nemesisCard.value))
  const delta = theirTotal - ourTotal
  // 非线性：delta>0（宿敌方更强）→ D 降低；delta<0 → D 升高
  let correction = 0
  if (delta > 0) correction = -(3.0 * (1 - Math.exp(-delta * 0.8)))
  else if (delta < 0) correction = 2.0 * (1 - Math.exp(delta * 0.8))
  const result = base + correction
  return Math.round(Math.max(0.01, Math.min(9.99, result)) * 100) / 100
})
const displayNemesisDifficulty = computed(() => {
  const nd = numeric(state.value.nemesis_difficulty, 0)
  return nd > 0 ? Math.round(nd * 100) / 100 : (liveNemesisDifficulty.value ?? null)
})
// 进度条位置：D0.01（最强）在左端，D9.99（最弱）在右端
const nemesisBarRatio = computed(() => {
  const d = displayNemesisDifficulty.value
  if (d == null) return 0.5
  return Math.max(0, Math.min(1, (d - 0.01) / (9.99 - 0.01)))
})

const compressionRecord = computed(() => {
  const raw = recordOf(state.value.compression_record)
  return Number.isFinite(Number(raw.round)) ? raw : null
})
const compressionKept = computed(() => numeric(compressionRecord.value?.kept_messages, 0))
const compressionDegraded = computed(() => compressionRecord.value?.fidelity === 'degraded')

const OPTION_KEYS = ['A', 'B', 'C', 'D', 'E', 'F'] as const
const options = computed<GameOption[]>(() => {
  const raw = state.value.options
  if (!Array.isArray(raw)) return []
  const result: GameOption[] = []
  for (const item of raw) {
    const record = recordOf(item)
    const key = String(record.key ?? '')
    const optionText = typeof record.text === 'string' ? record.text.trim() : ''
    if ((OPTION_KEYS as readonly string[]).includes(key) && optionText) {
      const factors = Array.isArray(record.factors)
        ? record.factors.filter((factor): factor is string => typeof factor === 'string')
        : undefined
      result.push({ key: key as GameOption['key'], text: optionText, factors })
    }
  }
  return result
})

interface TimelineEntry {
  kind: 'past' | 'current' | 'upcoming'
  node: AnchorNode
  fade: number
}

const timelineNodes = computed<TimelineEntry[]>(() => {
  const timeline = recordOf(state.value.anchor_timeline)
  if (!Object.keys(timeline).length) return []
  const entries: TimelineEntry[] = []
  const past = arrayOfRecords(timeline.past).map(anchorNodeOf).filter((node): node is AnchorNode => Boolean(node))
  const lastPast = past[past.length - 1]
  if (lastPast) entries.push({ kind: 'past', node: lastPast, fade: 1 })
  const current = anchorNodeOf(timeline.current)
  if (current) entries.push({ kind: 'current', node: current, fade: 1 })
  arrayOfRecords(timeline.upcoming)
    .map(anchorNodeOf)
    .filter((node): node is AnchorNode => Boolean(node))
    .slice(0, 3)
    .forEach((node, index) => entries.push({ kind: 'upcoming', node, fade: 1 - index * 0.3 }))
  return entries
})
const timelineKey = computed(() => {
  const current = timelineNodes.value.find((entry) => entry.kind === 'current')
  return current ? `${current.node.chapter}-${current.node.title}` : 'idle'
})

function anchorNodeOf(value: unknown): AnchorNode | null {
  const record = recordOf(value)
  if (!Object.keys(record).length) return null
  const title = typeof record.title === 'string' ? record.title : ''
  if (!title) return null
  return {
    chapter: numeric(record.chapter, 0),
    title,
    status: typeof record.status === 'string' ? record.status : undefined,
    summary: typeof record.summary === 'string' ? record.summary : undefined,
  }
}
const startDisabled = computed(() => {
  if (busy.value || !customGoldenFingerReady.value) return true
  // 新流程：必须确认设定并选定金手指（或生成推荐）后才能开局
  if (!setupConfirmed.value) return true
  if (!form.value.golden_finger) return true
  if (enhanced.value) return !novelUpload.value
  return !workValid.value
})
const inGame = computed(() => Boolean(sessionId.value && state.value.game_ready === true))

// —— 刷新恢复：sessionId 持久化 + 启动时自动找回会话（服务端内存丢失时由
//    磁盘存档回填），刷新页面不再丢档。 ——
const SESSION_KEY = 'fate_session_id'

watch(sessionId, (id) => {
  if (typeof window === 'undefined') return
  if (id) window.localStorage.setItem(SESSION_KEY, id)
  else window.localStorage.removeItem(SESSION_KEY)
})

async function restoreSession(id: string, source: 'link' | 'storage' | 'sync' = 'storage'): Promise<boolean> {
  if (!id) return false
  try {
    const data = await fetchSessionState(id)
    const restored = (data?.state ?? {}) as Record<string, unknown>
    const history = Array.isArray(restored.history) ? restored.history as ChatMessage[] : []
    const usable = restored.game_ready === true || history.length > 0
    if (!usable) {
      if (source !== 'sync') window.localStorage.removeItem(SESSION_KEY)
      return false
    }
    sessionId.value = id
    state.value = restored
    chat.value = history
    askThread.value = []
    // 连接与开局关键配置恢复，避免玩家重选（凭据不落盘，需在连接区重填）。
    if (typeof restored.provider === 'string' && restored.provider) form.value.provider = restored.provider
    if (typeof restored.base_url === 'string' && restored.base_url) form.value.base_url = restored.base_url
    if (typeof restored.model === 'string' && restored.model) form.value.model = restored.model
    const sp = (restored.start_params ?? {}) as Record<string, unknown>
    for (const key of ['mode', 'difficulty', 'convergence', 'golden_finger'] as const) {
      if (typeof sp[key] === 'string' && sp[key]) (form.value as Record<string, unknown>)[key] = sp[key]
    }
    if (source !== 'sync') {
      status.value = source === 'link'
        ? '已接续电脑端会话（同一 Wi-Fi 设备可控制当前对局）'
        : '已恢复上次会话（刷新不丢档；如需模型回复请在连接区重填 API Key）'
      mobilePanel.value = 'story'
      await scrollToBottom()
    }
    return true
  } catch {
    // URL 会话不存在时不清本机已保存会话，避免无效链接破坏原恢复入口。
    if (source === 'storage') window.localStorage.removeItem(SESSION_KEY)
    return false
  }
}

async function tryRestoreSession(): Promise<void> {
  if (typeof window === 'undefined') return
  const linked = new URLSearchParams(window.location.search).get('session')
  if (linked && await restoreSession(linked, 'link')) return
  const stored = window.localStorage.getItem(SESSION_KEY)
  if (stored) await restoreSession(stored, 'storage')
}

async function syncSessionFromServer(): Promise<void> {
  if (busy.value || !sessionId.value) return
  await restoreSession(sessionId.value, 'sync')
}

// —— 锚点蒸馏进度：右侧小窗口数据源（强化模式开局前后持续轮询） ——
const distillProgress = ref<DistillProgress | null>(null)
const sessionEnhanced = computed(() =>
  String(state.value.mode ?? '').startsWith('强化') || enhanced.value)
let distillTimer: ReturnType<typeof setInterval> | null = null
let distillMisses = 0

/** 会话在服务端已不存在（连续 404）：清理本地状态回到全新开局。
 *  玩家正看到的那局聊天记录随之丢弃——存档也被清理时本地已无可恢复之源，
 *  比留在永远 404 的死会话里更诚实。 */
function handleStaleSession(): void {
  if (distillTimer) {
    clearInterval(distillTimer)
    distillTimer = null
  }
  distillProgress.value = null
  sessionId.value = null
  state.value = {}   // options 是由 state 推导的 computed，清 state 即清选项
  chat.value = []
  askThread.value = []
  status.value = '上次会话已在服务端失效，已回到全新开局（刷新前的游玩记录可在存档列表读取）'
  error.value = ''
}

async function refreshDistillProgress(): Promise<void> {
  if (!sessionId.value) return
  try {
    distillProgress.value = await fetchDistillProgress(sessionId.value)
    distillMisses = 0
  } catch (err) {
    // 轮询失败静默重试；但连续 404 说明会话在服务端已不存在（存档被清/服务
    // 重置后无档可回填），继续轮询只会刷日志——达到阈值自动退出到新局。
    if (err instanceof Error && err.message.includes('404')) {
      distillMisses += 1
      if (distillMisses >= 3) {
        distillMisses = 0
        handleStaleSession()
      }
    }
  }
}

function syncDistillPolling(): void {
  const shouldPoll = Boolean(sessionId.value) && sessionEnhanced.value
  if (shouldPoll && !distillTimer) {
    void refreshDistillProgress()
    distillTimer = setInterval(refreshDistillProgress, 5000)
  } else if (!shouldPoll && distillTimer) {
    clearInterval(distillTimer)
    distillTimer = null
    distillProgress.value = null
  }
}

watch([sessionId, sessionEnhanced], syncDistillPolling, { immediate: true })
onBeforeUnmount(() => {
  window.removeEventListener('focus', syncSessionFromServer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (catTapTimer) window.clearTimeout(catTapTimer)
  if (distillTimer) {
    clearInterval(distillTimer)
    distillTimer = null
  }
})

// —— 本局 Token 用量：实测与估算来源分开累计，避免混合值被误标为实测。 ——
function fmtTok(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0'
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  if (n >= 1000) return (n / 1000).toFixed(1) + '千'
  return String(Math.round(n))
}
const tokenUsage = computed(() => {
  const s = state.value as Record<string, unknown>
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
  const last = Array.isArray(s.tok_last) ? (s.tok_last as number[]) : [0, 0]
  const measured = num(s.tok_measured_in) + num(s.tok_measured_out)
  const estimated = num(s.tok_estimated_in) + num(s.tok_estimated_out)
  return {
    in: num(s.tok_in), out: num(s.tok_out), cache: num(s.tok_cache),
    lastIn: num(last[0]), lastOut: num(last[1]),
    source: measured && estimated ? 'mixed' : measured ? 'measured' : estimated ? 'estimated' : 'unknown',
  }
})

const distillRatio = computed(() => {
  const p = distillProgress.value
  if (!p?.enabled || !p.total) return 0
  return Math.min(1, (p.done ?? 0) / p.total)
})
const distillWindowChapters = computed(() => distillProgress.value?.chapters ?? [])
const savePointDefault = computed(() => `第${round.value + 1}回合`)
const modelConnection = computed(() => ({
  provider: form.value.provider,
  base_url: form.value.base_url,
  api_key: form.value.api_key,
  model: form.value.model,
}))

// 叙事渲染（正文分块缓存 + 反向一次扫描的焦点距离）由共享 kernel 维护。
const { chatView } = useNarrativeView(chat)

async function onDesignerSaved(label: string): Promise<void> {
  // 设计器产物入数据库；刷新四栏池让新卡立即出现在候选中。
  try {
    bootstrap.value = await getBootstrap()
    loadAllPoolSlots()
  } catch {
    /* 静默：返回主界面时会再次拉取 */
  }
  status.value = `角色已入库：${label || '未命名角色'}`
  currentView.value = 'main'
  mobilePanel.value = 'setup'
  protagonistOpen.value = true
}

function onLibraryChanged(): void {
  /* 角色库入口已下线；自定义角色统一走角色设计器生成入库。 */
  void 0
}

function numeric(value: unknown, fallback: number): number {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value))
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function arrayOfRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : []
}

function text(value: unknown, fallback: unknown = '未记录'): string {
  if (value === null || value === undefined || value === '') return String(fallback)
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value, null, 2)
}

function newRosterEntry(role: RosterRole): EditableRosterEntry {
  return {
    role_type: role,
    model_id: '',
    skill_mode: 'describe',
    skill_upload: null,
    name: '',
    background: '',
    participation: 5,
    skill: '',
    custom_skill: '',
    skill_upload_id: null,
    persona_preset: '',
    character_model: '',
    character_model_source: '',
    character_card: {
      goal: '',
      fear: '',
      abilities: [],
      relationship_vector: {},
      knowledge_scope: [],
      speech_style: '',
      unacceptable_behaviors: [],
    },
  }
}

function resizeRoster(role: RosterRole): void {
  if (role === '伙伴') {
    const target = Math.max(0, Math.min(20, Math.trunc(form.value.companion_count)))
    form.value.companion_count = target
    companionRoster.value = resized(companionRoster.value, target, role)
  } else {
    const target = Math.max(0, Math.min(10, Math.trunc(form.value.heroine_count)))
    form.value.heroine_count = target
    heroineRoster.value = resized(heroineRoster.value, target, role)
  }
}

function resized(source: EditableRosterEntry[], target: number, role: RosterRole): EditableRosterEntry[] {
  const rows = source.slice(0, target)
  while (rows.length < target) rows.push(newRosterEntry(role))
  return rows
}

function poolFor(role: RosterRole): CharacterPool[] {
  if (!bootstrap.value) return []
  return bootstrap.value.character_pools.filter((item) => item.role === '伙伴')
}

function customTag(modelId: string): string {
  const customIds = bootstrap.value?.custom_character_ids ?? []
  return customIds.includes(modelId) ? '〔自定义〕' : ''
}

function applyCharacter(entry: EditableRosterEntry): void {
  const model = bootstrap.value?.character_pools.find((item) => item.id === entry.model_id)
  if (!model) {
    entry.character_model = ''
    entry.character_model_source = ''
    return
  }
  entry.name = model.name
  entry.background = model.background
  entry.character_model = model.name
  entry.character_model_source = model.source
  entry.gender = model.gender
  entry.protagonist_gender = model.protagonist_gender
  entry.character_card = {
    goal: model.desire,
    fear: model.fear,
    abilities: [...model.abilities],
    relationship_vector: { ...model.relationship_vector },
    knowledge_scope: [...model.knowledge_scope],
    speech_style: model.voice,
    unacceptable_behaviors: [...model.unacceptable_actions],
  }
}

function rosterPayload(rows: EditableRosterEntry[]): RosterEntry[] {
  return rows.map(({ model_id: _modelId, skill_mode: _skillMode, skill_upload, ...entry }) => ({
    ...entry,
    participation: numeric(entry.participation, 5),
    skill: skill_upload?.filename || entry.skill,
    custom_skill: entry.skill,
    skill_upload_id: skill_upload?.upload_id ?? null,
  }))
}

function onProviderChanged(): void {
  const provider = activeProvider.value
  if (!provider) return
  form.value.base_url = provider.base_url
  fetchedModels.value = []
  connectionResult.value = null
  form.value.model = provider.models[0] ?? ''
}

function onModeChanged(): void {
  if (enhanced.value) {
    form.value.work = ''
    form.value.timepoint = '故事开篇'
    // 强化模式的开工确认与回合门禁依赖锚点蒸馏，强制开启（后端同款兜底）。
    form.value.distill_enabled = true
  } else if (!workValid.value) {
    form.value.work = bootstrap.value?.works[0] ?? ''
    enableNemesis.value = false
  }
  // 切换模式使设定失效：回退到"待确认"，用户需重新点"确定设定"
  setupConfirmed.value = false
  gfGenerated.value = false
}

function selectWork(work: string): void {
  form.value.work = work
  workPickerOpen.value = false
  workQuery.value = ''
  workPage.value = 1
}

function goldenFingerContext(): { world: string; persona: string; difficulty: string; nemesis_d: number } {
  return {
    world: enhanced.value ? String(novelUpload.value?.filename || '') : form.value.work,
    persona: personaForRecommend.value,
    difficulty: form.value.difficulty,
    // 宿敌强度 D（GF(D)=D^1.15 缩放的输入）：未启用宿敌系统时按玩家难度反推
    nemesis_d: displayNemesisDifficulty.value ?? (10 - playerDifficultyNum.value),
  }
}

// 用户显式点击"确定设定"：四类人物+难度齐备后进入金手指阶段。
// 注意：此处只检查 problems（不含 setupConfirmed）——setupConfirmed 正是本函数要设置的，
// 若检查 ok（含 setupConfirmed）会形成"永远无法首次确认"的死循环。
function confirmSetup(): void {
  const problems = gfPrerequisites.value.problems
  if (problems.length === 0) {
    setupConfirmed.value = true
    gfOpen.value = true
    error.value = ''
    status.value = '设定已确认，请选择金手指'
    return
  }
  error.value = `人物与难度尚未齐备：${problems.join('、')}`
}

async function refreshGoldenFingers(): Promise<void> {
  if (!bootstrap.value) return
  // 必须先点"确定设定"（人物+难度齐备并确认）才能生成金手指。
  if (!gfPrerequisites.value.ok) {
    error.value = gfPrerequisites.value.problems.length
      ? `需先确定：${gfPrerequisites.value.problems.join('、')}`
      : '需先点击"确定人物与难度设定"'
    return
  }
  if (gfGenerated.value) return  // 已生成，锁定：不可再刷新（设定已冻结）
  try {
    const context = goldenFingerContext()
    const result = await recommendGoldenFingers(context.world, context.persona, context.difficulty, context.nemesis_d)
    goldenFingerChoices.value = result.choices
    customGoldenFingerLabel.value = result.custom_label
    form.value.golden_finger = result.choices[0] ?? result.none_label
    goldenFingerProposal.value = null
    goldenFingerText.value = ''
    // 金手指已生成：锁定人物/难度等设定
    gfGenerated.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '金手指推荐刷新失败'
  }
}

function onGoldenFingerChanged(): void {
  if (!customGoldenFinger.value) {
    goldenFingerProposal.value = null
    goldenFingerText.value = ''
  }
}

async function proposeCustomGoldenFinger(): Promise<void> {
  if (!goldenFingerText.value.trim()) return
  if (!gfPrerequisites.value.ok) {
    error.value = gfPrerequisites.value.problems.length
      ? `需先确定：${gfPrerequisites.value.problems.join('、')}`
      : '需先点击"确定人物与难度设定"'
    return
  }
  error.value = ''
  try {
    const context = goldenFingerContext()
    const attempt = (goldenFingerProposal.value?.attempt ?? 0) + 1
    goldenFingerProposal.value = await proposeGoldenFinger(
      goldenFingerText.value,
      context.world,
      context.persona,
      context.difficulty,
      attempt,
      context.nemesis_d,
    )
    // 自定义提案发起即视为进入金手指流程，锁定其他设定
    gfGenerated.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '提案生成失败'
  }
}

async function confirmCustomGoldenFinger(): Promise<void> {
  if (!goldenFingerProposal.value) return
  error.value = ''
  try {
    goldenFingerProposal.value = await confirmGoldenFinger(goldenFingerProposal.value)
    status.value = `已确认：${goldenFingerProposal.value.spec.name || '自定义金手指'}`
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '提案确认失败'
  }
}

// ---------------------------------------------------------------------------
// 角色池候选数据，嵌入开局各人物栏
// 主角性别决定伴侣栏过滤方向；跨栏同名即时提示；开局携带 roster_card_ids。
// ---------------------------------------------------------------------------

interface PoolSlotState {
  /** 两级分组：第一级来源（主角/男主/女主/配角/反派），第二级栏位分类 */
  groups: Array<{ key: string; sub_groups: Array<{ key: string; cards: PoolCardEntry[] }> }>
  loading: boolean
  error: string
  query: string
  /** 第一级选中的来源；空 = 全部来源 */
  category: string
  /** 第二级选中的具体分类；空 = 该来源下全部分类 */
  subtype: string
  /** 简介卡当前展示的卡 id（悬停优先，其次已选） */
  previewId: string
}

function emptyPoolSlotState(): PoolSlotState {
  return { groups: [], loading: false, error: '', query: '', category: '', subtype: '', previewId: '' }
}

const selectedPoolCards = ref<Record<PoolSlotKey, string>>({
  '主角栏': '',
  '伴侣栏': '',
  '伙伴栏': '',
  '宿敌栏': '',
})
const poolSlots = ref<Record<PoolSlotKey, PoolSlotState>>({
  '主角栏': emptyPoolSlotState(),
  '伴侣栏': emptyPoolSlotState(),
  '伙伴栏': emptyPoolSlotState(),
  '宿敌栏': emptyPoolSlotState(),
})

const POOL_SLOT_META: Array<{ slot: PoolSlotKey; label: string; note: string }> = [
  { slot: '主角栏', label: '主角栏', note: '先选来源，再选类型；全池可选' },
  { slot: '伴侣栏', label: '伴侣栏', note: '先选来源，再选类型；已剔除与主角同性别' },
  { slot: '伙伴栏', label: '伙伴栏', note: '先选来源，再选类型；全池可选' },
  { slot: '宿敌栏', label: '宿敌栏', note: '先选来源，再选类型；全池可选' },
]

async function loadPoolSlot(slot: PoolSlotKey): Promise<void> {
  const state = poolSlots.value[slot]
  state.loading = true
  state.error = ''
  try {
    // 性别栏杆已破除：四栏均不按性别过滤，卡和性格都只是「魂」，
    // 叙事以附身角色（书中身体）的生理性别为准。
    const result = await fetchCharacterPool(slot)
    state.groups = result.keys
  } catch (cause) {
    state.error = cause instanceof Error ? cause.message : '角色池加载失败'
  } finally {
    state.loading = false
  }
}

function loadAllPoolSlots(): void {
  POOL_SLOT_KEYS.forEach((slot) => {
    void loadPoolSlot(slot)
  })
}

// 角色池在开局配置加载时预取，候选控件直接嵌入对应配置栏。
onMounted(() => {
  loadAllPoolSlots()
})

function togglePoolCard(slot: PoolSlotKey, cardId: string): void {
  selectedPoolCards.value[slot] = selectedPoolCards.value[slot] === cardId ? '' : cardId
  poolSlots.value[slot].previewId = selectedPoolCards.value[slot]
}

function poolCardById(slot: PoolSlotKey, cardId: string): PoolCardEntry | null {
  if (!cardId) return null
  for (const group of poolSlots.value[slot].groups) {
    for (const sub of group.sub_groups) {
      const card = sub.cards.find((item) => item.id === cardId)
      if (card) return card
    }
  }
  return null
}

const POOL_SLOT_KEYS: PoolSlotKey[] = ['主角栏', '伴侣栏', '伙伴栏', '宿敌栏']

const selectedPoolCardNames = computed(() => {
  const entries: Array<{ slot: PoolSlotKey; name: string }> = []
  POOL_SLOT_KEYS.forEach((slot) => {
    const card = poolCardById(slot, selectedPoolCards.value[slot])
    if (card) entries.push({ slot, name: card.name })
  })
  return entries
})

// 重名即时提示：同一角色卡（同 id）在多个栏位被选，或同名卡出现在不同栏。
const duplicateNameWarnings = computed(() => {
  const byName = new Map<string, string[]>()
  selectedPoolCardNames.value.forEach(({ slot, name }) => {
    byName.set(name, [...(byName.get(name) ?? []), slot])
  })
  const warnings: string[] = []
  byName.forEach((slots, name) => {
    if (slots.length > 1) {
      warnings.push(`「${name}」被 ${slots.join('、')} 同时选中，开局将依世界观自动改名`)
    }
  })
  return warnings
})

// 两级筛选：category = 来源（主角/男主/女主/配角/反派），subtype = 栏位分类。
// category 为空显示全部来源；subtype 为空显示该来源下全部分类。
function filteredPoolGroups(slot: PoolSlotKey): Array<{ key: string; sub_groups: Array<{ key: string; cards: PoolCardEntry[] }> }> {
  const state = poolSlots.value[slot]
  const query = state.query.trim().toLowerCase()
  const match = (card: PoolCardEntry) =>
    !query || card.name.toLowerCase().includes(query)
  return state.groups
    .filter((group) => !state.category || group.key === state.category)
    .map((group) => ({
      key: group.key,
      sub_groups: group.sub_groups
        .filter((sub) => !state.subtype || sub.key === state.subtype)
        .map((sub) => ({ key: sub.key, cards: sub.cards.filter(match) }))
        .filter((sub) => sub.cards.length),
    }))
    .filter((group) => group.sub_groups.length)
}

// 第一级来源选项（固定顺序来自后端分组）。
function poolCategoryOptions(slot: PoolSlotKey): string[] {
  return poolSlots.value[slot].groups.map((group) => group.key).filter(Boolean)
}

// 第二级分类选项：当前来源（未选来源则全部来源）下的分类并集。
function poolSubtypeOptions(slot: PoolSlotKey): string[] {
  const state = poolSlots.value[slot]
  const seen = new Set<string>()
  state.groups
    .filter((group) => !state.category || group.key === state.category)
    .forEach((group) => group.sub_groups.forEach((sub) => seen.add(sub.key)))
  return [...seen].sort()
}

// 来源或分类切换后，第二级分类需要联动重置，避免残留无效选项。
function onPoolCategoryChanged(slot: PoolSlotKey): void {
  poolSlots.value[slot].subtype = ''
}

function onPoolQueryInput(slot: PoolSlotKey, event: Event): void {
  poolSlots.value[slot].query = (event.target as HTMLInputElement).value
  // 主栏搜索联动其余三栏，保持四栏一致体验。
  const keyword = poolSlots.value[slot].query
  POOL_SLOT_KEYS
    .filter((other) => other !== slot)
    .forEach((other) => { poolSlots.value[other].query = keyword })
}

// 简介卡：悬停中的卡优先，其次当前已选卡。
function poolPreviewCard(slot: PoolSlotKey): PoolCardEntry | null {
  const state = poolSlots.value[slot]
  return poolCardById(slot, state.previewId || selectedPoolCards.value[slot])
}

// 简介卡正文：优先一句话简介 → 背景 → 欲望，保证任何卡都有可读内容。
function poolPreviewText(card: PoolCardEntry): string {
  return card.background || card.desire || card.archetype || '暂无简介'
}

const totalPoolCards = computed(() => bootstrap.value?.counts?.character_pools ?? bootstrap.value?.character_pools.length ?? 0)

function rosterCardIdsPayload(): Array<{ slot: string; card_id: string }> {
  const result: Array<{ slot: string; card_id: string }> = []
  // 主角栏、宿敌栏仍用 selectedPoolCards（单选）；通用性格预设不是卡 id，不进卡列表
  if (selectedPoolCards.value['主角栏'] && !selectedPoolCards.value['主角栏'].startsWith(PRESET_PREFIX)) {
    result.push({ slot: '主角', card_id: selectedPoolCards.value['主角栏'] })
  }
  if (selectedPoolCards.value['宿敌栏'] && !selectedPoolCards.value['宿敌栏'].startsWith(PRESET_PREFIX)) {
    result.push({ slot: '宿敌', card_id: selectedPoolCards.value['宿敌栏'] })
  }
  // 伙伴栏、伴侣栏从 roster 条目提取（多选）
  companionRoster.value.forEach((entry) => {
    if (entry.model_id) result.push({ slot: '伙伴', card_id: entry.model_id })
  })
  heroineRoster.value.forEach((entry) => {
    if (entry.model_id) result.push({ slot: '主线', card_id: entry.model_id })
  })
  return result
}

function validSkillFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return name.endsWith('.md') || name.endsWith('.txt')
}

// 从 change（点击选择）或 drop（拖拽）事件中提取文件，兼容两种上传方式。
function fileFromEvent(event: Event): { file: File | null; input: HTMLInputElement | null } {
  const drag = event as DragEvent
  if (drag.dataTransfer?.files?.length) {
    return { file: drag.dataTransfer.files[0] ?? null, input: null }
  }
  const input = event.target as HTMLInputElement
  const isInput = input instanceof HTMLInputElement
  return { file: isInput ? (input.files?.[0] ?? null) : null, input: isInput ? input : null }
}

async function handleUpload(event: Event): Promise<void> {
  const { file, input } = fileFromEvent(event)
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.txt')) {
    error.value = '原著上传仅接受 TXT 文件'
    if (input) input.value = ''
    return
  }
  error.value = ''
  status.value = '正在上传原著'
  try {
    const result = await uploadTxt(file, sessionId.value)
    sessionId.value = result.session_id
    novelUpload.value = result.upload
    status.value = enhanced.value ? 'TXT 已上传，开局时将执行切章校验' : 'TXT 已上传，开局时将优先使用该文本'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '上传失败'
    status.value = '上传失败'
  }
}

async function handleRosterSkillUpload(entry: EditableRosterEntry, event: Event): Promise<void> {
  const { file, input } = fileFromEvent(event)
  if (!file) return
  if (!validSkillFile(file)) {
    error.value = '角色 Skill 仅接受 .md / .txt 文件'
    if (input) input.value = ''
    return
  }
  error.value = ''
  try {
    const result = await uploadSkill(file, sessionId.value)
    sessionId.value = result.session_id
    entry.skill_upload = result.upload
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '角色 Skill 上传失败'
  }
}

async function pullModelList(): Promise<void> {
  if (fetchingModels.value) return
  fetchingModels.value = true
  error.value = ''
  try {
    const result = await fetchModels({
      provider: form.value.provider,
      base_url: form.value.base_url,
      api_key: form.value.api_key,
    })
    fetchedModels.value = result.models ?? []
    if (fetchedModels.value.length && !fetchedModels.value.includes(form.value.model)) {
      form.value.model = fetchedModels.value[0]
    }
    connectionResult.value = { ok: true, message: result.message || `已拉取 ${fetchedModels.value.length} 个模型` }
  } catch (cause) {
    connectionResult.value = { ok: false, message: cause instanceof Error ? cause.message : '模型列表拉取失败' }
  } finally {
    fetchingModels.value = false
  }
}

async function runConnectionTest(): Promise<void> {
  if (testingConnection.value) return
  testingConnection.value = true
  error.value = ''
  try {
    connectionResult.value = await testModelConnection({
      provider: form.value.provider,
      base_url: form.value.base_url,
      api_key: form.value.api_key,
      model: form.value.model,
    })
  } catch (cause) {
    connectionResult.value = { ok: false, message: cause instanceof Error ? cause.message : '连接测试失败' }
  } finally {
    testingConnection.value = false
  }
}

function handleEvent(event: StreamEvent): void {
  if (event.type === 'error') {
    error.value = event.data.message || '生成失败'
    return
  }
  if (event.data.session_id) sessionId.value = event.data.session_id
  if (event.type === 'state') {
    if (Array.isArray(event.data.chat)) chat.value = event.data.chat
    if (event.data.state) state.value = event.data.state
    if (event.data.status) status.value = event.data.status
    void scrollToBottom()
  }
}

async function runStream(url: string, body: unknown): Promise<void> {
  abortController?.abort()
  abortController = new AbortController()
  busy.value = true
  error.value = ''
  try {
    await readNdjson(url, body, abortController.signal, handleEvent)
  } catch (cause) {
    if ((cause as DOMException)?.name !== 'AbortError') {
      error.value = cause instanceof Error ? cause.message : '请求失败'
    }
  } finally {
    busy.value = false
    abortController = null
    selectedOption.value = null
    relaySelectedKeys.value = []
  }
}

async function startGame(): Promise<void> {
  const payload: StartPayload = {
    session_id: sessionId.value,
    provider: form.value.provider,
    base_url: form.value.base_url,
    api_key: form.value.api_key,
    model: form.value.model,
    thinking_mode: form.value.thinking_mode,
    thinking_param: form.value.thinking_param,
    mode: form.value.mode,
    work: enhanced.value ? null : form.value.work,
    novel_upload_id: novelUpload.value?.upload_id ?? null,
    fragment: form.value.fragment,
    role: form.value.role,
    timepoint: form.value.timepoint,
    difficulty: form.value.difficulty,
    convergence: form.value.convergence,
    story_richness: form.value.story_richness,
    story_agent_mode: enhanced.value && form.value.story_agent_mode,
    golden_finger: form.value.golden_finger || null,
    golden_finger_proposal: goldenFingerProposal.value ?? {},
    persona_preset: selectedPersonaPreset.value || poolCardById('主角栏', selectedPoolCards.value['主角栏'])?.name || '',
    persona_custom: '',
    persona_upload_id: null,
    distill_enabled: form.value.distill_enabled,
    companion_roster: rosterPayload(companionRoster.value),
    heroine_roster: rosterPayload(heroineRoster.value),
    companion_count: form.value.companion_count,
    heroine_count: form.value.heroine_count,
    // 数量即事实：伴侣数量 >1 时自动按"多女主"提交，与后端单女主校验对齐
    heroine_mode: form.value.heroine_count > 1 ? '多女主' : '单女主',
    enable_nemesis: enableNemesis.value,
    nemesis_select: selectedPoolCards.value['宿敌栏'].startsWith(PRESET_PREFIX)
      ? selectedPoolCards.value['宿敌栏'].slice(PRESET_PREFIX.length)
      : poolCardById('宿敌栏', selectedPoolCards.value['宿敌栏'])?.name ?? '',
    nemesis_upload_id: null,
    roster_card_ids: rosterCardIdsPayload(),
  }
  mobilePanel.value = 'story'
  askThread.value = []
  askError.value = ''
  askInput.value = ''
  status.value = enhanced.value ? '正在校验原著并提取剧情' : '正在生成开场'
  await runStream('/api/sessions/start', payload)
}

function chooseOption(option: GameOption): void {
  if (busy.value || !inGame.value) return
  // 增补通路接通：点击仅切换勾选（可多选），由「按已选项行动」合并发送
  if (relayActive.value) {
    const index = relaySelectedKeys.value.indexOf(option.key)
    if (index >= 0) relaySelectedKeys.value.splice(index, 1)
    else relaySelectedKeys.value.push(option.key)
    return
  }
  if (selectedOption.value) return
  selectedOption.value = option.key
  window.setTimeout(() => {
    void sendUserContent(`选择${option.key}：${option.text}`)
  }, 220)
}

// 增补通路：把所有勾选选项合并成一条消息发送，附可选的本回合增补内容
function submitRelayChoices(): void {
  if (busy.value || !inGame.value) return
  const picked = options.value.filter((option) => relaySelectedKeys.value.includes(option.key))
  if (!picked.length) return
  const lines = picked.map((option) => `选择${option.key}：${option.text}`)
  const supplement = relaySupplement.value.trim()
  if (supplement) lines.push(`增补：${supplement}`)
  relaySelectedKeys.value = []
  relaySupplement.value = ''
  void sendUserContent(lines.join('\n'))
}

async function autoplay(): Promise<void> {
  if (busy.value || autoplayBusy.value || !inGame.value || !sessionId.value || !options.value.length) return
  autoplayBusy.value = true
  try {
    const result = await autoplayChoice(sessionId.value)
    const option = options.value.find((item) => item.key === result.choice)
    if (!option) {
      error.value = `托管选择了无效选项：${result.choice}`
      return
    }
    status.value = `托管：主角选择 ${option.key}（${result.reason || '未说明理由'}）`
    chooseOption(option)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '托管失败'
  } finally {
    autoplayBusy.value = false
  }
}

function confirmOpeningStep(): void {
  if (busy.value || !openingStep.value) return
  void sendUserContent(openingStep.value === 'gf' ? '确认金手指' : '确认开局')
}

async function sendUserContent(content: string): Promise<void> {
  if (!content || busy.value || !inGame.value || !sessionId.value) {
    selectedOption.value = null
    return
  }
  status.value = '正在推演下一幕'
  await runStream(`/api/sessions/${encodeURIComponent(sessionId.value)}/messages`, {
    message: content,
    provider: form.value.provider,
    base_url: form.value.base_url,
    api_key: form.value.api_key,
    model: form.value.model,
    thinking_mode: form.value.thinking_mode,
    thinking_param: form.value.thinking_param,
  })
}

function submitAction(): void {
  const content = actionInput.value.trim()
  if (!content || openingInputDisabled.value || !inGame.value || !sessionId.value) return
  actionInput.value = ''
  void sendUserContent(content)
}

async function submitAsk(): Promise<void> {
  const question = askInput.value.trim()
  if (!question || askBusy.value || !inGame.value || !sessionId.value) return
  askBusy.value = true
  askError.value = ''
  try {
    const result = await askQuestion(sessionId.value, question)
    askThread.value = [...askThread.value, { question, answer: result.answer }].slice(-6)
    askInput.value = ''
  } catch (cause) {
    askError.value = cause instanceof Error ? cause.message : '提问失败'
  } finally {
    askBusy.value = false
  }
}

function stopStream(): void {
  abortController?.abort()
  status.value = '已停止读取当前响应'
}

function applyQuestState(nextQuest: unknown, reward?: unknown): void {
  const questRecord = recordOf(nextQuest)
  if (reward !== undefined) questRecord.reward = reward
  state.value = { ...state.value, quest: questRecord }
}

function applyBreakAnchorState(payload: { break_anchor?: unknown; momentum_bar?: unknown; broken_anchors?: unknown }): void {
  const next: Record<string, unknown> = { ...state.value }
  if (payload.break_anchor !== undefined) next.break_anchor = recordOf(payload.break_anchor)
  if (payload.momentum_bar !== undefined) next.momentum_bar = payload.momentum_bar
  if (payload.broken_anchors !== undefined) next.broken_anchors = payload.broken_anchors
  state.value = next
}

async function offerBreakAnchor(): Promise<void> {
  if (!inGame.value || !sessionId.value || breakAnchorBusy.value || busy.value) return
  breakAnchorBusy.value = true
  error.value = ''
  try {
    const result = await breakAnchorOffer(sessionId.value)
    applyBreakAnchorState(result)
    status.value = '碎锚任务已生成，等待抉择'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '碎锚生成失败'
  } finally {
    breakAnchorBusy.value = false
  }
}

async function acceptBreakAnchor(): Promise<void> {
  if (!inGame.value || !sessionId.value || breakAnchorBusy.value || busy.value) return
  breakAnchorBusy.value = true
  error.value = ''
  try {
    const result = await breakAnchorAccept(sessionId.value)
    applyBreakAnchorState(result)
    status.value = '碎锚已接受'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '碎锚接受失败'
  } finally {
    breakAnchorBusy.value = false
  }
}

async function declineBreakAnchor(): Promise<void> {
  if (!inGame.value || !sessionId.value || breakAnchorBusy.value || busy.value) return
  breakAnchorBusy.value = true
  error.value = ''
  try {
    const result = await breakAnchorDecline(sessionId.value)
    applyBreakAnchorState(result)
    status.value = '已婉拒碎锚'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '碎锚婉拒失败'
  } finally {
    breakAnchorBusy.value = false
  }
}

async function offerNewQuest(): Promise<void> {
  if (!inGame.value || !sessionId.value || questBusy.value || busy.value) return
  questBusy.value = true
  error.value = ''
  try {
    const result = await offerQuest(sessionId.value, questKind.value, questDifficulty.value)
    applyQuestState(result.quest, result.reward ?? quest.value.reward)
    questEstimate.value = result.estimated ?? null
    questDoneDismissed.value = false
    status.value = '任务已生成，等待抉择'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务生成失败'
  } finally {
    questBusy.value = false
  }
}

async function acceptCurrentQuest(): Promise<void> {
  if (!inGame.value || !sessionId.value || questBusy.value || busy.value) return
  questBusy.value = true
  error.value = ''
  try {
    const result = await acceptQuest(sessionId.value)
    applyQuestState(result.quest)
    status.value = '任务已接受'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务接受失败'
  } finally {
    questBusy.value = false
  }
}

async function refreshQuest(): Promise<void> {
  if (!inGame.value || !sessionId.value || questBusy.value || busy.value) return
  questBusy.value = true
  error.value = ''
  try {
    await declineQuest(sessionId.value)
    const result = await offerQuest(sessionId.value, questKind.value, questDifficulty.value)
    applyQuestState(result.quest, result.reward)
    questEstimate.value = result.estimated ?? null
    status.value = '任务已刷新'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务刷新失败'
  } finally {
    questBusy.value = false
  }
}

async function refreshSaves(): Promise<void> {
  savesLoading.value = true
  try {
    saveList.value = await listSaves()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '存档列表加载失败'
  } finally {
    savesLoading.value = false
  }
}

function formatSavedAt(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function saveModeLabel(mode: string | null): string {
  return (mode ?? '').startsWith('强化') ? '强化' : '基础'
}

function saveWorkLabel(meta: SaveMeta): string {
  return meta.work || meta.novel || '未命名作品'
}

function saveRoleLabel(meta: SaveMeta): string {
  return meta.role || meta.persona || '未指定角色'
}

function highlightSave(saveIdValue: string): void {
  highlightedSaveId.value = saveIdValue
  if (highlightTimer) window.clearTimeout(highlightTimer)
  highlightTimer = window.setTimeout(() => {
    highlightedSaveId.value = null
  }, 2600)
}

async function saveGame(): Promise<void> {
  if (!inGame.value || !sessionId.value || saveBusy.value || busy.value) return
  const name = savePointName.value.trim() || savePointDefault.value
  saveBusy.value = true
  error.value = ''
  try {
    await saveSession(sessionId.value, name)
    status.value = `已保存为 ${name}`
    savePointName.value = ''
    await refreshSaves()
    highlightSave(name)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '保存失败'
  } finally {
    saveBusy.value = false
  }
}

async function loadSave(meta: SaveMeta): Promise<void> {
  if (busy.value || loadingSaveId.value) return
  loadingSaveId.value = meta.save_id
  error.value = ''
  try {
    if (sessionId.value) {
      state.value = await loadSession(sessionId.value, meta.save_id)
    } else {
      const result = await loadAnySave(meta.save_id)
      sessionId.value = result.session_id
      state.value = result.state
    }
    chat.value = Array.isArray(state.value.history) ? state.value.history as ChatMessage[] : []
    askThread.value = []
    askError.value = ''
    status.value = `已恢复 ${meta.save_id}`
    mobilePanel.value = 'story'
    await scrollToBottom()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '读取失败'
  } finally {
    loadingSaveId.value = null
  }
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (storyScroll.value) storyScroll.value.scrollTop = storyScroll.value.scrollHeight
}

onMounted(async () => {
  activeTheme.value = currentTheme()
  applyTheme(activeTheme.value)
  reduceMotion.value = typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  try {
    bootstrap.value = await getBootstrap()
    const provider = bootstrap.value.providers[0]
    if (provider) {
      form.value.provider = provider.id
      form.value.base_url = provider.base_url
      form.value.model = provider.models[0] ?? ''
    }
    form.value.work = bootstrap.value.works[0] ?? ''
    form.value.difficulty = bootstrap.value.difficulties[0] ?? form.value.difficulty
    form.value.story_richness = richnessConfig.value.default
    // 初始化只填兜底金手指列表（供下拉显示）；真正的生成必须等用户
    // 点"确定设定"后手动触发，绝不在选完主角时自动生成。
    goldenFingerChoices.value = [...bootstrap.value.golden_fingers]
    form.value.golden_finger = ''
    status.value = '目录加载完成'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '无法加载配置目录'
    status.value = '后端服务未连接'
  } finally {
    booting.value = false
  }
  void refreshSaves()
  // URL session 优先接续扫码来源；没有时再回退本机 localStorage。
  void tryRestoreSession()
  window.addEventListener('focus', syncSessionFromServer)
  document.addEventListener('visibilitychange', onVisibilityChange)
})

function onVisibilityChange(): void {
  if (document.visibilityState === 'visible') void syncSessionFromServer()
}

watch(() => form.value.companion_count, () => resizeRoster('伙伴'))
watch(() => form.value.heroine_count, () => resizeRoster('主线'))
// 确认前修改任意人物/难度/收束力设定 → 回退确认状态，需重新点"确定设定"
watch(() => [form.value.work, form.value.difficulty, form.value.mode, form.value.convergence,
  selectedPoolCards.value['主角栏'], selectedPoolCards.value['宿敌栏'],
  form.value.companion_count, form.value.heroine_count, enableNemesis.value], () => {
  if (setupConfirmed.value && !gfGenerated.value) setupConfirmed.value = false
})

watch(questStatus, (next) => {
  if (next === 'offered' || next === 'active') questDoneDismissed.value = false
})

watch(questSettlementKey, (next, prev) => {
  if (!next || next === prev) return
  const settlement = recordOf(quest.value.last_settlement)
  questFlash.value = `回合结算：${text(settlement.status, '进度已更新')}`
  if (questFlashTimer) window.clearTimeout(questFlashTimer)
  questFlashTimer = window.setTimeout(() => {
    questFlash.value = ''
  }, 3200)
})

watch([compressionRecord, round], () => {
  const record = compressionRecord.value
  if (!record || numeric(record.round, -1) !== round.value) return
  const key = `${numeric(record.round, -1)}:${String(record.compressed_at ?? '')}`
  if (key === compressionSeenKey) return
  compressionSeenKey = key
  compressionToastVisible.value = true
  if (compressionToastTimer) window.clearTimeout(compressionToastTimer)
  compressionToastTimer = window.setTimeout(() => {
    compressionToastVisible.value = false
  }, 5200)
})
</script>

<template>
  <div
    class="app-root h-dvh bg-(--fe-bg) text-(--fe-ink)"
    :class="[`app-root--${props.variant}`, fontSize === 'large' ? 'font-large' : '', reduceMotion ? 'reduce-motion' : '', isWindowed ? 'windowed-root' : '']"
  >
    <!-- 窗控三键（仅窗口化模式）：最小化/最大化还原/关闭，实调 Win32 窗口
         操作；Web 版不渲染此栏，用回原生浏览器窗控。 -->
    <div v-if="isWindowed" class="titlebar app-titlebar flex h-10 shrink-0 select-none items-center gap-3 px-4">
      <div class="titlebar-logo grid size-5 place-items-center">
        <BookOpen :size="13" />
      </div>
      <span class="titlebar-title">书中织梦 <em>Novelborne</em></span>
      <span class="cat-secret-host relative inline-flex self-stretch items-center">
        <SiameseCat title="喵" @tap="tapCat" />
      </span>
      <span
        class="titlebar-drag flex-1 self-stretch"
        title="双击最大化 / 还原"
        @dblclick="winControl('toggle')"
      />
      <!-- 窗口化下原 header 被隐藏：主题选择器与手机扫码入口在此补齐 -->
      <ThemePicker :themes="THEME_META" :model-value="activeTheme" @update:model-value="selectTheme" />
      <button v-if="showsLanAccess" class="titlebar-btn" title="手机扫码远程使用" @click="openLanQr">
        <Smartphone :size="14" />
      </button>
      <button class="titlebar-btn" title="最小化" @click="winControl('minimize')">
        <Minus :size="14" />
      </button>
      <button class="titlebar-btn" title="最大化 / 还原" @click="winControl('toggle')">
        <Square :size="10" />
      </button>
      <button class="titlebar-btn titlebar-close" title="关闭" @click="winControl('close')">
        <X :size="14" />
      </button>
    </div>

    <header v-if="!isWindowed" class="app-header relative z-10 flex h-14 items-center border-b border-(--fe-border) bg-(--fe-panel) px-3 sm:px-4">
      <div class="flex min-w-0 items-center gap-2.5">
        <div class="seal-logo grid size-8 shrink-0 place-items-center rounded-full text-(--fe-accent-ink)">
          <BookOpen :size="17" />
        </div>
        <div class="min-w-0 flex items-center gap-1.5">
          <div class="min-w-0">
            <h1 class="truncate text-sm font-bold">书中织梦</h1>
            <p class="title-loom truncate text-[10px] text-(--fe-ink-3)">Novelborne · 生于书卷</p>
          </div>
          <span class="cat-secret-host relative inline-flex self-stretch items-center">
            <SiameseCat title="喵" @tap="tapCat" />
          </span>
        </div>
      </div>

      <div class="ml-5 hidden min-w-0 items-center gap-2 text-xs text-(--fe-ink-3) md:flex">
        <span class="status-badge rounded border px-2 py-1" :class="enhanced ? 'border-[color-mix(in_srgb,_var(--fe-warn)_55%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-warn)_10%,_var(--fe-panel))] text-[color-mix(in_srgb,_var(--fe-warn)_72%,_var(--fe-ink))]' : 'border-(--fe-border) bg-(--fe-panel-2)'">{{ enhanced ? '强化模式' : '基础模式' }}</span>
        <span class="truncate">{{ status }}</span>
      </div>

      <div class="ml-auto flex items-center gap-2">
        <ThemePicker class="hidden sm:flex" :themes="THEME_META" :model-value="activeTheme" @update:model-value="selectTheme" />
        <div class="flex items-center gap-1.5 lg:hidden">
        <button class="icon-button" title="配置" @click="mobilePanel = 'setup'">
          <PanelLeft :size="16" />
        </button>
        <button class="icon-button" title="状态" @click="mobilePanel = 'state'">
          <PanelRight :size="16" />
        </button>
        </div>
        <button v-if="showsLanAccess" class="icon-button" title="手机扫码远程使用" @click="openLanQr">
          <Smartphone :size="16" />
        </button>
      </div>
    </header>

    <Transition name="pop">
      <div v-if="error" class="absolute inset-x-3 top-[62px] z-30 mx-auto flex max-w-2xl items-start gap-2 rounded-md border border-[color-mix(in_srgb,_var(--fe-danger)_28%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-danger)_6%,_var(--fe-panel))] px-3 py-2 text-xs text-(--fe-danger) shadow-sm">
        <CircleAlert class="mt-0.5 shrink-0" :size="15" />
        <span class="min-w-0 flex-1 break-words">{{ error }}</span>
        <button title="关闭" @click="error = ''"><X :size="15" /></button>
      </div>
    </Transition>

    <main class="app-main grid min-h-0 grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_300px] xl:grid-cols-[300px_minmax(420px,1fr)_320px]">
      <aside
        class="panel-left scrollbar overflow-y-auto border-r border-(--fe-border) bg-(--fe-panel-2) pb-20 lg:block lg:pb-4"
        :class="mobilePanel === 'setup' ? 'block panel--active' : 'hidden'"
      >
        <section class="border-b border-(--fe-border) p-3">
          <button class="section-toggle" @click="basicOpen = !basicOpen">
            <span class="flex items-center gap-2"><Settings2 :size="15" /> 基础设定</span>
            <ChevronDown :size="15" class="chevron" :class="basicOpen ? 'rotate-180' : ''" />
          </button>

          <div v-if="basicOpen" class="mt-3">
            <label class="block">
              <span class="label">运行模式</span>
              <select v-model="form.mode" class="field h-9 px-2 text-[13px]" :disabled="setupLocked" @change="onModeChanged">
                <option v-for="item in bootstrap?.modes" :key="item">{{ item }}</option>
              </select>
            </label>

            <div class="mt-2">
              <span class="label">收束力（确认设定后锁定）</span>
              <div class="flex gap-1.5">
                <button
                  v-for="item in ['一般', '较高', '极高']"
                  :key="item"
                  type="button"
                  class="skill-tab"
                  :class="form.convergence === item ? 'active' : ''"
                  :disabled="setupLocked"
                  @click="form.convergence = item"
                >{{ item }}</button>
              </div>
              <p class="mt-1 text-[10px] leading-4 text-(--fe-ink-3)">收束力越高，剧情走向越贴近原著主线锚点。</p>
            </div>

            <div v-if="enhanced" class="mt-3">
              <span class="label flex items-center justify-between">
                <span>故事丰富度</span>
                <span class="richness-badge" :data-tier="richnessTier.label">{{ richnessTier.label }}</span>
              </span>
              <div class="richness-slider">
                <span class="seal-mark seal-min" aria-hidden="true">简</span>
                <input
                  v-model.number="form.story_richness"
                  type="range"
                  class="richness-range"
                  :min="richnessConfig.min"
                  :max="richnessConfig.max"
                  :step="richnessConfig.step"
                  aria-label="故事丰富度"
                  :disabled="setupLocked"
                />
                <span class="seal-mark seal-max" aria-hidden="true">繁</span>
              </div>
              <p class="mt-1.5 text-[10px] leading-4 text-(--fe-ink-3)">{{ richnessTier.note }}</p>
              <Transition name="pop">
                <p
                  v-if="richnessThinkingHint && enhanced"
                  class="mt-1 flex items-start gap-1 rounded-md border border-[color-mix(in_srgb,_var(--fe-warn)_32%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-warn)_10%,_var(--fe-panel))] px-2 py-1.5 text-[10px] leading-4 text-[color-mix(in_srgb,_var(--fe-warn)_72%,_var(--fe-ink))]"
                >
                  <Sparkles :size="11" class="mt-0.5 shrink-0" />
                  当前丰富度建议搭配带思考模式的模型，或在下方把思考模式设为「开启」。
                </p>
              </Transition>
            </div>

            <div v-if="enhanced" class="mt-2">
              <label class="flex cursor-pointer items-center gap-2 text-xs font-bold">
                <input v-model="form.story_agent_mode" type="checkbox" class="size-4 accent-(--fe-accent)" :disabled="setupLocked" />
                {{ bootstrap?.story_agent_mode?.label ?? '类 Agent 生成' }}
                <Sparkles :size="12" class="text-(--fe-accent)" />
              </label>
              <p class="mt-1 text-[10px] leading-4 text-(--fe-ink-3)">{{ bootstrap?.story_agent_mode?.note }}</p>
            </div>

            <div v-if="!enhanced" class="mt-2">
              <span class="label flex items-center justify-between">
                <span>作品库</span>
                <span class="font-normal text-(--fe-ink-3)">{{ worksCount }} 部</span>
              </span>
              <div class="work-picker">
                <button type="button" class="field work-picker-trigger" :disabled="setupLocked" @click="workPickerOpen = !workPickerOpen">
                  <span class="min-w-0 flex-1 truncate text-left">{{ form.work || '选择作品' }}</span>
                  <ChevronDown :size="14" class="shrink-0 text-(--fe-ink-3)" />
                </button>
                <div v-if="workPickerOpen" class="picker-overlay" @click="workPickerOpen = false" />
                <div v-if="workPickerOpen" class="work-picker-panel">
                  <div class="work-picker-search">
                    <Search :size="13" class="shrink-0 text-(--fe-ink-3)" />
                    <input v-model="workQuery" placeholder="搜索作品名或编号…" />
                    <span class="shrink-0 text-[10px] text-(--fe-ink-3)">{{ filteredWorks.length }} 部</span>
                  </div>
                  <div class="work-picker-list scrollbar">
                    <button
                      v-for="work in pagedWorks"
                      :key="work"
                      type="button"
                      class="work-option"
                      :class="work === form.work ? 'active' : ''"
                      @click="selectWork(work)"
                    >{{ work }}</button>
                    <button v-if="hasMoreWorks" type="button" class="w-full py-2 text-center text-[11px] text-(--fe-accent) hover:underline" @click="loadMoreWorks">加载更多（{{ filteredWorks.length - pagedWorks.length }} 部）</button>
                    <p v-if="!filteredWorks.length" class="py-4 text-center text-[11px] text-(--fe-ink-3)">无匹配作品</p>
                  </div>
                </div>
              </div>

              <label class="mt-2 block">
                <span class="label">切入时间点</span>
                <input v-model="form.timepoint" class="field h-9 px-2 text-[13px]" placeholder="例如：故事开篇 / 第三章之后" :disabled="setupLocked" />
              </label>
              <label class="mt-2 block">
                <span class="label">指定片段（可选）</span>
                <textarea v-model="form.fragment" class="field h-16 p-2 text-xs" placeholder="粘贴原著片段，开局将锚定该片段" :disabled="setupLocked" />
              </label>

              <div v-if="!enhanced" class="mt-2">
                <span class="label flex items-center justify-between">
                  <span>自定义原著（可选上传）</span>
                  <button v-if="novelUpload" type="button" class="text-[10px] text-(--fe-accent) hover:underline" :disabled="setupLocked" @click="novelUpload = null">清除（回到作品库）</button>
                </span>
                <label class="upload-zone" @dragover.prevent @drop.prevent="handleUpload">
                  <Upload :size="16" class="shrink-0 text-(--fe-accent)" />
                  <span class="min-w-0 text-xs">
                    <strong class="block truncate">{{ novelUpload?.filename || '选择 TXT 文件' }}</strong>
                    <small class="block truncate text-[10px] text-(--fe-ink-3)">
                      {{ novelUpload ? `${Number(novelUpload.bytes || 0).toLocaleString()} bytes · 开局时优先于作品库` : '上传自有文本作为穿越世界（优先于左侧作品库）' }}
                    </small>
                  </span>
                  <input type="file" accept=".txt,text/plain" class="hidden" @change="handleUpload" />
                </label>
              </div>
            </div>

            <div v-else class="mt-2">
              <span class="label">完整原著 TXT</span>
              <label class="upload-zone" @dragover.prevent @drop.prevent="handleUpload">
                <Upload :size="16" class="shrink-0 text-(--fe-accent)" />
                <span class="min-w-0 text-xs">
                  <strong class="block truncate">{{ novelUpload?.filename || '选择 TXT 文件' }}</strong>
                  <small class="block truncate text-[10px] text-(--fe-ink-3)">
                    {{ novelUpload ? `${Number(novelUpload.bytes || 0).toLocaleString()} bytes` : '点击或拖入 TXT，开局时执行章节切分门禁' }}
                  </small>
                </span>
                <input type="file" accept=".txt,text/plain" class="hidden" @change="handleUpload" />
              </label>
            </div>
          </div>
        </section>

        <section class="border-b border-(--fe-border) p-3">
          <button class="section-toggle" @click="savesOpen = !savesOpen">
            <span class="flex items-center gap-2"><Save :size="15" /> 存档</span>
            <ChevronDown :size="15" class="chevron" :class="savesOpen ? 'rotate-180' : ''" />
          </button>

          <div v-if="savesOpen" class="mt-3">
            <div v-if="inGame" class="save-point">
              <input
                v-model="savePointName"
                class="field h-9 min-w-0 flex-1 px-2 text-[13px]"
                :placeholder="savePointDefault"
                :disabled="saveBusy || busy"
                aria-label="存档点名称"
                @keydown.enter.exact.prevent="saveGame"
              />
              <button type="button" class="small-action primary h-9 shrink-0" :disabled="saveBusy || busy" @click="saveGame">
                <LoaderCircle v-if="saveBusy" class="animate-spin" :size="12" />
                <Save v-else :size="12" /> 保存
              </button>
            </div>

            <div class="save-list scrollbar" :class="inGame ? 'mt-2' : ''">
              <div v-if="savesLoading && !saveList.length" class="empty-state flex items-center justify-center gap-2">
                <LoaderCircle class="animate-spin" :size="12" /> 正在加载存档…
              </div>
              <p v-else-if="!saveList.length" class="empty-state">暂无存档，开始游戏后可在此保存进度</p>
              <div
                v-for="(meta, index) in saveList"
                :key="`${meta.save_id}-${index}`"
                class="save-card"
                :class="highlightedSaveId === meta.save_id ? 'flash' : ''"
              >
                <div class="flex items-center gap-1.5">
                  <strong class="min-w-0 flex-1 truncate text-[12px]">{{ meta.save_id }}</strong>
                  <span class="save-mode-badge" :class="saveModeLabel(meta.mode) === '强化' ? 'enhanced' : ''">{{ saveModeLabel(meta.mode) }}</span>
                </div>
                <p class="mt-1 truncate text-[11px] text-(--fe-ink-2)" :title="saveWorkLabel(meta)">{{ saveWorkLabel(meta) }}</p>
                <p class="truncate text-[10px] text-(--fe-ink-3)">{{ saveRoleLabel(meta) }} · {{ meta.difficulty || '未知难度' }}</p>
                <div class="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-(--fe-ink-3)">
                  <span>回合 {{ meta.round ?? '—' }} · 章节 {{ meta.chapter ?? '—' }}</span>
                  <span class="shrink-0">{{ formatSavedAt(meta.saved_at) }}</span>
                </div>
                <button
                  type="button"
                  class="small-action mt-2 w-full"
                  :disabled="busy || loadingSaveId !== null"
                  @click="loadSave(meta)"
                >
                  <LoaderCircle v-if="loadingSaveId === meta.save_id" class="animate-spin" :size="12" />
                  <ArchiveRestore v-else :size="12" /> 读取
                </button>
              </div>
            </div>

            <button
              type="button"
              class="small-action mt-2.5 w-full"
              :disabled="!inGame && !saveList.length"
              title="将当前对局或存档导出为小说文稿"
              @click="exportOpen = true"
            >
              <ScrollText :size="12" /> 导出小说
            </button>
          </div>
        </section>

        <section class="border-b border-(--fe-border) p-3">
          <button class="section-toggle" @click="protagonistOpen = !protagonistOpen">
            <span class="flex items-center gap-2"><Sparkles :size="15" /> 主角、角色与金手指</span>
            <ChevronDown :size="15" class="chevron" :class="protagonistOpen ? 'rotate-180' : ''" />
          </button>

          <div v-if="protagonistOpen" class="mt-3">
            <div class="grid grid-cols-1 gap-2 mb-3">
              <label class="block">
                <span class="label">主角身份（可留空：默认穿成原著主角；性别不限，以书中身体为准）</span>
                <input v-model="form.role" class="field h-9 px-2 text-[13px]" placeholder="例如：青云门外门弟子" :disabled="setupLocked" />
              </label>
            </div>

            <div class="mt-2">
              <span class="label flex items-center justify-between">
                <span>主角性格</span>
                <span class="font-normal text-(--fe-ink-3)">{{ totalPoolCards }} 张卡</span>
              </span>
              <div class="mt-1">
                <select v-model="poolSlots['主角栏'].category" class="field h-8 flex-1 px-2 text-[12px]" :disabled="setupLocked" @change="onPoolCategoryChanged('主角栏')">
                  <option value="">全部来源</option>
                  <option v-for="category in poolCategoryOptions('主角栏')" :key="category" :value="category">{{ category }}</option>
                </select>
                <select v-model="poolSlots['主角栏'].subtype" class="field mt-1 h-8 w-full px-2 text-[12px]" :disabled="setupLocked">
                  <option value="">全部类型</option>
                  <option v-for="subtype in poolSubtypeOptions('主角栏')" :key="subtype" :value="subtype">{{ subtype }}</option>
                </select>
              </div>
              <select
                v-model="selectedPoolCards['主角栏']"
                class="pool-select-main field mt-1.5 h-8 w-full px-2 text-[12px]"
                :class="{ 'pool-select-active': selectedPoolCards['主角栏'] }"
                :disabled="setupLocked"
                @change="poolSlots['主角栏'].previewId = selectedPoolCards['主角栏']"
              >
                <option value="">选择主角性格</option>
                <optgroup v-if="genericPersonas.length" label="通用性格">
                  <option
                    v-for="persona in genericPersonas"
                    :key="persona"
                    :value="`${PRESET_PREFIX}${persona}`"
                    :title="persona"
                  >【通用】{{ persona }}</option>
                </optgroup>
                <optgroup v-for="group in filteredPoolGroups('主角栏')" :key="group.key" :label="group.key">
                  <template v-for="sub in group.sub_groups" :key="`${group.key}-${sub.key}`">
                    <option
                      v-for="card in sub.cards"
                      :key="card.id"
                      :value="card.id"
                      :title="`${card.archetype || ''} ${card.background || ''}`"
                      @mouseover="poolSlots['主角栏'].previewId = card.id"
                    >【{{ sub.key }}】{{ card.name }} · {{ card.gender || '未知性别' }} · {{ card.work || '原创' }}</option>
                  </template>
                </optgroup>
              </select>
              <p v-if="selectedPoolCards['主角栏']" class="pool-picked">
                已选：<strong>{{ selectedPersonaPreset || poolCardById('主角栏', selectedPoolCards['主角栏'])?.name }}</strong>
                <span v-if="!selectedPersonaPreset" class="ml-1 text-[10px] text-(--fe-ink-3)">{{ poolCardById('主角栏', selectedPoolCards['主角栏'])?.gender || '未知性别' }} · {{ poolCardById('主角栏', selectedPoolCards['主角栏'])?.work || '原创' }}</span>
                <span v-else class="ml-1 text-[10px] text-(--fe-ink-3)">通用性格</span>
                <button type="button" class="ml-1 text-(--fe-danger)" :disabled="setupLocked" @click="togglePoolCard('主角栏', selectedPoolCards['主角栏'])">移除</button>
              </p>
              <input
                :value="poolSlots['主角栏'].query"
                type="text"
                class="pool-search mt-1.5 h-8 w-full px-2.5 text-[12px]"
                placeholder="搜角色名（模糊匹配）…"
                :disabled="setupLocked"
                @input="onPoolQueryInput('主角栏', $event)"
              />
              <div v-if="poolPreviewCard('主角栏')" class="pool-preview-card">
                <div class="flex items-baseline justify-between gap-2">
                  <strong>{{ poolPreviewCard('主角栏')!.name }}</strong>
                  <span class="text-[10px] text-(--fe-ink-3)">{{ poolPreviewCard('主角栏')!.original_position || '未标注' }} · {{ poolPreviewCard('主角栏')!.gender || '未知性别' }}</span>
                </div>
                <dl class="pool-preview-fields">
                  <div><dt>原型</dt><dd>{{ poolPreviewCard('主角栏')!.archetype || '—' }}</dd></div>
                  <div><dt>出处</dt><dd>{{ poolPreviewCard('主角栏')!.work || '原创' }}<span v-if="poolPreviewCard('主角栏')!.source_medium">（{{ poolPreviewCard('主角栏')!.source_medium }}）</span></dd></div>
                  <div><dt>定位</dt><dd>{{ poolPreviewCard('主角栏')!.original_position || '未标注' }}</dd></div>
                  <div><dt>适配类型</dt><dd>{{ (poolPreviewCard('主角栏')!.protagonist_type || []).join(' / ') || '通用' }}</dd></div>
                  <div><dt>简介</dt><dd>{{ poolPreviewText(poolPreviewCard('主角栏')!) }}</dd></div>
                </dl>
              </div>
            </div>

            <button type="button" class="designer-entry" @click="currentView = 'designer'">
              <Wand2 :size="14" class="shrink-0 text-(--fe-accent)" />
              <span class="min-w-0 flex-1 text-left">
                <strong class="block text-[12px]">角色设计器</strong>
                <small class="block text-[10px] font-normal text-(--fe-ink-3)">自定义角色由此生成并存入数据库</small>
              </span>
            </button>

            <label class="mt-3 block">
              <span class="label flex items-center justify-between">
                <span>伙伴数量</span>
                <span class="text-[10px] font-normal" :class="gfPrerequisites.ok ? 'text-(--fe-ok)' : 'text-(--fe-accent)'">
                  {{ gfPrerequisites.ok ? '人物已齐备' : `待定：${gfPrerequisites.problems.join('、')}` }}
                </span>
              </span>
              <input v-model.number="form.companion_count" type="number" min="0" max="20" class="field h-9 px-2 text-[13px]" :disabled="setupLocked" />
            </label>

            <div v-for="(entry, index) in companionRoster" :key="`c-${index}`" class="mt-3 roster-row">
              <div class="mb-2 flex items-center justify-between">
                <strong class="text-[11px]">伙伴 {{ index + 1 }}/{{ form.companion_count }}<span class="ml-1 font-normal text-(--fe-ink-3)">{{ poolCardById('伙伴栏', entry.model_id)?.gender || entry.gender || '' }}</span></strong>
                <span class="text-[10px] text-(--fe-ink-3)">剧情相关度 {{ entry.participation }}</span>
              </div>
              <div class="flex gap-1.5">
                <select v-model="poolSlots['伙伴栏'].category" class="field h-8 flex-1 px-2 text-[11px]" :disabled="setupLocked" @change="onPoolCategoryChanged('伙伴栏')">
                  <option value="">全部来源</option>
                  <option v-for="category in poolCategoryOptions('伙伴栏')" :key="category" :value="category">{{ category }}</option>
                </select>
                <select v-model="poolSlots['伙伴栏'].subtype" class="field h-8 flex-1 px-2 text-[11px]" :disabled="setupLocked">
                  <option value="">全部类型</option>
                  <option v-for="subtype in poolSubtypeOptions('伙伴栏')" :key="subtype" :value="subtype">{{ subtype }}</option>
                </select>
              </div>
              <select v-model="entry.model_id" class="field mt-1.5 h-8 w-full px-2 text-[11px]" :disabled="setupLocked" @change="applyCharacter(entry); poolSlots['伙伴栏'].previewId = entry.model_id">
                <option value="">选择伙伴</option>
                <optgroup v-for="group in filteredPoolGroups('伙伴栏')" :key="group.key" :label="group.key">
                  <template v-for="sub in group.sub_groups" :key="`${group.key}-${sub.key}`">
                    <option
                      v-for="card in sub.cards"
                      :key="card.id"
                      :value="card.id"
                      :title="`${card.archetype || ''} ${card.background || ''}`"
                      @mouseover="poolSlots['伙伴栏'].previewId = card.id"
                    >【{{ sub.key }}】{{ card.name }} · {{ card.gender || '未知性别' }} · {{ card.work || '原创' }}</option>
                  </template>
                </optgroup>
              </select>
              <input
                :value="poolSlots['伙伴栏'].query"
                type="text"
                class="pool-search mt-1.5 h-8 w-full px-2.5 text-[11px]"
                placeholder="搜角色名（模糊匹配）…"
                :disabled="setupLocked"
                @input="onPoolQueryInput('伙伴栏', $event)"
              />
              <!-- 每行独立预览：直接按本行 model_id 取卡，不受共享 previewId 影响 -->
              <div v-if="entry.model_id && poolCardById('伙伴栏', entry.model_id)" class="pool-preview-card">
                <div class="flex items-baseline justify-between gap-2">
                  <strong>{{ poolCardById('伙伴栏', entry.model_id)!.name }}</strong>
                  <span class="text-[10px] text-(--fe-ink-3)">{{ poolCardById('伙伴栏', entry.model_id)!.original_position || '未标注' }} · {{ poolCardById('伙伴栏', entry.model_id)!.gender || '未知性别' }}</span>
                </div>
                <dl class="pool-preview-fields">
                  <div><dt>原型</dt><dd>{{ poolCardById('伙伴栏', entry.model_id)!.archetype || '—' }}</dd></div>
                  <div><dt>出处</dt><dd>{{ poolCardById('伙伴栏', entry.model_id)!.work || '原创' }}<span v-if="poolCardById('伙伴栏', entry.model_id)!.source_medium">（{{ poolCardById('伙伴栏', entry.model_id)!.source_medium }}）</span></dd></div>
                  <div><dt>定位</dt><dd>{{ poolCardById('伙伴栏', entry.model_id)!.original_position || '未标注' }}</dd></div>
                  <div><dt>适配类型</dt><dd>{{ (poolCardById('伙伴栏', entry.model_id)!.partner_type || []).join(' / ') || '通用' }}</dd></div>
                  <div><dt>简介</dt><dd>{{ poolPreviewText(poolCardById('伙伴栏', entry.model_id)!) }}</dd></div>
                </dl>
              </div>
              <input v-model="entry.name" class="field mt-1.5 h-8 px-2 text-[11px]" placeholder="姓名" :disabled="setupLocked" />
              <select v-model="entry.persona_preset" class="field mt-1.5 h-8 w-full px-2 text-[11px]" :disabled="setupLocked">
                <option value="">性格：跟随设定</option>
                <option v-for="persona in genericPersonas" :key="persona" :value="persona">【通用】{{ persona }}</option>
              </select>
              <textarea v-model="entry.background" class="field mt-1.5 h-14 p-2 text-[11px]" placeholder="背景与关系" :disabled="setupLocked" />
              <textarea v-model="entry.skill" class="field mt-1.5 h-12 p-2 text-[11px]" placeholder="技能/能力描述（留空按设定推断）" />
              <input v-model.number="entry.participation" type="range" min="1" max="9" class="mt-2 w-full accent-(--fe-accent)" :disabled="setupLocked" />
              <div class="participation-scale">
                <span>偶尔</span><span>·</span><span>·</span><span>·</span><span>·</span><span>·</span><span>·</span><span>·</span><span>全程</span>
              </div>
            </div>

            <label class="mt-3 block">
              <span class="label">伴侣数量</span>
              <input v-model.number="form.heroine_count" type="number" min="0" max="10" class="field h-9 px-2 text-[13px]" :disabled="setupLocked" />
            </label>

            <p v-if="!heroineRoster.length" class="py-4 text-center text-[11px] text-(--fe-ink-3)">
              输入目标数量后逐位配置
            </p>

            <div v-for="(entry, index) in heroineRoster" :key="`h-${index}`" class="mt-3 roster-row">
              <div class="mb-2 flex items-center justify-between">
                <strong class="text-[11px]">伴侣 {{ index + 1 }}/{{ form.heroine_count }}<span class="ml-1 font-normal text-(--fe-ink-3)">{{ poolCardById('伴侣栏', entry.model_id)?.gender || entry.gender || '' }}</span></strong>
                <span class="text-[10px] text-(--fe-ink-3)">剧情相关度 {{ entry.participation }}</span>
              </div>
              <div class="flex gap-1.5">
                <select v-model="poolSlots['伴侣栏'].category" class="field h-8 flex-1 px-2 text-[11px]" :disabled="setupLocked" @change="onPoolCategoryChanged('伴侣栏')">
                  <option value="">全部来源</option>
                  <option v-for="category in poolCategoryOptions('伴侣栏')" :key="category" :value="category">{{ category }}</option>
                </select>
                <select v-model="poolSlots['伴侣栏'].subtype" class="field h-8 flex-1 px-2 text-[11px]" :disabled="setupLocked">
                  <option value="">全部类型</option>
                  <option v-for="subtype in poolSubtypeOptions('伴侣栏')" :key="subtype" :value="subtype">{{ subtype }}</option>
                </select>
              </div>
              <select v-model="entry.model_id" class="field mt-1.5 h-8 w-full px-2 text-[11px]" :disabled="setupLocked" @change="applyCharacter(entry); poolSlots['伴侣栏'].previewId = entry.model_id">
                <option value="">选择伴侣</option>
                <optgroup v-for="group in filteredPoolGroups('伴侣栏')" :key="group.key" :label="group.key">
                  <template v-for="sub in group.sub_groups" :key="`${group.key}-${sub.key}`">
                    <option
                      v-for="card in sub.cards"
                      :key="card.id"
                      :value="card.id"
                      :title="`${card.archetype || ''} ${card.background || ''}`"
                      @mouseover="poolSlots['伴侣栏'].previewId = card.id"
                    >【{{ sub.key }}】{{ card.name }} · {{ card.gender || '未知性别' }} · {{ card.work || '原创' }}</option>
                  </template>
                </optgroup>
              </select>
              <input
                :value="poolSlots['伴侣栏'].query"
                type="text"
                class="pool-search mt-1.5 h-8 w-full px-2.5 text-[11px]"
                placeholder="搜角色名（模糊匹配）…"
                :disabled="setupLocked"
                @input="onPoolQueryInput('伴侣栏', $event)"
              />
              <select v-model="entry.persona_preset" class="field mt-1.5 h-8 w-full px-2 text-[11px]" :disabled="setupLocked">
                <option value="">性格：跟随设定</option>
                <option v-for="persona in genericPersonas" :key="persona" :value="persona">【通用】{{ persona }}</option>
              </select>
              <input v-model.number="entry.participation" type="range" min="1" max="9" class="mt-2 w-full accent-(--fe-accent)" :disabled="setupLocked" />
              <div class="participation-scale">
                <span>偶尔</span><span>·</span><span>·</span><span>·</span><span>·</span><span>·</span><span>·</span><span>·</span><span>全程</span>
              </div>
              <!-- 每行独立预览：直接按本行 model_id 取卡，不受共享 previewId 影响 -->
              <div v-if="entry.model_id && poolCardById('伴侣栏', entry.model_id)" class="pool-preview-card">
                <div class="flex items-baseline justify-between gap-2">
                  <strong>{{ poolCardById('伴侣栏', entry.model_id)!.name }}</strong>
                  <span class="text-[10px] text-(--fe-ink-3)">{{ poolCardById('伴侣栏', entry.model_id)!.original_position || '未标注' }} · {{ poolCardById('伴侣栏', entry.model_id)!.gender || '未知性别' }}</span>
                </div>
                <dl class="pool-preview-fields">
                  <div><dt>原型</dt><dd>{{ poolCardById('伴侣栏', entry.model_id)!.archetype || '—' }}</dd></div>
                  <div><dt>出处</dt><dd>{{ poolCardById('伴侣栏', entry.model_id)!.work || '原创' }}<span v-if="poolCardById('伴侣栏', entry.model_id)!.source_medium">（{{ poolCardById('伴侣栏', entry.model_id)!.source_medium }}）</span></dd></div>
                  <div><dt>定位</dt><dd>{{ poolCardById('伴侣栏', entry.model_id)!.original_position || '未标注' }}</dd></div>
                  <div><dt>适配类型</dt><dd>{{ (poolCardById('伴侣栏', entry.model_id)!.companion_type || []).join(' / ') || '通用' }}</dd></div>
                  <div><dt>简介</dt><dd>{{ poolPreviewText(poolCardById('伴侣栏', entry.model_id)!) }}</dd></div>
                </dl>
              </div>
            </div>
          </div>
        </section>


        <section class="border-b border-(--fe-border) p-3">
          <button class="section-toggle" @click="nemesisOpen = !nemesisOpen">
            <span class="flex items-center gap-2"><Swords :size="15" /> 宿敌</span>
            <ChevronDown :size="15" class="chevron" :class="nemesisOpen ? 'rotate-180' : ''" />
          </button>

          <div v-if="nemesisOpen" class="mt-3">
            <label class="flex cursor-pointer items-center gap-2 text-xs font-bold">
              <input v-model="enableNemesis" type="checkbox" class="size-4 accent-(--fe-accent)" :disabled="setupLocked" />
              启用宿敌系统
            </label>
            <p v-if="enableNemesis && !enhanced" class="mt-1 text-[10px] text-(--fe-accent)">注：宿敌系统仅强化模式生效，基础模式下勾选不会启用</p>

            <div v-if="enableNemesis" class="mt-2">
              <div class="mt-1 flex gap-1.5">
                  <select v-model="poolSlots['宿敌栏'].category" class="field h-8 flex-1 px-2 text-[12px]" :disabled="setupLocked" @change="onPoolCategoryChanged('宿敌栏')">
                    <option value="">全部来源</option>
                    <option v-for="category in poolCategoryOptions('宿敌栏')" :key="category" :value="category">{{ category }}</option>
                  </select>
                  <select v-model="poolSlots['宿敌栏'].subtype" class="field h-8 flex-1 px-2 text-[12px]" :disabled="setupLocked">
                    <option value="">全部类型</option>
                    <option v-for="subtype in poolSubtypeOptions('宿敌栏')" :key="subtype" :value="subtype">{{ subtype }}</option>
                  </select>
                </div>
                <select
                  v-model="selectedPoolCards['宿敌栏']"
                  class="pool-select-main field mt-1.5 h-8 w-full px-2 text-[12px]"
                  :class="{ 'pool-select-active': selectedPoolCards['宿敌栏'] }"
                  :disabled="setupLocked"
                  @change="poolSlots['宿敌栏'].previewId = selectedPoolCards['宿敌栏']"
                >
                  <option value="">选择宿敌</option>
                  <optgroup v-if="genericPersonas.length" label="通用性格">
                    <option
                      v-for="persona in genericPersonas"
                      :key="persona"
                      :value="`${PRESET_PREFIX}${persona}`"
                      :title="persona"
                    >【通用】{{ persona }}</option>
                  </optgroup>
                  <optgroup v-for="group in filteredPoolGroups('宿敌栏')" :key="group.key" :label="group.key">
                    <template v-for="sub in group.sub_groups" :key="`${group.key}-${sub.key}`">
                      <option
                        v-for="card in sub.cards"
                        :key="card.id"
                        :value="card.id"
                        :title="`${card.archetype || ''} ${card.background || ''}`"
                        @mouseover="poolSlots['宿敌栏'].previewId = card.id"
                      >【{{ sub.key }}】{{ card.name }} · {{ card.gender || '未知性别' }} · {{ card.work || '原创' }}</option>
                    </template>
                  </optgroup>
                </select>
                <p v-if="selectedPoolCards['宿敌栏']" class="pool-picked">
                  已选：<strong>{{ selectedPoolCards['宿敌栏'].startsWith(PRESET_PREFIX) ? selectedPoolCards['宿敌栏'].slice(PRESET_PREFIX.length) : poolCardById('宿敌栏', selectedPoolCards['宿敌栏'])?.name }}</strong>
                  <span v-if="!selectedPoolCards['宿敌栏'].startsWith(PRESET_PREFIX)" class="ml-1 text-[10px] text-(--fe-ink-3)">{{ poolCardById('宿敌栏', selectedPoolCards['宿敌栏'])?.gender || '未知性别' }} · {{ poolCardById('宿敌栏', selectedPoolCards['宿敌栏'])?.work || '原创' }}</span>
                  <span v-else class="ml-1 text-[10px] text-(--fe-ink-3)">通用性格 · 强度按默认战力 2 计</span>
                  <button type="button" class="ml-1 text-(--fe-danger)" :disabled="setupLocked" @click="togglePoolCard('宿敌栏', selectedPoolCards['宿敌栏'])">移除</button>
                </p>
                <input
                  :value="poolSlots['宿敌栏'].query"
                  type="text"
                  class="pool-search mt-1.5 h-8 w-full px-2.5 text-[12px]"
                  placeholder="搜角色名（模糊匹配）…"
                  :disabled="setupLocked"
                  @input="onPoolQueryInput('宿敌栏', $event)"
                />
                <div v-if="poolPreviewCard('宿敌栏')" class="pool-preview-card">
                  <div class="flex items-baseline justify-between gap-2">
                    <strong>{{ poolPreviewCard('宿敌栏')!.name }}</strong>
                    <span class="text-[10px] text-(--fe-ink-3)">{{ poolPreviewCard('宿敌栏')!.original_position || '未标注' }} · {{ poolPreviewCard('宿敌栏')!.gender || '未知性别' }}</span>
                  </div>
                  <dl class="pool-preview-fields">
                    <div><dt>原型</dt><dd>{{ poolPreviewCard('宿敌栏')!.archetype || '—' }}</dd></div>
                    <div><dt>出处</dt><dd>{{ poolPreviewCard('宿敌栏')!.work || '原创' }}<span v-if="poolPreviewCard('宿敌栏')!.source_medium">（{{ poolPreviewCard('宿敌栏')!.source_medium }}）</span></dd></div>
                    <div><dt>定位</dt><dd>{{ poolPreviewCard('宿敌栏')!.original_position || '未标注' }}</dd></div>
                    <div><dt>适配类型</dt><dd>{{ (poolPreviewCard('宿敌栏')!.nemesis_type || []).join(' / ') || '通用' }}</dd></div>
                    <div><dt>简介</dt><dd>{{ poolPreviewText(poolPreviewCard('宿敌栏')!) }}</dd></div>
                  </dl>
                </div>
              <!-- 宿敌强度系数（选卡后实时计算） -->
              <div v-if="selectedPoolCards['宿敌栏']" class="mt-2 rounded border border-(--fe-border) bg-(--fe-panel) px-2.5 py-1.5">
                <div class="flex items-center justify-between">
                  <span class="text-[11px] font-medium text-(--fe-ink-2)">宿敌强度系数</span>
                  <span class="text-[13px] font-bold text-(--fe-accent)">D{{ displayNemesisDifficulty?.toFixed(2) }}</span>
                </div>
                <div class="nemesis-bar-row">
                  <span class="nemesis-bar-label">强</span>
                  <span class="nemesis-bar-track">
                    <span class="nemesis-bar-fill" :style="{ left: `${nemesisBarRatio * 100}%` }" />
                  </span>
                  <span class="nemesis-bar-label">弱</span>
                </div>
                <p class="mt-0.5 text-[9px] leading-tight text-(--fe-ink-3)">D0.01 碾压级 ← → D9.99 微末级；主角难度 + 主角团 vs 宿敌方非线性计算</p>
              </div>
            </div>
          </div>
        </section>

        <section class="border-b border-(--fe-border) p-3">
          <button class="section-toggle" @click="gfOpen = !gfOpen">
            <span class="flex items-center gap-2"><Sparkles :size="15" /> 难度与金手指</span>
            <span class="ml-1 text-[10px] font-normal" :class="gfGenerated ? 'text-(--fe-ok)' : (setupConfirmed ? 'text-(--fe-ok)' : (gfPrerequisites.problems.length ? 'text-(--fe-accent)' : 'text-(--fe-ink-3)'))">
              {{ gfGenerated ? '已生成·设定锁定' : (setupConfirmed ? '已确认，选择金手指' : (gfPrerequisites.problems.length ? `待定：${gfPrerequisites.problems.join('、')}` : '待确认设定')) }}
            </span>
            <ChevronDown :size="15" class="chevron" :class="gfOpen ? 'rotate-180' : ''" />
          </button>

          <div v-if="gfOpen" class="mt-3.5 space-y-3">
            <p class="text-[10px] leading-4 text-(--fe-ink-3)">
              流程：① 配置人物（四栏均可留空，空位开局由模型分配：主角→原著主角，其余→性格最贴合的原著角色）→ ② 选难度 → ③ 点「确定人物与难度设定」→ ④ 选择金手指。
              金手指按宿敌强度 D 缩放（GF(D)=D^1.15）；确认设定后人物与难度将锁定。
            </p>

            <label class="block">
              <span class="label">难度（确认设定后锁定）</span>
              <select v-model="form.difficulty" class="field h-9 px-2 text-[13px]" :disabled="setupLocked">
                <option v-for="item in bootstrap?.difficulties" :key="item">{{ item }}</option>
              </select>
            </label>

            <p class="text-[10px] leading-4 text-(--fe-ink-3)">
              收束力：<strong class="text-(--fe-ink-2)">{{ form.convergence }}</strong>
              <span class="ml-1">（「基础设定」区可选；{{ setupLocked ? '已随设定锁定' : '确认设定后一并锁定' }}）</span>
            </p>

            <div class="flex items-center gap-2">
              <button
                v-if="!setupConfirmed"
                type="button"
                class="small-action primary"
                :disabled="gfPrerequisites.problems.length > 0 || busy"
                @click="confirmSetup"
              >
                <Check :size="12" /> 确定人物与难度设定
              </button>
              <button
                v-else
                type="button"
                class="small-action primary"
                :disabled="gfGenerated || busy"
                @click="refreshGoldenFingers()"
              >
                <Sparkles :size="12" /> {{ gfGenerated ? '已生成' : '生成推荐金手指' }}
              </button>
              <span v-if="gfGenerated" class="text-[10px] text-(--fe-ok)">✓ 已按宿敌强度 D{{ displayNemesisDifficulty?.toFixed(2) }} 缩放（GF={{ gfValue }}）</span>
              <span v-else-if="!setupConfirmed && gfPrerequisites.problems.length" class="text-[10px] text-(--fe-accent)">先补齐：{{ gfPrerequisites.problems.join('、') }}</span>
              <span v-else-if="setupConfirmed && !gfGenerated" class="text-[10px] text-(--fe-ink-3)">可生成推荐，或直接从下方选择</span>
            </div>

            <label class="block">
              <span class="label flex items-center justify-between">
                <span>金手指选择</span>
              </span>
              <select v-model="form.golden_finger" class="field h-9 px-2 text-[13px]" :disabled="!setupConfirmed || inGame || busy" @change="onGoldenFingerChanged">
                <option v-if="!form.golden_finger && setupConfirmed" value="" disabled>请选择金手指（或点上方生成推荐）</option>
                <option v-for="item in goldenFingerSelectOptions" :key="item">{{ item }}</option>
              </select>
            </label>
            <div v-if="customGoldenFinger" class="proposal-card mt-2 border-l-2 border-(--fe-warn) bg-(--fe-panel) p-2.5">
              <textarea v-model="goldenFingerText" class="field h-14 p-2 text-[11px]" placeholder="描述能力想法" :disabled="!setupConfirmed || inGame || busy" />
              <div class="mt-2 flex gap-1.5">
                <button class="small-action" :disabled="!setupConfirmed || !goldenFingerText.trim() || (goldenFingerProposal?.attempt ?? 0) >= 3" @click="proposeCustomGoldenFinger">
                  <Sparkles :size="12" /> {{ goldenFingerProposal ? '重新提案' : '生成提案' }}
                </button>
                <button class="small-action primary" :disabled="goldenFingerProposal?.status !== 'await_confirmation'" @click="confirmCustomGoldenFinger">
                  <Check :size="12" /> 确认采用
                </button>
              </div>
              <Transition name="pop">
                <dl v-if="goldenFingerProposal" class="compact-list mt-2">
                  <div><dt>名称</dt><dd>{{ goldenFingerProposal.spec.name }}</dd></div>
                  <div><dt>代价</dt><dd>{{ goldenFingerProposal.spec.cost }}</dd></div>
                  <div><dt>冷却</dt><dd>{{ goldenFingerProposal.spec.cooldown }}</dd></div>
                  <div><dt>状态</dt><dd>{{ goldenFingerProposal.status === 'confirmed' ? '已确认' : `待确认 · 剩余 ${goldenFingerProposal.remaining} 次` }}</dd></div>
                </dl>
              </Transition>
            </div>

            <p v-if="gfLockHint" class="mt-2 rounded border border-[color-mix(in_srgb,_var(--fe-warn)_32%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-warn)_10%,_var(--fe-panel))] px-2 py-1.5 text-[10px] leading-4 text-[color-mix(in_srgb,_var(--fe-warn)_72%,_var(--fe-ink))]">
              🔒 {{ gfLockHint }}
            </p>
          </div>
        </section>

        <section class="border-b border-(--fe-border) p-3">
          <button class="section-toggle" @click="modelOpen = !modelOpen">
            <span class="flex items-center gap-2"><KeyRound :size="15" /> 模型与参数</span>
            <ChevronDown :size="15" class="chevron" :class="modelOpen ? 'rotate-180' : ''" />
          </button>
          <div v-if="modelOpen" class="mt-3 space-y-2">
            <label class="block">
              <span class="label">提供商</span>
              <select v-model="form.provider" class="field h-9 px-2 text-[13px]" :disabled="modelLocked" @change="onProviderChanged">
                <option v-for="item in bootstrap?.providers" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
            </label>
            <label class="block">
              <span class="label">API Key（留空则使用服务端环境变量）</span>
              <input v-model="form.api_key" type="password" name="fate-api-key" autocomplete="new-password" autocapitalize="off" spellcheck="false" class="field h-9 px-2 text-[13px]" placeholder="仅保存在当前页面内存" :disabled="modelLocked" />
            </label>
            <label class="block">
              <span class="label flex items-center justify-between">
                <span>模型</span>
                <span class="text-[10px] font-normal text-(--fe-ink-3)">优先选带思考模式的模型</span>
              </span>
              <div class="flex gap-1.5">
                <select v-if="availableModels.length" v-model="form.model" class="field h-9 flex-1 px-2 text-[13px]" :disabled="modelLocked">
                  <option v-for="item in availableModels" :key="item">{{ item }}</option>
                </select>
                <input v-else v-model="form.model" class="field h-9 flex-1 px-2 text-[13px]" :disabled="modelLocked" />
                <button type="button" class="small-action h-9" :disabled="fetchingModels" title="拉取模型列表" @click="pullModelList">
                  <LoaderCircle v-if="fetchingModels" class="animate-spin" :size="12" />
                  <ListChecks v-else :size="12" /> 拉取
                </button>
              </div>
            </label>
            <label class="block">
              <span class="label">接口地址</span>
              <div class="flex gap-1.5">
                <input v-model="form.base_url" class="field h-9 flex-1 px-2 text-[13px]" placeholder="自定义服务的 Base URL" :disabled="modelLocked" />
                <button type="button" class="small-action h-9" :disabled="testingConnection" title="测试连接" @click="runConnectionTest">
                  <LoaderCircle v-if="testingConnection" class="animate-spin" :size="12" />
                  <PlugZap v-else :size="12" /> 测试
                </button>
              </div>
            </label>
            <Transition name="pop">
              <p v-if="connectionResult" class="rounded-md border px-2.5 py-1.5 text-[11px]" :class="connectionResult.ok ? 'border-[color-mix(in_srgb,_var(--fe-ok)_30%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-ok)_8%,_var(--fe-panel))] text-(--fe-ok)' : 'border-[color-mix(in_srgb,_var(--fe-danger)_28%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-danger)_6%,_var(--fe-panel))] text-(--fe-danger)'">
                {{ connectionResult.message }}
              </p>
            </Transition>
            <div class="grid grid-cols-2 gap-2">
              <label>
                <span class="label">思考模式</span>
                <select v-model="form.thinking_mode" class="field h-9 px-2 text-[13px]" :disabled="modelLocked">
                  <option value="auto">自动</option>
                  <option value="on">开启</option>
                  <option value="off">关闭</option>
                </select>
              </label>
              <label>
                <span class="label">思考参数</span>
                <input v-model="form.thinking_param" class="field h-9 px-2 text-[13px]" placeholder="如 budget_tokens" :disabled="modelLocked" />
              </label>
            </div>
            <label class="flex cursor-pointer items-center gap-2 text-xs font-bold" :class="enhanced ? 'opacity-80' : ''">
              <input v-model="form.distill_enabled" type="checkbox" class="size-4 accent-(--fe-accent)" :disabled="modelLocked || enhanced" />
              启用锚点蒸馏{{ enhanced ? '（强化模式必需）' : '' }}
            </label>
          </div>

          <button class="start-button mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-(--fe-accent) px-3 text-[13px] font-bold text-(--fe-accent-ink) hover:bg-(--fe-accent-strong) disabled:bg-(--fe-panel-3) disabled:text-(--fe-ink-3)" :disabled="startDisabled" @click="startGame">
            <LoaderCircle v-if="busy" class="animate-spin" :size="16" />
            <Play v-else :size="16" />
            {{ busy ? '正在推演' : enhanced ? '校验原著并准备' : '开始模拟' }}
          </button>
        </section>

        <section class="p-3">
          <button class="section-toggle" @click="uiOpen = !uiOpen">
            <span class="flex items-center gap-2"><Palette :size="15" /> 界面</span>
            <ChevronDown :size="15" class="chevron" :class="uiOpen ? 'rotate-180' : ''" />
          </button>
          <div v-if="uiOpen" class="mt-3 space-y-2">
            <label class="block">
              <span class="label">字号</span>
              <select v-model="fontSize" class="field h-9 px-2 text-[13px]">
                <option value="standard">标准</option>
                <option value="large">较大</option>
              </select>
            </label>
            <label class="flex cursor-pointer items-center gap-2 text-xs font-bold">
              <input v-model="dropCapEnabled" type="checkbox" class="size-4 accent-(--fe-accent)" />
              首字下沉（章节首段朱红大字）
            </label>
            <label class="flex cursor-pointer items-center gap-2 text-xs font-bold">
              <input v-model="reduceMotion" type="checkbox" class="size-4 accent-(--fe-accent)" />
              减少动效
            </label>
          </div>
        </section>
      </aside>

      <section
        class="story-panel theme-decor relative flex min-h-0 min-w-0 flex-col bg-(--fe-panel-3) pb-16 lg:flex lg:pb-0"
        :class="mobilePanel === 'story' ? 'flex panel--active' : 'hidden'"
      >
        <div class="flex h-11 shrink-0 items-center border-b border-[color-mix(in_srgb,_var(--fe-border)_60%,_var(--fe-panel))] px-4">
          <MessageSquareText :size="15" class="mr-2 text-(--fe-accent)" />
          <h2 class="text-xs font-bold">叙事舞台</h2>
          <div class="ml-auto flex items-center gap-3 text-[10px] text-(--fe-ink-3)">
            <span>回合 {{ round }}</span>
            <span>章节 {{ chapter }}</span>
            <span v-if="sessionId" class="hidden max-w-24 truncate sm:inline">{{ sessionId }}</span>
          </div>
        </div>

        <div v-if="turnBudget > 0" class="chapter-track shrink-0" aria-hidden="true">
          <span class="chapter-fill" :style="{ width: `${chapterRatio * 100}%` }" />
        </div>

        <Transition name="pop">
          <div
            v-if="compressionToastVisible && compressionRecord"
            class="compression-toast"
            :style="{ top: timelineNodes.length ? '96px' : '50px' }"
          >
            <FileArchive :size="14" class="mt-0.5 shrink-0" />
            <span class="min-w-0 flex-1">
              上下文已压缩接手 · 保留 {{ compressionKept }} 条消息
              <template v-if="compressionDegraded"> · 已保留原文降级</template>
            </span>
            <button title="关闭" @click="compressionToastVisible = false"><X :size="13" /></button>
          </div>
        </Transition>

        <div v-if="timelineNodes.length" :key="timelineKey" class="anchor-timeline scrollbar shrink-0">
          <template v-for="(entry, index) in timelineNodes" :key="`${entry.kind}-${entry.node.chapter}-${index}`">
            <span v-if="index" class="tl-line" />
            <div
              class="tl-node"
              :class="`tl-${entry.kind}`"
              :style="entry.kind === 'upcoming' ? { opacity: entry.fade } : undefined"
              :title="entry.node.summary || entry.node.title"
            >
              <span class="tl-dot">
                <Check v-if="entry.kind === 'past'" :size="9" />
              </span>
              <span class="tl-text">
                <span class="tl-chapter">第 {{ entry.node.chapter }} 章</span>
                <span class="tl-title">{{ entry.node.title }}</span>
              </span>
            </div>
          </template>
        </div>

        <div ref="storyScroll" class="scrollbar min-h-0 flex-1 overflow-y-auto">
          <div v-if="booting" class="grid h-full place-items-center">
            <LoaderCircle class="animate-spin text-(--fe-accent)" :size="24" />
          </div>
          <div v-else-if="!chat.length" class="mx-auto flex h-full max-w-lg flex-col items-center justify-center px-8 text-center">
            <div class="grid size-12 place-items-center rounded-md border border-(--fe-border) bg-(--fe-panel) text-(--fe-accent)">
              <Sparkles :size="21" />
            </div>
            <h2 class="mt-4 text-base font-bold">等待第一幕</h2>
            <p class="mt-1 text-[13px] leading-6 text-(--fe-ink-3)">
              {{ enhanced ? '上传可切章的完整 TXT，并完成左侧配置。' : '从作品库选择世界，配置主角与同伴。' }}
            </p>
            <div class="mt-5 flex gap-2 text-[10px] text-(--fe-ink-3)">
              <span class="rounded border border-(--fe-border) bg-(--fe-panel) px-2 py-1">{{ worksCount }} 部作品</span>
              <span class="rounded border border-(--fe-border) bg-(--fe-panel) px-2 py-1">{{ poolsCount }} 个角色模型</span>
            </div>
          </div>
          <div v-else class="mx-auto w-full max-w-[860px] px-3 py-5 sm:px-6 sm:py-7">
            <div class="book-page">
              <article
                v-for="(item, index) in chatView"
                :key="index"
                class="chat-message mb-7 wheel-transition"
                :class="[item.role === 'user' ? 'pl-[12%]' : '', item.isFocus ? 'is-focus' : '']"
                :style="item.style"
              >
                <div class="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold text-(--fe-ink-3)" :class="item.role === 'user' ? 'justify-end' : ''">
                  <UserRound v-if="item.role === 'user'" :size="12" />
                  <Bot v-else :size="12" />
                  {{ item.role === 'user' ? '你的行动' : '叙事引擎' }}
                </div>
                <div
                  v-if="item.role === 'user'"
                  class="whitespace-pre-wrap break-words rounded-md bg-(--fe-panel-3) px-4 py-3 text-[15px] leading-7 text-(--fe-ink) kraft-note"
                >{{ item.content }}</div>
                <div v-else class="narrative-body">
                  <template v-for="(block, blockIndex) in item.blocks" :key="blockIndex">
                    <h3 v-if="block.kind === 'chapter'" class="narrative-chapter">{{ block.text }}</h3>
                    <p v-else class="narrative-para" :class="block.dropCap && dropCapEnabled ? 'first' : ''">
                      <template v-if="block.dropCap && dropCapEnabled"><span class="drop-cap" aria-hidden="true">{{ block.cap }}</span>{{ block.rest }}</template>
                      <template v-else>{{ block.text }}</template>
                    </p>
                  </template>
                </div>
              </article>
            </div>
          </div>
        </div>

        <div class="shrink-0 border-t border-(--fe-border) bg-(--fe-panel) px-3 pb-3 pt-2 sm:px-4">
          <div class="mx-auto max-w-[820px]">
            <div class="ask-card">
              <button type="button" class="ask-toggle" @click="askOpen = !askOpen">
                <HelpCircle :size="13" class="shrink-0 text-(--fe-ok)" />
                <span>不懂就问</span>
                <span class="ask-note">{{ relayActive ? '已接通：输入内容将永久增补进主线' : '基于规则文档答疑，不影响剧情' }}</span>
                <ChevronDown :size="13" class="chevron ml-auto shrink-0 text-(--fe-ink-3)" :class="askOpen ? 'rotate-180' : ''" />
              </button>
              <div v-if="askOpen" class="ask-body">
                <div v-if="askThread.length" class="ask-thread scrollbar">
                  <div v-for="(qa, index) in askThread" :key="index" class="ask-pair">
                    <p class="ask-q">{{ qa.question }}</p>
                    <p class="ask-a">{{ qa.answer }}</p>
                  </div>
                </div>
                <div class="flex gap-1.5">
                  <input
                    v-model="askInput"
                    class="field h-8 flex-1 px-2 text-xs"
                    :disabled="!inGame || askBusy"
                    :placeholder="relayActive ? '增补设定/剧情内容，永久接入主线…' : '向规则引擎提问…'"
                    @keydown.enter.exact.prevent="submitAsk"
                  />
                  <button type="button" class="small-action primary h-8" :disabled="!inGame || askBusy || !askInput.trim()" @click="submitAsk">
                    <LoaderCircle v-if="askBusy" class="animate-spin" :size="12" />
                    <Send v-else :size="12" /> 提问
                  </button>
                </div>
                <p v-if="askError" class="mt-1.5 text-[10px] text-(--fe-danger)">{{ askError }}</p>
              </div>
            </div>

            <div v-if="openingStep" class="mb-3">
              <button
                type="button"
                class="flex h-10 w-full items-center justify-center gap-2 rounded-md bg-(--fe-accent) px-3 text-[13px] font-bold text-(--fe-accent-ink) hover:bg-(--fe-accent-strong) disabled:bg-(--fe-panel-3) disabled:text-(--fe-ink-3)"
                :disabled="busy"
                @click="confirmOpeningStep"
              >
                <Check :size="14" />
                {{ openingStep === 'gf' ? '确认金手指' : '确认开局' }}
              </button>
              <p class="mt-1 text-center text-[10px] text-(--fe-ink-3)">
                {{ openingStep === 'gf' ? '点击即确认当前金手指，然后进入开局确认' : '点击后立即生成第一幕' }}
              </p>
            </div>

            <div v-if="inGame" class="mb-3 flex gap-1.5">
              <textarea
                v-model="actionInput"
                rows="2"
                class="field min-h-0 flex-1 resize-none px-2 py-2 text-[13px] leading-5"
                :disabled="openingInputDisabled"
                :placeholder="openingInputPlaceholder"
                @keydown.enter.exact.prevent="submitAction"
              />
              <button
                type="button"
                class="small-action primary h-9 shrink-0 self-end"
                :disabled="openingInputDisabled || !actionInput.trim()"
                @click="submitAction"
              >
                <LoaderCircle v-if="busy" class="animate-spin" :size="12" />
                <Send v-else :size="12" /> 发送
              </button>
            </div>

            <div v-if="options.length && inGame" class="mb-1.5 flex items-center justify-end gap-2">
              <span class="text-[10px] text-(--fe-ink-3)">点一次仅托管本回合</span>
              <button
                type="button"
                class="small-action"
                :disabled="busy || autoplayBusy"
                title="由主角性格子智能体代替你选择本回合的行动"
                @click="autoplay"
              >
                <LoaderCircle v-if="autoplayBusy" class="animate-spin" :size="12" />
                <Bot v-else :size="12" /> 托管本回合
              </button>
            </div>

            <div v-if="options.length" class="options-grid">
              <button
                v-for="option in options"
                :key="option.key"
                type="button"
                class="option-card"
                :class="[`opt-${option.key.toLowerCase()}`, (relayActive ? relaySelectedKeys.includes(option.key) : selectedOption === option.key) ? 'selected' : '']"
                :disabled="busy || !inGame"
                @click="chooseOption(option)"
              >
                <span class="option-key">{{ option.key }}</span>
                <span class="option-text">{{ option.text }}</span>
              </button>
            </div>
            <!-- 增补通路接通：多选合并 + 本回合增补输入 -->
            <div v-if="relayActive && options.length && inGame" class="mt-1.5">
              <textarea
                v-model="relaySupplement"
                rows="2"
                class="field w-full resize-none px-2 py-1.5 text-xs leading-4"
                :disabled="busy"
                placeholder="增补本回合行动内容（可选）…"
              ></textarea>
              <button
                type="button"
                class="small-action primary mt-1.5 w-full"
                :disabled="busy || !relaySelectedKeys.length"
                @click="submitRelayChoices"
              >
                按已选项行动（{{ relaySelectedKeys.length }}）
              </button>
            </div>
            <div v-else class="options-placeholder" :class="busy ? 'waiting' : ''">
              <LoaderCircle v-if="busy" class="animate-spin" :size="14" />
              {{ busy ? '正在推演下一幕' : inGame ? '等待引擎给出选项' : '开局后由引擎给出选项' }}
            </div>

            <div class="mt-1.5 flex items-center gap-2">
              <button v-if="busy" type="button" class="small-action" @click="stopStream">
                <Square :size="11" /> 停止读取
              </button>
              <p class="min-w-0 flex-1 truncate text-[10px] text-(--fe-ink-3)">{{ status }}</p>
            </div>

            <!-- 页脚版权（铭刻已移至右侧栏底部） -->
            <div class="mt-1.5 border-t border-[color-mix(in_srgb,_var(--fe-border)_60%,_var(--fe-panel))] pt-1.5 text-center">
              <p class="text-[9px] leading-3 tracking-wide text-(--fe-ink-3) opacity-80">© 2026 书中织梦 · Novelborne</p>
            </div>
          </div>
        </div>
      </section>

      <aside
        class="panel-right scrollbar overflow-y-auto border-l border-(--fe-border) bg-(--fe-panel-2) pb-20 lg:block lg:pb-4"
        :class="mobilePanel === 'state' ? 'block panel--active' : 'hidden'"
      >
        <div class="flex h-11 items-center border-b border-(--fe-border) bg-(--fe-panel) px-3">
          <Gauge :size="15" class="mr-2 text-(--fe-ok)" />
          <h2 class="text-xs font-bold">运行状态</h2>
          <span class="status-dot ml-auto size-2 rounded-full" :class="busy ? 'animate-pulse bg-(--fe-warn)' : inGame ? 'bg-(--fe-ok)' : 'bg-(--fe-border-strong)'" />
        </div>

        <section class="grid grid-cols-2 border-b border-(--fe-border) bg-(--fe-panel)">
          <div class="metric"><span>回合</span><strong>{{ round }}</strong></div>
          <div class="metric border-l"><span>章节</span><strong>{{ chapter }}</strong></div>
          <div class="metric border-t"><span>章内进度</span><strong>{{ chapterRound }}/{{ turnBudget || '—' }}</strong></div>
          <div class="metric border-l border-t"><span>相容性 K</span><strong>{{ compatibility }}</strong></div>
          <div class="metric border-t">
            <span>故事丰富度</span>
            <strong>{{ stateRichnessLabel }}</strong>
          </div>
          <div class="metric col-span-2 border-t" title="最近一次调用：入 {{ tokenUsage.lastIn.toLocaleString() }} · 出 {{ tokenUsage.lastOut.toLocaleString() }}">
            <span>本局 Token{{ tokenUsage.source === 'measured' ? '' : tokenUsage.source === 'mixed' ? '（含估算）' : '（估算）' }}</span>
            <strong class="metric-token">
              入 {{ fmtTok(tokenUsage.in) }} · 出 {{ fmtTok(tokenUsage.out) }}<template v-if="tokenUsage.cache"> · 缓存 {{ fmtTok(tokenUsage.cache) }}</template>
              <em v-if="tokenUsage.source === 'measured'" class="metric-token-badge">实测</em>
              <em v-else-if="tokenUsage.source === 'mixed'" class="metric-token-badge">含估算</em>
            </strong>
          </div>
        </section>

        <section class="state-section">
          <h3><ScrollText :size="14" /> 任务</h3>

          <div v-if="showQuestGenerator" class="quest-generator">
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="kindOption in QUEST_KIND_OPTIONS"
                :key="kindOption.value"
                type="button"
                class="skill-tab"
                :class="questKind === kindOption.value ? 'active' : ''"
                @click="questKind = kindOption.value"
              >{{ kindOption.label }}</button>
            </div>
            <div class="mt-2.5">
              <span class="label flex items-center justify-between">
                <span>难度系数</span>
                <span class="font-normal text-(--fe-ink-3)">{{ questDifficulty.toFixed(2) }}</span>
              </span>
              <input v-model.number="questDifficulty" type="range" min="0" max="1" step="0.05" class="w-full accent-(--fe-accent)" :disabled="questBusy" />
              <p class="mt-1 text-[10px] leading-4 text-(--fe-ink-3)">系数在可选区间内线性取档（受世界总体难度制约）；系数越高奖励越丰厚、时限越长。</p>
            </div>
            <button
              type="button"
              class="small-action primary mt-2 w-full"
              :disabled="!inGame || questBusy || busy"
              @click="offerNewQuest"
            >
              <LoaderCircle v-if="questBusy" class="animate-spin" :size="12" />
              <Sparkles v-else :size="12" /> 生成任务
            </button>
          </div>

          <div v-else-if="questStatus === 'offered'" class="quest-card">
            <strong class="quest-title">{{ text(questOffer.title, '未命名任务') }}</strong>
            <ul v-if="questRequirements.length" class="quest-req">
              <li v-for="(requirement, index) in questRequirements" :key="index">{{ requirement }}</li>
            </ul>
            <p class="quest-goal">目标：{{ text(questOffer.goal, '—') }}</p>
            <div v-if="questEstimate" class="mt-2">
              <span class="label flex items-center justify-between">
                <span>任务难度预估</span>
                <span class="font-normal text-(--fe-ink-3)">{{ questEstimate.label }}（{{ questEstimate.level }}/9）</span>
              </span>
              <div class="relative h-1.5 w-full rounded bg-(--fe-panel-3)">
                <div class="h-full rounded bg-(--fe-accent)" :style="{ width: `${(questEstimate.level / 9) * 100}%` }"></div>
                <div
                  v-for="bound in [questEstimate.range_lo, questEstimate.range_hi]"
                  :key="bound"
                  class="absolute top-[-2px] h-2.5 w-[2px] bg-(--fe-ink-2)"
                  :style="{ left: `${(bound / 9) * 100}%` }"
                ></div>
              </div>
              <p class="mt-1 text-[10px] leading-4 text-(--fe-ink-3)">
                可选难度区间：{{ questEstimate.range_label }}（受世界总体难度制约）；
                时限 {{ questEstimate.deadline_span }} 回合。
              </p>
            </div>
            <p v-if="questRemaining !== null" class="quest-meta">时限：剩余 {{ questRemaining }} 回合</p>
            <dl v-if="questRewardEntries.length" class="compact-list mt-2">
              <div v-for="entry in questRewardEntries" :key="entry[0]"><dt>{{ entry[0] }}</dt><dd>{{ entry[1] }}</dd></div>
            </dl>
            <div class="mt-2 flex gap-1.5">
              <button type="button" class="small-action primary flex-1" :disabled="!inGame || questBusy || busy" @click="acceptCurrentQuest">
                <LoaderCircle v-if="questBusy" class="animate-spin" :size="12" />
                <Check v-else :size="12" /> 接受
              </button>
              <button type="button" class="small-action flex-1" :disabled="!inGame || questBusy || busy" @click="refreshQuest">
                <RotateCcw :size="12" /> 刷新
              </button>
            </div>
          </div>

          <div v-else-if="questStatus === 'active'" class="quest-card">
            <strong class="quest-title">{{ text(questOffer.title, '进行中任务') }}</strong>
            <p class="quest-goal">目标：{{ text(questOffer.goal, '—') }}</p>
            <div class="quest-progress-track" aria-hidden="true">
              <span class="quest-progress-fill" :style="{ width: `${questProgressRatio * 100}%` }" />
            </div>
            <p class="quest-meta">
              已进行 {{ questElapsed }}/{{ questTotal }} 回合
              <template v-if="questRemaining !== null"> · 剩余 {{ questRemaining }} 回合</template>
            </p>
            <Transition name="pop">
              <p v-if="questFlash" class="quest-flash-msg">{{ questFlash }}</p>
            </Transition>
          </div>

          <div v-else class="quest-card" :class="questStatus === 'failed' ? 'failed' : 'completed'">
            <strong class="quest-title">{{ text(questOffer.title, '任务') }}</strong>
            <p class="quest-result" :class="questStatus === 'failed' ? 'failed' : 'completed'">
              {{ questStatus === 'failed' ? '已失败 · 超时未完成' : '已完成' }}
            </p>
            <dl v-if="questStatus === 'completed' && questRewardEntries.length" class="compact-list mt-2">
              <div v-for="entry in questRewardEntries" :key="entry[0]"><dt>{{ entry[0] }}</dt><dd>{{ entry[1] }}</dd></div>
            </dl>
            <button type="button" class="small-action primary mt-2 w-full" @click="questDoneDismissed = true">
              <Sparkles :size="12" /> 生成新任务
            </button>
          </div>
        </section>

        <section class="state-section">
          <h3>
            <Sparkles :size="14" /> 涟漪与积势
            <span v-if="convergenceEffective" class="ml-auto text-[10px] font-normal text-(--fe-ink-3)">收束力 · {{ convergenceEffective }}</span>
          </h3>
          <div v-if="convergenceAvailable" class="conv-bar" role="img" aria-label="收束力位置">
            <span class="conv-tick" style="left: 25%" />
            <span class="conv-tick" style="left: 75%" />
            <span class="conv-dot settled" :style="{ left: `${convergenceSettled * 100}%` }" />
            <span class="conv-dot current" :style="{ left: `${convergencePosition * 100}%` }" />
          </div>
          <dl class="compact-list" :class="convergenceAvailable ? 'mt-2' : ''">
            <div><dt>原始等级</dt><dd>{{ text(ripple.raw_level, text(ripple.raw_name)) }}</dd></div>
            <div><dt>有效等级</dt><dd>{{ text(ripple.effective_level, text(ripple.level)) }}</dd></div>
            <div><dt>有效积势</dt><dd>{{ text(ripple.effective_total) }}</dd></div>
            <div><dt>尝试压力</dt><dd>{{ text(ripple.attempt_total) }}</dd></div>
          </dl>
          <div class="momentum-bar-wrap mt-2">
            <div class="flex items-center justify-between text-[10px] text-(--fe-ink-3)">
              <span>碎锚积势 · {{ text(momentumBar.tier, convergenceEffective || '较高') }}</span>
              <span>{{ numeric(momentumBar.total, 0) }}/{{ numeric(momentumBar.threshold, 0) }}{{ momentumReady ? ' · 可碎锚' : '' }}</span>
            </div>
            <div class="quest-progress-track" aria-label="积势进度">
              <span class="quest-progress-fill" :class="momentumReady ? 'ready' : ''" :style="{ width: `${momentumRatio * 100}%` }" />
            </div>
          </div>
          <div class="break-anchor-panel mt-2">
            <p class="quest-meta">碎锚 · {{ breakAnchorStatus }}
              <template v-if="breakAnchorRemaining !== null"> · 剩余 {{ breakAnchorRemaining }} 回合</template>
            </p>
            <p v-if="breakAnchorStatus === 'active'" class="quest-goal">{{ text(breakAnchorStage.requirement, text(breakAnchorStage.title, '进行中')) }}</p>
            <p v-else-if="breakAnchorStatus === 'offered'" class="quest-goal">{{ text(recordOf(breakAnchor.target_anchor).title, '当前锚点') }}</p>
            <p v-else-if="breakAnchorStatus === 'completed'" class="quest-result completed">已打碎当前锚点</p>
            <p v-else-if="breakAnchorStatus === 'failed' || breakAnchorStatus === 'cooldown'" class="quest-result failed">冷却中</p>
            <div v-if="breakAnchorStatus === 'idle' || breakAnchorStatus === 'ready' || breakAnchorStatus === 'completed' || breakAnchorStatus === 'failed' || breakAnchorStatus === 'cooldown'" class="mt-2">
              <button
                type="button"
                class="small-action primary w-full"
                :disabled="!inGame || breakAnchorBusy || busy || !breakAnchorCanOffer"
                @click="offerBreakAnchor"
              >
                <LoaderCircle v-if="breakAnchorBusy" class="animate-spin" :size="12" />
                <Sparkles v-else :size="12" /> 发起碎锚
              </button>
            </div>
            <div v-else-if="breakAnchorStatus === 'offered'" class="mt-2 flex gap-1.5">
              <button type="button" class="small-action primary flex-1" :disabled="!inGame || breakAnchorBusy || busy" @click="acceptBreakAnchor">
                <LoaderCircle v-if="breakAnchorBusy" class="animate-spin" :size="12" />
                <Check v-else :size="12" /> 接受
              </button>
              <button type="button" class="small-action flex-1" :disabled="!inGame || breakAnchorBusy || busy" @click="declineBreakAnchor">
                拒绝
              </button>
            </div>
          </div>
        </section>

        <section class="state-section">
          <h3><UsersRound :size="14" /> 活跃角色</h3>
          <div v-if="activeMembers.length" class="space-y-1.5">
            <div v-for="(member, index) in activeMembers" :key="index" class="member-card flex items-center gap-2 rounded-md border border-(--fe-border) bg-(--fe-panel) px-2.5 py-2">
              <div class="grid size-7 shrink-0 place-items-center rounded bg-(--fe-panel-3) text-(--fe-ink-2)"><UserRound :size="14" /></div>
              <div class="min-w-0 flex-1">
                <strong class="block truncate text-[11px]">{{ text(member.name, `角色 ${index + 1}`) }}</strong>
                <span class="block truncate text-[10px] text-(--fe-ink-3)">{{ text(member.role, text(member.background, '已进入场景')) }}</span>
              </div>
            </div>
          </div>
          <p v-else class="empty-state">当前场景尚无活跃角色</p>
        </section>

        <section v-if="nemesisSummary" class="state-section">
          <h3><Swords :size="14" /> 宿敌动向（5 回合摘要）</h3>
          <div v-if="displayNemesisDifficulty" class="mb-1.5 flex items-center gap-2 text-[10px]">
            <span class="rounded bg-(--fe-panel) border border-(--fe-border) px-1.5 py-0.5 font-medium text-(--fe-accent)">强度 D{{ displayNemesisDifficulty.toFixed(2) }}</span>
            <span class="nemesis-bar-mini">
              <span class="nemesis-bar-fill" :style="{ left: `${nemesisBarRatio * 100}%` }" />
            </span>
          </div>
          <div class="nemesis-card">
            <p class="nemesis-text">{{ nemesisSummary.text }}</p>
            <div class="distortion-row">
              <span class="distortion-track" aria-hidden="true">
                <span class="distortion-fill" :style="{ width: `${nemesisDistortion * 100}%` }" />
              </span>
              <span class="distortion-value">失真 {{ Math.round(nemesisDistortion * 100) }}%</span>
            </div>
            <p class="distortion-note">难度越高情报越模糊</p>
          </div>
        </section>

        <section class="state-section">
          <h3><FileText :size="14" /> 锚点蒸馏</h3>
          <template v-if="distillProgress && distillProgress.enabled">
            <p class="text-[11px] leading-5 text-(--fe-ink-2)">{{ distillProgress.summary }}</p>
            <div v-if="distillProgress.total" class="quest-progress-track mt-2" aria-label="蒸馏进度">
              <span class="quest-progress-fill" :style="{ width: `${distillRatio * 100}%` }" />
            </div>
            <div v-if="distillWindowChapters.length" class="mt-2 flex flex-wrap gap-1">
              <span
                v-for="c in distillWindowChapters"
                :key="c.chapter"
                class="rounded border px-1.5 py-0.5 text-[10px] leading-4"
                :class="c.status === 'done'
                  ? 'border-[color-mix(in_srgb,_var(--fe-ok)_30%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-ok)_8%,_var(--fe-panel))] text-[color-mix(in_srgb,_var(--fe-ok)_70%,_var(--fe-ink))]'
                  : c.status === 'in_progress'
                    ? 'border-[color-mix(in_srgb,_var(--fe-warn)_55%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-warn)_10%,_var(--fe-panel))] text-[color-mix(in_srgb,_var(--fe-warn)_72%,_var(--fe-ink))]'
                    : c.status === 'failed'
                      ? 'border-[color-mix(in_srgb,_var(--fe-danger)_40%,_var(--fe-panel))] bg-[color-mix(in_srgb,_var(--fe-danger)_8%,_var(--fe-panel))] text-[color-mix(in_srgb,_var(--fe-danger)_75%,_var(--fe-ink))]'
                      : 'border-(--fe-border) bg-(--fe-panel-2) text-(--fe-ink-3)'"
              >{{ c.current ? '第' + c.chapter + '章·当前·' : '第' + c.chapter + '章·' }}{{ c.status_zh }}</span>
            </div>
          </template>
          <dl v-else class="compact-list">
            <div><dt>运行状态</dt><dd>{{ text(distillProgress?.summary || state.distill_status, '未启动') }}</dd></div>
          </dl>
        </section>

      </aside>
    </main>

    <!-- Lomsting 遗物：羽毛笔（点击在新页面展开铭文，所见即所抄） -->
    <button
      type="button"
      class="reader-trigger fixed bottom-3 right-3 z-40 flex cursor-pointer select-none items-center justify-center bg-transparent p-0"
      style="user-select: none; -webkit-user-select: none; border: none;"
      aria-label="打开我的原著阅读器"
      title="我的原著"
      @click="openOriginalReader"
    >
      <span class="flex size-6 items-center justify-center rounded text-(--fe-ink-3) opacity-60 transition-opacity duration-200 hover:opacity-100">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 4c-5 0-11 4-13.5 9.5C5 17 5 19 5 19s2 0 5.5-1.5C16 15 20 9 20 4Z" />
          <path d="M5 19 15 9" />
          <path d="M9 12c-2 1-4 4-4 7" />
        </svg>
      </span>
    </button>

    <Transition name="pop">
      <LanQrModal v-if="lanQrOpen" :info="lanInfo" :loading="lanBusy" @close="lanQrOpen = false" />
    </Transition>

    <Transition name="pop">
      <div v-if="mobileThemeOpen" class="mobile-theme-sheet" @click.self="mobileThemeOpen = false">
        <section role="dialog" aria-modal="true" aria-label="主题选择">
          <header><strong>界面主题</strong><button title="关闭" aria-label="关闭" @click="mobileThemeOpen = false"><X :size="17" /></button></header>
          <ThemePicker compact :themes="THEME_META" :model-value="activeTheme" @update:model-value="selectTheme" />
        </section>
      </div>
    </Transition>

    <nav v-if="isMobileShell || !isWindowed" class="app-bottom-nav fixed inset-x-0 bottom-0 z-20 grid h-16 grid-cols-4 border-t border-(--fe-border) bg-(--fe-panel)" :class="isMobileShell ? '' : 'lg:hidden'">
      <button class="mobile-tab" :class="mobilePanel === 'setup' ? 'active' : ''" @click="mobilePanel = 'setup'">
        <Menu :size="18" /><span>配置</span>
      </button>
      <button class="mobile-tab" :class="mobilePanel === 'story' ? 'active' : ''" @click="mobilePanel = 'story'">
        <MessageSquareText :size="18" /><span>剧情</span>
      </button>
      <button class="mobile-tab" :class="mobilePanel === 'state' ? 'active' : ''" @click="mobilePanel = 'state'">
        <Gauge :size="18" /><span>状态</span>
      </button>
      <button class="mobile-tab" :class="mobileThemeOpen ? 'active' : ''" @click="mobileThemeOpen = !mobileThemeOpen">
        <Palette :size="18" /><span>主题</span>
      </button>
    </nav>

    <OriginalReaderModal v-if="readerOpen" @close="readerOpen = false" />

    <CharacterDesigner
      v-if="currentView === 'designer'"
      :connection="modelConnection"
      :session-id="sessionId"
      @close="currentView = 'main'"
      @saved="onDesignerSaved"
    />
    <NovelExportModal
      v-if="exportOpen"
      :session-id="sessionId"
      :saves="saveList"
      :connection="modelConnection"
      @close="exportOpen = false"
    />
  </div>
</template>

<style scoped>
.app-header { box-shadow: var(--fe-shadow-1); }
.windowed-root .app-main { height: calc(100dvh - 40px); min-height: 600px; }

/* —— 无边框窗口标题栏（pywebview + DWM 圆角）—— */
.app-titlebar {
  border-bottom: 1px solid color-mix(in srgb, var(--fe-border) 80%, transparent);
  background: linear-gradient(180deg,
    color-mix(in srgb, var(--fe-panel) 96%, var(--fe-accent-soft)),
    var(--fe-panel));
  backdrop-filter: saturate(1.05);
}
.titlebar-logo {
  border-radius: calc(var(--fe-radius) - 2px);
  color: var(--fe-accent-ink);
  background: radial-gradient(circle at 32% 28%,
    color-mix(in srgb, var(--fe-accent) 78%, white), var(--fe-accent) 60%,
    var(--fe-accent-strong));
  box-shadow: inset 0 0 0 1.5px rgb(255 255 255 / 14%), 0 1px 3px rgb(0 0 0 / 18%);
}
.titlebar-title {
  color: var(--fe-ink);
  font-family: var(--font-serif);
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.titlebar-title em {
  margin-left: 2px;
  color: var(--fe-ink-3);
  font-family: var(--font-sans);
  font-size: 10.5px;
  font-style: normal;
  font-weight: 500;
  letter-spacing: 0.08em;
}
.titlebar-drag { -webkit-app-region: drag; cursor: default; }
.titlebar-btn {
  display: grid;
  width: 34px;
  height: 26px;
  place-items: center;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--fe-ink-2);
  transition: background-color 130ms ease, color 130ms ease, transform 100ms ease;
}
.titlebar-btn:hover { background: var(--fe-panel-2); color: var(--fe-ink); }
.titlebar-btn:active { transform: scale(.9); }
.titlebar-close { margin-right: -4px; }
.titlebar-close:hover { background: #e81123; color: #fff; }
.mobile-theme-sheet { position: fixed; inset: 0; z-index: 45; display: flex; align-items: end; background: rgb(0 0 0 / 32%); }
.mobile-theme-sheet section { width: 100%; max-height: min(58dvh, 440px); overflow: hidden auto; border-top: 1px solid var(--fe-border); border-radius: 14px 14px 0 0; background: var(--fe-panel); box-shadow: var(--fe-shadow-2); padding: 12px 14px calc(14px + env(safe-area-inset-bottom, 0px)); }
.mobile-theme-sheet header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; color: var(--fe-ink-2); font-size: 13px; }.mobile-theme-sheet header button { display: grid; width: 32px; height: 32px; place-items: center; border-radius: 50%; color: var(--fe-ink-3); }.mobile-theme-sheet header button:hover { background: var(--fe-panel-2); color: var(--fe-ink); }
.mobile-theme-sheet :deep(.theme-picker) { display: grid; max-width: none; grid-template-columns: repeat(2, minmax(0, 1fr)); overflow: visible; }.mobile-theme-sheet :deep(.theme-choice) { min-height: 38px; justify-content: start; font-size: 11px; }
.theme-decor { isolation: isolate; background: var(--fe-decor-stage); }
.theme-decor::after { z-index: -1; }

.icon-button {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel);
  color: var(--fe-ink-2);
  transition: background-color 140ms ease, border-color 140ms ease, transform 100ms ease;
}

/* 顶栏标题火漆印：不规则边缘的蜡封质感（色相随主题 --fe-accent 联动）。
   ::after 为一根穿印而过的细织线——「书中织梦」的经纬意象，六主题通用。 */
.seal-logo {
  position: relative;
  background: radial-gradient(circle at 34% 30%,
              color-mix(in srgb, var(--fe-accent) 82%, white),
              var(--fe-accent) 55%,
              var(--fe-accent-strong));
  box-shadow:
    inset 0 0 0 2.5px rgb(255 255 255 / 16%),
    inset 0 -3px 6px rgb(90 20 10 / 25%),
    0 1px 4px rgb(90 30 20 / 30%);
}
.seal-logo::after {
  content: "";
  position: absolute;
  inset: -3px -4px auto auto;
  width: 14px;
  height: 10px;
  border-top-right-radius: 10px;
  border: 1.5px solid var(--fe-accent);
  border-left: none;
  border-bottom: none;
  opacity: 0.55;
  pointer-events: none;
}
.icon-button:hover:not(:disabled) { border-color: var(--fe-border-strong); background: var(--fe-panel-2); }
.icon-button:active:not(:disabled) { transform: scale(.94); }
.icon-button:disabled { color: var(--fe-border-strong); opacity: .55; }
.section-toggle { display: flex; width: 100%; align-items: center; justify-content: space-between; text-align: left; font-size: 13px; font-weight: 700; }
.section-toggle .chevron { transition: transform 180ms ease; }
.small-action { display: inline-flex; height: 30px; align-items: center; justify-content: center; gap: 5px; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel-2); padding: 0 9px; color: var(--fe-ink-2); font-size: 11px; font-weight: 700; transition: background-color 140ms ease, border-color 140ms ease, transform 100ms ease; }
.small-action:hover:not(:disabled) { border-color: var(--fe-border-strong); }
.small-action:active:not(:disabled) { transform: scale(.96); }
.small-action.primary { border-color: var(--fe-accent); background: var(--fe-accent); color: var(--fe-accent-ink); }
.small-action.primary:hover:not(:disabled) { background: var(--fe-accent-strong); }
.small-action:disabled { border-color: var(--fe-border); background: var(--fe-panel-3); color: var(--fe-border-strong); }
.start-button { transition: background-color 160ms ease, transform 100ms ease, box-shadow 160ms ease; }
.start-button:hover:not(:disabled) { box-shadow: 0 2px 8px rgb(182 58 43 / 25%); }
.start-button:active:not(:disabled) { transform: scale(.98); }
.roster-row { margin-top: 10px; border-left: 2px solid var(--fe-accent); border-radius: 0 var(--fe-radius) var(--fe-radius) 0; background: var(--fe-panel); padding: 10px; }
.roster-row.heroine { border-left-color: var(--fe-ok); }
.skill-tab { display: inline-flex; height: 24px; align-items: center; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel-2); padding: 0 8px; color: var(--fe-ink-2); font-size: 10px; font-weight: 700; transition: background-color 140ms ease, border-color 140ms ease, color 140ms ease; }
.skill-tab.active { border-color: var(--fe-accent); background: var(--fe-panel); color: var(--fe-accent); }

/* —— 角色选择池 —— */
.pool-count-chip { display: inline-grid; min-width: 16px; height: 16px; place-items: center; border-radius: 999px; background: var(--fe-accent); padding: 0 4px; color: var(--fe-accent-ink); font-size: 10px; font-weight: 800; }
.pool-note { margin-bottom: 8px; color: var(--fe-ink-3); font-size: 11px; line-height: 1.6; }
.pool-warn { display: flex; gap: 6px; margin-bottom: 8px; border: 1px solid color-mix(in srgb, var(--fe-warn) 32%, var(--fe-panel)); border-radius: var(--fe-radius); background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel)); padding: 7px 9px; color: color-mix(in srgb, var(--fe-warn) 80%, var(--fe-ink)); font-size: 11px; line-height: 1.55; }

/* —— 选角框美化层（主题变量驱动，六主题通用）—— */
/* 主选择框：主 CTA 级——更高、更圆润、字重清晰 */
.pool-select-main {
  border-radius: calc(var(--fe-radius) + 2px);
  font-weight: 650;
  background:
    repeating-linear-gradient(0deg, rgb(120 96 54 / 1.4%) 0 1px, transparent 1px 3px),
    var(--fe-panel);
  transition: border-color var(--fe-motion, 140ms) var(--ease-brand, ease),
    box-shadow var(--fe-motion, 140ms) ease, background-color var(--fe-motion, 140ms) ease;
}
.pool-select-main:hover:not(:disabled) {
  border-color: var(--fe-border-strong);
  box-shadow: var(--fe-shadow-1);
}
/* 已选中：左边框织线意象 + 主题强调色轻染（像一枚挂了线的牌） */
.pool-select-main.pool-select-active {
  border-color: color-mix(in srgb, var(--fe-accent) 55%, var(--fe-border));
  border-left-width: 3px;
  background:
    repeating-linear-gradient(0deg, rgb(120 96 54 / 1.2%) 0 1px, transparent 1px 3px),
    color-mix(in srgb, var(--fe-accent-soft, var(--fe-accent-soft)) 46%, var(--fe-panel));
}
/* 搜索框：辅助级——轻若无物（虚感细边+浅底），不与主选择抢层级 */
.pool-search {
  border: 1px dashed var(--fe-border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--fe-panel) 55%, transparent);
  color: var(--fe-ink-2);
  outline: none;
  transition: border-color var(--fe-motion, 140ms) ease, background-color var(--fe-motion, 140ms) ease;
}
.pool-search::placeholder { color: var(--fe-ink-3); }
.pool-search:hover:not(:disabled) { border-color: var(--fe-border-strong); }
.pool-search:focus {
  border-style: solid;
  border-color: var(--fe-focus-ring);
  background: var(--fe-panel);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--fe-focus-ring) 14%, transparent);
}
.pool-search:disabled { opacity: .6; }
/* 已选行：胶囊化（点 + 圆底 + 悬停时移除按钮浮现重量） */
.pool-picked {
  margin-top: 7px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid color-mix(in srgb, var(--fe-ok, var(--fe-ok)) 35%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--fe-ok, var(--fe-ok)) 9%, transparent);
  padding: 3px 10px;
  color: var(--fe-ok, var(--fe-ok));
  font-size: 11px;
}
.pool-picked::before {
  content: "";
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: var(--fe-ok, var(--fe-ok));
  box-shadow: 0 0 0 2.5px color-mix(in srgb, var(--fe-ok, var(--fe-ok)) 25%, transparent);
}
.pool-picked button { transition: opacity var(--fe-motion, 140ms) ease; }
.pool-picked button:hover:not(:disabled) { text-decoration: underline; }
.pool-search { display: flex; align-items: center; gap: 7px; margin-bottom: 10px; border: 1px solid var(--fe-border); border-radius: 999px; background: var(--fe-panel); padding: 6px 12px; color: var(--fe-ink-3); }
.pool-search input { flex: 1; min-width: 0; border: none; outline: none; background: transparent; color: var(--fe-ink); font-size: 12px; }
.pool-slot { margin-top: 10px; border: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); border-radius: calc(var(--fe-radius) + 2px); background: var(--fe-panel); padding: 9px 10px; }
.pool-slot-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.pool-slot-head strong { color: var(--fe-ink); font-size: 12.5px; font-weight: 800; }
.pool-slot-note { margin-top: 2px; color: var(--fe-ink-3); font-size: 10px; line-height: 1.5; }
.pool-error { margin-top: 6px; color: var(--fe-danger); font-size: 11px; }
.pool-loading { display: flex; align-items: center; gap: 6px; margin-top: 8px; color: var(--fe-ink-3); font-size: 11px; }
.pool-group { margin-top: 7px; overflow: hidden; border: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); border-radius: var(--fe-radius); background: var(--fe-panel); }
.pool-group-head { display: flex; width: 100%; align-items: center; gap: 6px; padding: 6px 9px; color: var(--fe-ink-2); font-size: 11.5px; font-weight: 700; transition: background-color 130ms ease; }
.pool-group-head:hover { background: var(--fe-panel-2); }
.pool-group-count { flex-shrink: 0; border-radius: 999px; background: var(--fe-panel-3); padding: 1px 7px; color: var(--fe-ink-2); font-size: 10px; font-weight: 800; }
.pool-card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); gap: 6px; border-top: 1px dashed color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); padding: 8px; }
.pool-card { display: flex; min-width: 0; flex-direction: column; gap: 2px; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 6px 8px; text-align: left; transition: border-color 120ms ease, background-color 120ms ease, transform 100ms ease; }
.pool-card:hover { border-color: var(--fe-border-strong); transform: translateY(-1px); }
.pool-card.selected { border-color: var(--fe-accent); background: color-mix(in srgb, var(--fe-accent) 8%, var(--fe-panel)); box-shadow: 0 0 0 1px var(--fe-accent) inset; }
.pool-card strong { color: var(--fe-ink); font-size: 11.5px; font-weight: 800; }
.pool-card small { color: var(--fe-ink-3); font-size: 10px; }
.pool-empty { margin-top: 8px; color: var(--fe-ink-3); font-size: 11px; text-align: center; }
/* —— 角色简介卡：悬停/选中下拉候选时显示 —— */
.pool-preview-card { margin-top: 6px; border: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); border-left: 3px solid var(--fe-accent); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 7px 9px; }
.participation-scale { display: flex; justify-content: space-between; margin-top: 1px; padding: 0 2px; font-size: 8px; color: var(--fe-border-strong); letter-spacing: 0.5px; }
.nemesis-bar-row { display: flex; align-items: center; gap: 4px; margin-top: 3px; }
.nemesis-bar-label { font-size: 8px; color: var(--fe-ink-3); flex-shrink: 0; }
.nemesis-bar-track { position: relative; flex: 1; height: 6px; border-radius: 3px; background: linear-gradient(90deg, var(--fe-accent) 0%, #e8a838 30%, #6b9a4a 60%, #5a7a8a 100%); }
.nemesis-bar-fill { position: absolute; top: -2px; width: 3px; height: 10px; border-radius: 1.5px; background: var(--fe-ink); border: 1px solid #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.3); transform: translateX(-50%); }
.nemesis-bar-mini { position: relative; display: inline-block; width: 60px; height: 5px; border-radius: 2.5px; background: linear-gradient(90deg, var(--fe-accent) 0%, #e8a838 30%, #6b9a4a 60%, #5a7a8a 100%); }
.nemesis-bar-mini .nemesis-bar-fill { top: -2px; height: 9px; width: 2px; }
.pool-preview-card strong { color: var(--fe-ink); font-size: 12px; }
.pool-preview-fields { margin: 4px 0 0; }
.pool-preview-fields > div { display: flex; gap: 5px; padding: 1.5px 0; }
.pool-preview-fields dt { flex: 0 0 52px; color: var(--fe-ink-3); font-size: 10px; }
.pool-preview-fields dd { flex: 1; min-width: 0; margin: 0; color: var(--fe-ink-2); font-size: 10px; line-height: 1.5; }

/* —— 故事丰富度：蜡封滑块 —— */
.richness-slider { display: flex; align-items: center; gap: 7px; margin-top: 2px; }
.seal-mark {
  display: grid; width: 22px; height: 22px; flex: 0 0 auto; place-items: center;
  border-radius: 999px; font-size: 10px; font-weight: 800; line-height: 1;
  color: #fff; user-select: none;
  /* 火漆印质感：深红底 + 边缘暗圈 + 微高光 */
  background: radial-gradient(circle at 32% 28%, color-mix(in srgb, var(--fe-accent) 78%, white), var(--fe-accent) 52%, var(--fe-danger));
  box-shadow: inset 0 0 0 2.5px rgb(255 255 255 / 14%), 0 1px 3px rgb(90 30 20 / 35%);
}
.seal-max { filter: saturate(.85) brightness(.92); }
.richness-range { flex: 1; min-width: 0; appearance: none; -webkit-appearance: none; height: 18px; background: transparent; cursor: grab; }
.richness-range:active { cursor: grabbing; }
.richness-range::-webkit-slider-runnable-track {
  height: 6px; border-radius: 999px;
  background: linear-gradient(90deg, var(--fe-border), color-mix(in srgb, var(--fe-warn) 55%, var(--fe-panel)));
  box-shadow: inset 0 1px 2px rgb(60 50 32 / 25%);
}
.richness-range::-moz-range-track {
  height: 6px; border-radius: 999px;
  background: linear-gradient(90deg, var(--fe-border), color-mix(in srgb, var(--fe-warn) 55%, var(--fe-panel)));
  box-shadow: inset 0 1px 2px rgb(60 50 32 / 25%);
}
/* 拇指做成小火漆章：拖动的就是一枚印章。 */
.richness-range::-webkit-slider-thumb {
  appearance: none; -webkit-appearance: none;
  width: 20px; height: 20px; margin-top: -7px;
  border-radius: 999px; border: 2px solid var(--fe-panel);
  background: radial-gradient(circle at 32% 28%, color-mix(in srgb, var(--fe-accent) 78%, white), var(--fe-accent) 55%, var(--fe-danger));
  box-shadow: inset 0 0 0 2px rgb(255 255 255 / 12%), 0 1px 4px rgb(90 30 20 / 40%);
  transition: transform 120ms ease;
}
.richness-range::-webkit-slider-thumb:hover { transform: scale(1.08); }
.richness-range::-moz-range-thumb {
  width: 16px; height: 16px;
  border-radius: 999px; border: 2px solid var(--fe-panel);
  background: radial-gradient(circle at 32% 28%, color-mix(in srgb, var(--fe-accent) 78%, white), var(--fe-accent) 55%, var(--fe-danger));
  box-shadow: inset 0 0 0 2px rgb(255 255 255 / 12%), 0 1px 4px rgb(90 30 20 / 40%);
}
.richness-range:focus-visible { outline: 2px solid var(--fe-accent); outline-offset: 3px; border-radius: 999px; }
.richness-badge {
  display: inline-flex; align-items: center; gap: 4px;
  border: 1px solid currentColor; border-radius: 4px;
  padding: 0 6px; font-size: 10px; font-weight: 700;
}
.richness-badge[data-tier='轻盈'] { color: var(--fe-ok); background: color-mix(in srgb, var(--fe-ok) 8%, var(--fe-panel)); }
.richness-badge[data-tier='适中'] { color: color-mix(in srgb, var(--fe-warn) 72%, var(--fe-ink)); background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel)); }
.richness-badge[data-tier='厚重'] { color: var(--fe-warn); background: color-mix(in srgb, var(--fe-warn) 16%, var(--fe-panel)); }
.richness-badge[data-tier='沉浸'] { color: var(--fe-accent); background: var(--fe-accent-soft); }
.upload-zone { display: flex; min-height: 64px; cursor: pointer; align-items: center; gap: 8px; border: 1px dashed var(--fe-border-strong); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 8px 12px; transition: border-color 140ms ease, background-color 140ms ease; }
.upload-zone:hover { border-color: var(--fe-accent); }
.upload-chip { display: flex; min-height: 34px; cursor: pointer; align-items: center; gap: 6px; border: 1px dashed var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 5px 9px; font-size: 11px; color: var(--fe-ink-2); transition: border-color 140ms ease, background-color 140ms ease; }
.upload-chip:hover { border-color: var(--fe-accent); }
.work-picker { position: relative; }
.work-picker-trigger { display: flex; height: 36px; align-items: center; gap: 6px; padding: 0 8px; font-size: 13px; }
.picker-overlay { position: fixed; inset: 0; z-index: 40; }
.work-picker-panel { position: absolute; z-index: 50; top: calc(100% + 4px); left: 0; right: 0; overflow: hidden; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); box-shadow: var(--fe-shadow-2); animation: panel-in 160ms ease-out; }
.work-picker-search { display: flex; align-items: center; gap: 6px; border-bottom: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); padding: 7px 9px; }
.work-picker-search input { min-width: 0; flex: 1; border: 0; background: transparent; font-size: 12px; outline: none; }
.work-picker-list { max-height: 240px; overflow-y: auto; }
.work-option { display: block; width: 100%; overflow: hidden; padding: 7px 10px; text-align: left; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: var(--fe-ink); transition: background-color 100ms ease; }
.work-option:hover { background: var(--fe-panel-2); }
.work-option.active { background: color-mix(in srgb, var(--fe-accent) 8%, var(--fe-panel)); color: var(--fe-accent); font-weight: 700; }
.proposal-card { border-radius: 0 var(--fe-radius) var(--fe-radius) 0; }
.status-badge { transition: background-color 200ms ease, border-color 200ms ease, color 200ms ease; }
.save-point { display: flex; gap: 6px; }
.save-list { display: flex; max-height: 300px; flex-direction: column; gap: 8px; overflow-y: auto; }
.save-card { border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 9px 10px; transition: border-color 160ms ease, box-shadow 160ms ease; }
.save-card.flash { animation: save-flash 2.4s ease-out; }
.save-mode-badge { flex: 0 0 auto; border: 1px solid var(--fe-border); border-radius: 4px; background: var(--fe-panel-2); padding: 1px 5px; color: var(--fe-ink-2); font-size: 9px; font-weight: 700; }
.save-mode-badge.enhanced { border-color: color-mix(in srgb, var(--fe-warn) 55%, var(--fe-panel)); background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel)); color: color-mix(in srgb, var(--fe-warn) 72%, var(--fe-ink)); }
.options-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 8px; }
.option-card { display: flex; align-items: flex-start; gap: 8px; border: 1px solid var(--fe-border); border-left-width: 3px; border-radius: var(--fe-radius); background: var(--fe-panel); padding: 9px 10px; text-align: left; transition: transform 120ms ease, box-shadow 140ms ease, background-color 140ms ease, opacity 140ms ease; }
.option-card:hover:not(:disabled) { transform: translateY(-2px); box-shadow: var(--fe-shadow-2); }
.option-card:active:not(:disabled) { transform: scale(.98); }
.option-card:disabled { opacity: .55; }
.option-card.selected { background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel)); box-shadow: 0 0 0 2px rgb(168 117 22 / 25%); }
.option-key { display: grid; width: 22px; height: 22px; flex: 0 0 auto; place-items: center; border: 1.5px solid; border-radius: var(--fe-radius); font-size: 11px; font-weight: 800; }
.option-text { min-width: 0; overflow-wrap: anywhere; color: var(--fe-ink); font-size: 12.5px; line-height: 1.55; }
.opt-a { border-left-color: var(--fe-accent); } .opt-a .option-key { border-color: var(--fe-accent); color: var(--fe-accent); }
.opt-b { border-left-color: var(--fe-ok); } .opt-b .option-key { border-color: var(--fe-ok); color: var(--fe-ok); }
.opt-c { border-left-color: var(--fe-warn); } .opt-c .option-key { border-color: var(--fe-warn); color: var(--fe-warn); }
.opt-d { border-left-color: var(--fe-ink); } .opt-d .option-key { border-color: var(--fe-ink); color: var(--fe-ink); }
.opt-e { border-left-color: var(--fe-danger); } .opt-e .option-key { border-color: var(--fe-danger); color: var(--fe-danger); }
.opt-f { border-left-color: color-mix(in srgb, var(--fe-ok) 78%, black); } .opt-f .option-key { border-color: color-mix(in srgb, var(--fe-ok) 78%, black); color: color-mix(in srgb, var(--fe-ok) 78%, black); }
.options-placeholder { display: flex; margin-top: 8px; align-items: center; justify-content: center; gap: 8px; border: 1px dashed var(--fe-border); border-radius: var(--fe-radius); padding: 13px 8px; color: var(--fe-ink-3); font-size: 11px; }
.options-placeholder.waiting { animation: placeholder-pulse 1.6s ease-in-out infinite; }
.ask-card { border: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); border-radius: var(--fe-radius); background: var(--fe-panel); }
.ask-toggle { display: flex; width: 100%; align-items: center; gap: 6px; padding: 6px 9px; color: var(--fe-ink-2); font-size: 11px; font-weight: 700; }
.ask-toggle .chevron { transition: transform 180ms ease; }
.ask-note { font-size: 10px; font-weight: 400; color: var(--fe-ink-3); }
.ask-body { border-top: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); padding: 8px 9px; }
.ask-thread { display: flex; max-height: 150px; flex-direction: column; gap: 6px; margin-bottom: 8px; overflow-y: auto; }
.ask-q { font-size: 11px; font-weight: 700; color: var(--fe-ink); }
.ask-a { margin-top: 2px; white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 5px 8px; font-size: 11px; line-height: 1.6; color: var(--fe-ink-2); }
.anchor-timeline { display: flex; align-items: center; overflow-x: auto; border-bottom: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); background: var(--fe-bg); padding: 9px 12px 13px; }
.tl-node { position: relative; display: flex; flex: 0 0 auto; align-items: center; gap: 6px; border: 1px solid var(--fe-border); border-radius: 5px 5px 2px 2px; background: var(--fe-panel); padding: 4px 9px 5px; box-shadow: 0 1px 2px rgb(60 50 32 / 8%); animation: tl-in 320ms ease-out both; }
.tl-node::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -5px;
  width: 7px;
  height: 7px;
  transform: translateX(-50%) rotate(45deg);
  border-right: 1px solid var(--fe-border);
  border-bottom: 1px solid var(--fe-border);
  background: var(--fe-panel);
}
.tl-past { border-color: color-mix(in srgb, var(--fe-ok) 30%, var(--fe-panel)); }
.tl-past::after { border-color: color-mix(in srgb, var(--fe-ok) 30%, var(--fe-panel)); }
.tl-current { border-color: var(--fe-accent); background: color-mix(in srgb, var(--fe-accent) 8%, var(--fe-panel)); }
.tl-current::after { border-color: var(--fe-accent); background: color-mix(in srgb, var(--fe-accent) 8%, var(--fe-panel)); }
.tl-dot { display: grid; width: 15px; height: 15px; flex: 0 0 auto; place-items: center; border: 1.5px solid var(--fe-border); border-radius: 999px; background: var(--fe-panel); color: #fff; }
.tl-past .tl-dot { border-color: var(--fe-ok); background: var(--fe-ok); }
.tl-current .tl-dot { border-color: var(--fe-accent); background: var(--fe-accent); animation: tl-pulse 1.6s ease-in-out infinite; }
.tl-line { width: 20px; height: 1.5px; flex: 0 0 auto; margin: 0 6px; background: var(--fe-border); }
.tl-chapter { display: block; font-size: 9px; color: var(--fe-ink-3); }
.tl-title { display: block; max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10.5px; font-weight: 700; color: var(--fe-ink); }
.tl-past .tl-title { color: var(--fe-ok); }
.tl-current .tl-title { color: var(--fe-accent); }
.metric { padding: 12px; border-color: var(--fe-border); }
.metric span { display: block; color: var(--fe-ink-3); font-size: 10px; }
.metric strong { display: block; margin-top: 2px; font-size: 15px; }
.metric-token { font-size: 13px !important; }
.metric-token-badge {
  margin-left: 5px;
  border: 1px solid color-mix(in srgb, var(--fe-ok) 45%, transparent);
  border-radius: 4px;
  padding: 0 4px;
  color: var(--fe-ok);
  font-size: 9px;
  font-style: normal;
  font-weight: 700;
  vertical-align: 1px;
}
.state-section { border-bottom: 1px solid var(--fe-border); padding: 12px; }
.state-section h3 { display: flex; align-items: center; gap: 7px; margin-bottom: 10px; font-size: 12px; font-weight: 700; }
.compact-list { overflow: hidden; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); }
.compact-list div { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); gap: 8px; border-bottom: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); padding: 7px 9px; font-size: 10px; }
.compact-list div:last-child { border-bottom: 0; }
.compact-list dt { color: var(--fe-ink-3); }
.compact-list dd { min-width: 0; overflow-wrap: anywhere; text-align: right; color: var(--fe-ink); font-weight: 650; }
.empty-state { border: 1px dashed var(--fe-border); border-radius: var(--fe-radius); padding: 18px 8px; text-align: center; color: var(--fe-ink-3); font-size: 10px; }
.mobile-tab { display: flex; min-width: 0; flex-direction: column; align-items: center; justify-content: center; gap: 3px; color: var(--fe-ink-3); font-size: 10px; transition: color 140ms ease; }
.mobile-tab.active { color: var(--fe-accent); font-weight: 700; }

/* fill 用 backwards 而非 both：both 会让 to 帧的 opacity:1/transform:none 在动画
   结束后永久压过内联的轮盘淡化样式（动画优先级高于 inline），导致「距焦点越远
   越淡」从未真正生效。backwards 保留入场起始帧，结束即释放控制权。 */
.chat-message { animation: message-in 260ms ease-out backwards; }
.member-card { animation: message-in 220ms ease-out both; }

/* 轮盘式叙事：距当前焦点越远越淡化，过渡自然。 */
.wheel-transition { transition: opacity 520ms ease, transform 520ms ease; }
.chat-message.is-focus { position: relative; padding-left: 18px; }
.chat-message.is-focus::before {
  content: '';
  position: absolute;
  left: 0;
  top: 2px;
  bottom: 2px;
  width: 3px;
  border-radius: 2px;
  background: linear-gradient(180deg, var(--fe-accent), color-mix(in srgb, var(--fe-accent) 55%, var(--fe-warn)));
  opacity: .8;
}

.book-page {
  position: relative;
  border: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel));
  border-radius: 3px;
  /* 输出框纸张：米黄纸底 + 隐约纤维纹理 + 轻微明暗斑驳，不抢正文 */
  background:
    repeating-linear-gradient(0deg, rgb(120 96 54 / 2.6%) 0 1px, transparent 1px 3px),
    repeating-linear-gradient(90deg, rgb(120 96 54 / 2.2%) 0 1px, transparent 1px 4px),
    radial-gradient(ellipse 90% 60% at 18% 8%, rgb(190 158 100 / 7%), transparent 55%),
    radial-gradient(ellipse 70% 50% at 88% 92%, rgb(160 128 72 / 6%), transparent 60%),
    var(--fe-panel);
  padding: 30px clamp(18px, 5vw, 56px) 36px;
  box-shadow:
    inset 26px 0 30px -30px rgb(90 74 46 / 28%),
    0 2px 10px rgb(60 50 32 / 8%),
    0 12px 32px rgb(60 50 32 / 7%);
}
/* 右下角隐约的火漆封缄：像旧信件出口处的一枚蜡印，只做氛围。 */
.book-page::after {
  content: '书中织梦';
  position: absolute;
  right: 26px;
  bottom: 20px;
  display: grid;
  width: 52px;
  height: 52px;
  place-items: center;
  border-radius: 999px;
  color: rgb(140 42 28 / .13);
  font-family: var(--font-serif);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 2px;
  text-align: center;
  background:
    radial-gradient(circle at 34% 30%, rgb(182 58 43 / .14), rgb(141 44 32 / .16) 55%, rgb(110 30 20 / .19));
  box-shadow:
    inset 0 0 0 3px rgb(255 250 240 / .10),
    inset 0 -2px 4px rgb(80 20 12 / .08),
    0 1px 3px rgb(90 40 24 / .07);
  transform: rotate(-9deg);
  pointer-events: none;
  user-select: none;
}
.narrative-body {
  font-family: var(--font-serif);
  font-size: 15.75px;
  line-height: 1.9;
  color: var(--fe-ink);
  overflow-wrap: anywhere;
}
.narrative-para { margin: 0 0 .9em; text-indent: 2em; /* 叙事正文用楷体书卷气（UI 仍为雅黑） */ font-family: var(--font-serif); line-height: 1.85; }
.narrative-para.first { text-indent: 0; }
.narrative-para:last-child { margin-bottom: 0; }
.drop-cap {
  float: left;
  margin: 4px 9px 0 0;
  color: var(--fe-accent);
  font-family: var(--font-serif);
  font-size: 2.55em;
  font-weight: 700;
  line-height: .92;
}
.narrative-chapter {
  position: relative;
  margin: 1.35em 0 1em;
  padding-bottom: .55em;
  color: var(--fe-ink);
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: .08em;
  text-align: center;
}
.narrative-chapter::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: 0;
  width: 72px;
  height: 5px;
  transform: translateX(-50%);
  border-bottom: 1px solid var(--fe-border-strong);
  background:
    linear-gradient(45deg, transparent 44%, var(--fe-accent) 44%, var(--fe-accent) 56%, transparent 56%) center / 7px 5px no-repeat;
}
.font-large .narrative-body { font-size: 17px; }

/* 玩家行动纸条：牛皮纸便签质感 */
.kraft-note {
  background:
    repeating-linear-gradient(0deg, rgb(110 86 48 / 3%) 0 1px, transparent 1px 4px),
    radial-gradient(ellipse 80% 100% at 100% 0%, rgb(170 140 90 / 6%), transparent 60%),
    var(--fe-panel-3);
  box-shadow: inset 0 0 0 1px rgb(120 96 54 / 8%), 0 1px 2px rgb(60 50 32 / 6%);
}

.designer-entry {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  border: 1px solid var(--fe-border);
  border-left: 3px solid var(--fe-accent);
  border-radius: var(--fe-radius);
  background: var(--fe-panel);
  padding: 8px 10px;
  color: var(--fe-ink);
  font-weight: 700;
  transition: border-color 140ms ease, background-color 140ms ease, transform 100ms ease;
}
.designer-entry:hover { border-color: var(--fe-accent); background: color-mix(in srgb, var(--fe-accent) 6%, var(--fe-panel)); }
.designer-entry:active { transform: scale(.98); }

.chapter-track { height: 3px; background: var(--fe-panel-3); }
.chapter-fill { display: block; height: 100%; background: var(--fe-accent); transition: width 500ms ease; }

.compression-toast {
  position: absolute;
  z-index: 20;
  inset-inline: 16px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-inline: auto;
  max-width: 640px;
  border: 1px solid color-mix(in srgb, var(--fe-warn) 55%, var(--fe-panel));
  border-radius: var(--fe-radius);
  background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel));
  padding: 8px 12px;
  color: color-mix(in srgb, var(--fe-warn) 72%, var(--fe-ink));
  font-size: 11px;
  line-height: 1.5;
  box-shadow: var(--fe-shadow-2);
}

.quest-card {
  border: 1px solid var(--fe-border);
  border-left: 3px solid var(--fe-accent);
  border-radius: var(--fe-radius);
  background: var(--fe-panel);
  padding: 10px;
  animation: quest-in 280ms ease-out both;
  transition: border-color 200ms ease, background-color 200ms ease;
}
.quest-card.completed { border-left-color: var(--fe-ok); }
.quest-card.failed { border-left-color: var(--fe-danger); }
.quest-title { display: block; font-size: 12px; font-weight: 700; color: var(--fe-ink); }
.quest-req { margin-top: 6px; padding-left: 14px; list-style: disc; font-size: 11px; line-height: 1.6; color: var(--fe-ink-2); }
.quest-goal { margin-top: 6px; overflow-wrap: anywhere; font-size: 11px; line-height: 1.6; color: var(--fe-ink-2); }
.quest-meta { margin-top: 5px; font-size: 10px; color: var(--fe-ink-3); }
.quest-result { margin-top: 6px; font-size: 11px; font-weight: 700; }
.quest-result.completed { color: var(--fe-ok); }
.quest-result.failed { color: var(--fe-danger); }
.quest-progress-track { margin-top: 8px; height: 6px; overflow: hidden; border-radius: 999px; background: var(--fe-panel-3); }
.quest-progress-fill { display: block; height: 100%; border-radius: 999px; background: var(--fe-accent); transition: width 500ms ease; }
.quest-progress-fill.ready { background: var(--fe-ok); }
.break-anchor-panel { border-left: 2px solid #2563eb; border-radius: 0 var(--fe-radius) var(--fe-radius) 0; background: var(--fe-panel); padding: 8px 10px; }
.quest-flash-msg {
  margin-top: 8px;
  border: 1px solid color-mix(in srgb, var(--fe-warn) 55%, var(--fe-panel));
  border-radius: var(--fe-radius);
  background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel));
  padding: 4px 8px;
  font-size: 10px;
  color: color-mix(in srgb, var(--fe-warn) 72%, var(--fe-ink));
}

.conv-bar {
  position: relative;
  height: 10px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--fe-accent), #2563eb);
}
.conv-tick {
  position: absolute;
  top: 50%;
  width: 6px;
  height: 6px;
  transform: translate(-50%, -50%) rotate(45deg);
  border-radius: 1px;
  background: var(--fe-panel);
  opacity: .9;
}
.conv-dot { position: absolute; top: 50%; transform: translate(-50%, -50%); border-radius: 999px; }
.conv-dot.current {
  width: 12px;
  height: 12px;
  border: 2px solid #fff;
  background: var(--fe-ink);
  box-shadow: 0 1px 3px rgb(36 35 33 / 35%);
  transition: left 600ms ease;
}
.conv-dot.settled {
  width: 7px;
  height: 7px;
  border: 1.5px solid #fff;
  background: transparent;
  transition: left 600ms ease;
}

.nemesis-card {
  border: 1px solid var(--fe-border);
  border-left: 3px solid var(--fe-danger);
  border-radius: var(--fe-radius);
  background: var(--fe-panel);
  padding: 10px;
  animation: nemesis-in 300ms ease-out both;
}
.nemesis-text { overflow-wrap: anywhere; white-space: pre-wrap; font-size: 11px; line-height: 1.65; color: var(--fe-ink-2); }
.distortion-row { display: flex; margin-top: 8px; align-items: center; gap: 8px; }
.distortion-track { height: 5px; flex: 1; overflow: hidden; border-radius: 999px; background: var(--fe-panel-3); }
.distortion-fill { display: block; height: 100%; border-radius: 999px; background: var(--fe-danger); transition: width 500ms ease; }
.distortion-value { flex: 0 0 auto; font-size: 9px; color: var(--fe-ink-3); }
.distortion-note { margin-top: 4px; font-size: 9px; color: var(--fe-ink-3); }

@keyframes quest-in {
  from { opacity: 0; transform: perspective(420px) rotateX(-8deg) translateY(6px); }
  to { opacity: 1; transform: none; }
}
@keyframes nemesis-in {
  from { opacity: 0; transform: translateX(14px); }
  to { opacity: 1; transform: translateX(0); }
}

.pop-enter-active, .pop-leave-active { transition: opacity 180ms ease, transform 180ms ease; }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateY(-4px) scale(.98); }

@keyframes message-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes panel-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes tl-in {
  from { opacity: 0; transform: translateX(16px); }
  to { opacity: 1; transform: translateX(0); }
}
@keyframes tl-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgb(182 58 43 / 35%); }
  50% { box-shadow: 0 0 0 5px rgb(182 58 43 / 0%); }
}
@keyframes placeholder-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .55; }
}
@keyframes save-flash {
  0%, 60% { border-color: color-mix(in srgb, var(--fe-warn) 55%, var(--fe-panel)); box-shadow: 0 0 0 2px rgb(201 162 74 / 40%); }
  100% { border-color: var(--fe-border); box-shadow: none; }
}

@media (max-width: 640px) {
  .options-grid { grid-template-columns: 1fr; }
}

.font-large .field { font-size: 15px; }
.font-large .label { font-size: 13px; }
.font-large .section-toggle { font-size: 14px; }

.reduce-motion *, .reduce-motion *::before, .reduce-motion *::after {
  animation-duration: .01ms !important;
  animation-iteration-count: 1 !important;
  transition-duration: .01ms !important;
}

@media (prefers-reduced-motion: reduce) {
  .app-root *, .app-root *::before, .app-root *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}

/* ==================== 品牌动效层（书中织梦 · Novelborne）====================
 * 统一缓动与浮起语言：入场「落墨」、悬停「浮起」、按压「沉纸」。
 * 全部叠加在现有元素上（不改变布局）；reduce-motion 由上方全局兜底关闭。 */

/* 缓动令牌：品牌弹性曲线（落纸的轻回弹） */
.app-root {
  --ease-brand: cubic-bezier(.34, 1.3, .5, 1);
  --lift-1: translateY(-1px);
  --lift-2: translateY(-2px);
}

/* —— 顶栏品牌区：火漆印「盖下」入场 + 标题渐显 —— */
.seal-logo { animation: seal-stamp 520ms var(--ease-brand) both; }
@keyframes seal-stamp {
  0% { transform: scale(1.5) rotate(-8deg); opacity: 0; box-shadow: 0 6px 18px rgb(90 30 20 / 0%); }
  55% { transform: scale(.94) rotate(1deg); opacity: 1; }
  100% { transform: scale(1) rotate(0deg); }
}
.seal-logo:hover { transform: var(--lift-1); transition: transform var(--fe-motion, 140ms) var(--ease-brand); }

/* —— 副标题经纬引线：一根细线自书脊引出，末端一枚织点（随主题强调色） —— */
.title-loom { position: relative; padding-left: 14px; animation: title-rise 640ms ease-out 120ms both; }
.title-loom::before {
  content: "";
  position: absolute;
  left: 0;
  top: 55%;
  width: 9px;
  height: 1.5px;
  background: linear-gradient(90deg, transparent, var(--fe-accent, var(--fe-accent)));
  border-radius: 2px;
}
.title-loom::after {
  content: "";
  position: absolute;
  left: 10px;
  top: calc(55% - 1.5px);
  width: 3px;
  height: 3px;
  border-radius: 999px;
  background: var(--fe-accent, var(--fe-accent));
  animation: loom-knot-pulse 3.8s ease-in-out infinite;
}
@keyframes loom-knot-pulse {
  0%, 100% { transform: scale(1); opacity: .9; }
  50% { transform: scale(1.35); opacity: .55; }
}
@keyframes title-rise {
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: translateY(0); }
}

/* —— 标题本体：入场墨迹渐显 —— */
.app-root h1 { animation: title-rise 520ms ease-out 60ms both; }

/* —— 通用按钮：悬停浮起 + 弹性回弹（叠加在原 transition 之上） —— */
.icon-button, .small-action, .theme-choice, .skill-tab {
  transition-timing-function: var(--ease-brand, ease);
}
.icon-button:hover:not(:disabled), .small-action:hover:not(:disabled), .theme-choice:hover {
  transform: var(--lift-1);
}
.icon-button:active:not(:disabled), .small-action:active:not(:disabled) {
  transform: translateY(.5px) scale(.96);
}

/* —— 面板浮层（轮播/弹层类）：入纸的轻弹 —— */
.theme-picker, .status-badge {
  animation: title-rise 420ms ease-out 160ms both;
}

/* —— 圆润补强：滚动条胶囊化（全局浅色主题安全） —— */
.app-root ::-webkit-scrollbar { width: 8px; height: 8px; }
.app-root ::-webkit-scrollbar-track { background: transparent; }
.app-root ::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--fe-ink, var(--fe-ink-2)) 22%, transparent);
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: padding-box;
}
.app-root ::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--fe-ink, var(--fe-ink-2)) 34%, transparent);
  background-clip: padding-box;
}


@media (max-width: 1023px) {
  .panel-left, .panel-right, .story-panel { grid-column: 1; grid-row: 1; width: 100%; min-width: 0; }
}
</style>
