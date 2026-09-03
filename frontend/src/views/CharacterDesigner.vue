<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ArrowLeft,
  ArrowRight,
  BookUser,
  Check,
  CircleAlert,
  FileText,
  LoaderCircle,
  Plus,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
  Wand2,
} from 'lucide-vue-next'
import {
  createCharacterCard,
  generateCharacterDesign,
  getCharacterDesignerSchema,
  saveCharacterDesign,
  type ModelConnectionParams,
} from '../api'
import type {
  CharacterDesignerGenerateResult,
  DesignerCorpusItem,
  DesignerCorpusKind,
  DesignerField,
  DesignerIdentity,
  DesignerQuestion,
  DesignerQuestionOption,
} from '../types'

const props = defineProps<{
  connection: ModelConnectionParams
  sessionId: string | null
}>()

const emit = defineEmits<{
  close: []
  saved: [label: string]
}>()

type Step = 0 | 1 | 2 | 3
const STEPS = ['定身份', '语料', '选择题', '生成'] as const

const step = ref<Step>(0)
const schemaLoading = ref(true)
const schemaError = ref('')

const DEFAULT_ROLE_TYPES = ['主角', '伙伴', '女主', '反派', '配角']
const DEFAULT_GENDERS = ['', 'male', 'female']
const DEFAULT_POSITIONS = ['', '主角', '男主', '女主', '配角', '反派']
const DEFAULT_CORPUS_KINDS: DesignerCorpusKind[] = [
  { id: 'original_text', label: '原著原文', hint: '原文片段、台词。最高优先级。' },
  { id: 'official_setting', label: '官方设定', hint: '设定集、人物小传，作事实层。' },
  { id: 'user_impression', label: '用户印象', hint: '你的口述理解，与原著冲突时让位原著。' },
  { id: 'reference_character', label: '参照角色', hint: '只借原型与语言骨架，不借具体设定。' },
]
const DEFAULT_FIELDS: DesignerField[] = [
  { key: 'name', label: '角色名', required: true, placeholder: '例如：沈砚' },
  { key: 'work', label: '出处作品', placeholder: '例如：城门风硬' },
  { key: 'role_type', label: '角色定位', options: DEFAULT_ROLE_TYPES },
  { key: 'gender', label: '性别（male/female）', options: DEFAULT_GENDERS },
  { key: 'original_position', label: '原著定位（影响宿敌强度评估）', options: DEFAULT_POSITIONS },
  { key: 'archetype', label: '性格原型', placeholder: '例如：谨慎隐忍的求道者' },
  { key: 'one_line', label: '一句话概括', placeholder: '用一句话说出这个角色的魂' },
]

const identityFields = ref<DesignerField[]>(DEFAULT_FIELDS)
const corpusKinds = ref<DesignerCorpusKind[]>(DEFAULT_CORPUS_KINDS)
const questions = ref<DesignerQuestion[]>([])

const identity = ref<Record<string, string>>({ name: '', work: '', role_type: '主角', gender: '', original_position: '', archetype: '', one_line: '' })
const corpus = ref<DesignerCorpusItem[]>([])
const answers = ref<Record<string, string>>({})
const skippedQuestions = ref<Set<string>>(new Set())

const generating = ref(false)
const generateError = ref('')
const result = ref<CharacterDesignerGenerateResult | null>(null)
const saveFilename = ref('')
const saving = ref(false)
const saveError = ref('')
const saveDone = ref('')
const personaOpen = ref(false)

const MAX_CORPUS = 10
const MAX_CORPUS_CHARS = 8000

function normalizeField(raw: unknown): DesignerField | null {
  if (typeof raw === 'string' && raw.trim()) return { key: raw.trim() }
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const record = raw as Record<string, unknown>
    const key = String(record.key ?? record.name ?? record.id ?? '').trim()
    if (!key) return null
    return {
      key,
      label: typeof record.label === 'string' ? record.label : undefined,
      required: Boolean(record.required),
      options: Array.isArray(record.options)
        ? record.options.filter((item): item is string => typeof item === 'string')
        : undefined,
      placeholder: typeof record.placeholder === 'string' ? record.placeholder : undefined,
      multiline: Boolean(record.multiline),
    }
  }
  return null
}

function normalizeQuestion(raw: unknown, index: number): DesignerQuestion | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const record = raw as Record<string, unknown>
  const question = String(record.question ?? record.text ?? record.title ?? '').trim()
  if (!question) return null
  const rawOptions = Array.isArray(record.options) ? record.options : []
  const options: DesignerQuestionOption[] = rawOptions
    .map((item, optionIndex): DesignerQuestionOption | null => {
      if (typeof item === 'string' && item.trim()) {
        return { key: String.fromCharCode(65 + optionIndex), text: item.trim() }
      }
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        const option = item as Record<string, unknown>
        const text = String(option.text ?? option.label ?? option.value ?? '').trim()
        if (!text) return null
        return { key: String(option.key ?? String.fromCharCode(65 + optionIndex)), text }
      }
      return null
    })
    .filter((item): item is DesignerQuestionOption => Boolean(item))
  return { id: String(record.id ?? record.key ?? `q${index + 1}`), question, options }
}

async function loadSchema(): Promise<void> {
  schemaLoading.value = true
  schemaError.value = ''
  try {
    const schema = await getCharacterDesignerSchema()
    const fields = (Array.isArray(schema.identity_fields) ? schema.identity_fields : [])
      .map(normalizeField)
      .filter((item): item is DesignerField => Boolean(item))
    if (fields.length) {
      const merged = [...fields]
      for (const fallback of DEFAULT_FIELDS) {
        if (!merged.some((field) => field.key === fallback.key)) merged.push(fallback)
      }
      identityFields.value = merged
    }
    if (Array.isArray(schema.corpus_kinds) && schema.corpus_kinds.length) {
      const kinds = schema.corpus_kinds
        .map((item): DesignerCorpusKind | null => {
          if (typeof item === 'string' && item.trim()) return { id: item.trim(), label: item.trim() }
          if (item && typeof item === 'object' && !Array.isArray(item)) {
            const record = item as unknown as Record<string, unknown>
            const id = String(record.id ?? record.key ?? '').trim()
            if (!id) return null
            return {
              id,
              label: String(record.label ?? id).trim() || id,
              hint: typeof record.hint === 'string' ? record.hint : undefined,
              priority: typeof record.priority === 'number' ? record.priority : undefined,
            }
          }
          return null
        })
        .filter((item): item is DesignerCorpusKind => Boolean(item))
        .sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99))
      if (kinds.length) corpusKinds.value = kinds
    }
    questions.value = (Array.isArray(schema.questions) ? schema.questions : [])
      .map(normalizeQuestion)
      .filter((item): item is DesignerQuestion => Boolean(item))
      .slice(0, 10)
    if (!corpus.value.length) addCorpus()
  } catch (cause) {
    schemaError.value = cause instanceof Error ? cause.message : '设计器配置加载失败'
  } finally {
    schemaLoading.value = false
  }
}

function fieldLabel(field: DesignerField): string {
  return field.label || DEFAULT_FIELDS.find((item) => item.key === field.key)?.label || field.key
}

function fieldOptions(field: DesignerField): string[] | null {
  if (field.options?.length) return field.options
  if (field.key === 'role_type') return DEFAULT_ROLE_TYPES
  return null
}

function isLongField(field: DesignerField): boolean {
  return Boolean(field.multiline) || field.key === 'one_line'
}

const nameValid = computed(() => Boolean(identity.value.name?.trim()))
const answeredCount = computed(() => Object.keys(answers.value).length)
const corpusFilledCount = computed(() => corpus.value.filter((item) => item.text.trim()).length)

function addCorpus(): void {
  if (corpus.value.length >= MAX_CORPUS) return
  corpus.value.push({ kind: corpusKinds.value[0]?.id ?? 'original_text', text: '' })
}

function removeCorpus(index: number): void {
  corpus.value.splice(index, 1)
}

function corpusCount(item: DesignerCorpusItem): number {
  return Array.from(item.text).length
}

function corpusHint(kindId: string): string {
  return corpusKinds.value.find((kind) => kind.id === kindId)?.hint ?? ''
}

function onCorpusInput(item: DesignerCorpusItem): void {
  if (corpusCount(item) > MAX_CORPUS_CHARS) {
    item.text = Array.from(item.text).slice(0, MAX_CORPUS_CHARS).join('')
  }
}

function chooseAnswer(questionId: string, key: string): void {
  answers.value = { ...answers.value, [questionId]: key }
  skippedQuestions.value.delete(questionId)
}

function skipQuestion(questionId: string): void {
  const next = { ...answers.value }
  delete next[questionId]
  answers.value = next
  skippedQuestions.value.add(questionId)
}

function nextStep(): void {
  if (step.value === 0 && !nameValid.value) return
  if (step.value < 3) step.value = (step.value + 1) as Step
}

function prevStep(): void {
  if (step.value > 0) step.value = (step.value - 1) as Step
}

const quality = computed(() => result.value?.quality ?? null)
const qualityLevel = computed(() => String(quality.value?.level || ''))
const qualityLabel = computed(() => {
  if (quality.value?.label) return quality.value.label
  if (qualityLevel.value === 'soulful') return '拥有灵魂'
  if (qualityLevel.value === 'playable') return '及格'
  if (qualityLevel.value === 'flat') return '扁平需补料'
  return '已评估'
})
const qualityBadgeClass = computed(() => {
  if (qualityLevel.value === 'soulful') return 'soul'
  if (qualityLevel.value === 'flat') return 'flat'
  return 'pass'
})
const qualityScore = computed(() => {
  const score = Number(quality.value?.score)
  return Number.isFinite(score) ? score : null
})
const qualityMissing = computed(() =>
  Array.isArray(quality.value?.missing)
    ? quality.value.missing.filter((item): item is string => typeof item === 'string')
    : [],
)
const characterCard = computed<Record<string, unknown>>(() => {
  const raw = result.value
  if (!raw) return {}
  const card = raw.card ?? raw.character_card
  return card && typeof card === 'object' && !Array.isArray(card) ? card : {}
})
const cardEntries = computed<Array<[string, string]>>(() =>
  Object.entries(characterCard.value).map(([key, value]) => [key, displayValue(value)]),
)
const personaMarkdown = computed(() => {
  const raw = result.value
  if (!raw) return ''
  const persona = raw.persona_markdown ?? raw.persona ?? raw.markdown
  return typeof persona === 'string' ? persona : ''
})

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(displayValue).join('、')
  return JSON.stringify(value, null, 2)
}

async function generate(): Promise<void> {
  if (generating.value || !nameValid.value) return
  generating.value = true
  generateError.value = ''
  result.value = null
  saveDone.value = ''
  saveError.value = ''
  try {
    result.value = await generateCharacterDesign({
      identity: { ...identity.value } as DesignerIdentity,
      corpus: corpus.value.filter((item) => item.text.trim()),
      answers: { ...answers.value },
      session_id: props.sessionId,
      provider: props.connection.provider,
      base_url: props.connection.base_url,
      api_key: props.connection.api_key,
      model: props.connection.model,
    })
    saveFilename.value = result.value.suggested_filename?.trim() || identity.value.name.trim()
  } catch (cause) {
    generateError.value = cause instanceof Error ? cause.message : '角色生成失败'
  } finally {
    generating.value = false
  }
}

async function saveToLibrary(): Promise<void> {
  const filename = saveFilename.value.trim() || identity.value.name.trim()
  if (!filename || saving.value || !result.value) return
  saving.value = true
  saveError.value = ''
  try {
    const saved = await saveCharacterDesign({
      filename,
      persona_markdown: personaMarkdown.value,
    })
    saveDone.value = saved.message || `已保存到主角模型库：${saved.label || filename}`
    emit('saved', saved.label || filename)
  } catch (cause) {
    saveError.value = cause instanceof Error ? cause.message : '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(loadSchema)

// --- 保存到角色库（四栏定位，区别于 persona 主角模型库）---
const savingToPool = ref(false)
const poolRoleChoice = ref('伙伴')
const poolSaveDone = ref('')
const poolSaveError = ref('')

function cardString(key: string): string {
  const value = characterCard.value[key]
  return typeof value === 'string' ? value.trim() : ''
}

function cardList(key: string): string[] {
  const value = characterCard.value[key]
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string' && v.trim() !== '')
  if (typeof value === 'string' && value.trim()) return [value.trim()]
  return []
}

// 数据库 role 词表：设计器的「女主」映射 single_heroine；「宿敌」映射反派（反派可饰宿敌）
const POOL_ROLE_TO_DB_ROLE: Record<string, string> = {
  主角: '主角',
  伙伴: '伙伴',
  女主: 'single_heroine',
  宿敌: '反派',
}

// gender 归一：只允许 male/female/unknown（后端 build_record 校验口径）
function normalizeGender(value: unknown): 'male' | 'female' | 'unknown' {
  const text = String(value ?? '').trim().toLowerCase()
  if (text === 'male' || text === 'female' || text === 'unknown') return text
  if (/男|雄/.test(text)) return 'male'
  if (/女|雌/.test(text)) return 'female'
  return 'unknown'
}

async function saveCardToCharacterLibrary(): Promise<void> {
  if (!result.value || savingToPool.value) return
  savingToPool.value = true
  poolSaveDone.value = ''
  poolSaveError.value = ''
  try {
    const name = cardString('name') || identity.value.name.trim()
    if (!name) throw new Error('角色卡缺少名字，无法入库')
    const rawGender = cardString('gender') || identity.value.gender || 'unknown'
    // slot_keys：后端 parse_fusion 已规整为四栏白名单标签；缺失时交给后端四维兜底
    const rawSlots = characterCard.value.slot_keys
    const slotKeys = rawSlots && typeof rawSlots === 'object' && !Array.isArray(rawSlots)
      ? Object.fromEntries(
          Object.entries(rawSlots as Record<string, unknown>)
            .map(([slot, tags]) => [slot.replace('主线栏', '伴侣栏'), Array.isArray(tags) ? tags.filter((t): t is string => typeof t === 'string' && t.trim() !== '') : []]),
        )
      : undefined
    const rawRelationship = characterCard.value.relationship_vector
    const relationshipVector: Record<string, string> | string =
      rawRelationship && typeof rawRelationship === 'object' && !Array.isArray(rawRelationship)
        ? Object.fromEntries(
            Object.entries(rawRelationship as Record<string, unknown>)
              .map(([target, rel]) => [target, String(rel ?? '')])
              .filter(([, rel]) => rel.trim() !== ''),
          )
        : cardString('relationship_vector')
    const rawScope = characterCard.value.knowledge_scope
    const knowledgeScope: string[] | string = Array.isArray(rawScope)
      ? rawScope.filter((v): v is string => typeof v === 'string' && v.trim() !== '')
      : cardString('knowledge_scope')
    await createCharacterCard(
      {
        name,
        role: POOL_ROLE_TO_DB_ROLE[poolRoleChoice.value] ?? '伙伴',
        gender: normalizeGender(rawGender),
        work: cardString('work'),
        archetype: cardString('archetype') || cardString('one_line'),
        desire: cardString('desire'),
        fear: cardString('fear'),
        abilities: cardList('abilities'),
        relationship_vector: relationshipVector,
        knowledge_scope: knowledgeScope,
        voice: cardString('voice'),
        unacceptable_actions: cardList('unacceptable_actions'),
        background: cardString('background'),
        original_position: cardString('original_position'),
        source_medium: cardString('source_medium'),
        source_region: cardString('source_region') || undefined,
        slot_keys: slotKeys,
        source: '角色设计器',
      },
      false,
    )
    poolSaveDone.value = `已加入角色库：${name}（${POOL_ROLE_TO_DB_ROLE[poolRoleChoice.value] ?? poolRoleChoice.value}）`
    emit('saved', name)
  } catch (cause) {
    poolSaveError.value = cause instanceof Error ? cause.message : '入库失败'
  } finally {
    savingToPool.value = false
  }
}
</script>

<template>
  <div class="designer-root">
    <header class="designer-header">
      <button type="button" class="btn ghost" @click="emit('close')">
        <ArrowLeft :size="14" /> 返回
      </button>
      <div class="designer-title">
        <Wand2 :size="16" class="text-(--fe-accent)" />
        <h1>角色设计器</h1>
      </div>
      <ol class="step-bar" aria-label="设计步骤">
        <li
          v-for="(label, index) in STEPS"
          :key="label"
          class="step-item"
          :class="index === step ? 'active' : index < step ? 'done' : ''"
        >
          <span class="step-dot">
            <Check v-if="index < step" :size="10" />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <span class="step-label">{{ label }}</span>
        </li>
      </ol>
    </header>

    <div class="designer-scroll scrollbar">
      <div v-if="schemaLoading" class="grid place-items-center py-24 text-(--fe-ink-3)">
        <LoaderCircle class="animate-spin" :size="22" />
      </div>

      <div v-else-if="schemaError" class="designer-page">
        <div class="error-banner">
          <CircleAlert :size="15" class="mt-0.5 shrink-0" />
          <span class="min-w-0 flex-1">{{ schemaError }}（服务可能尚未就绪）</span>
          <button type="button" class="btn" @click="loadSchema">
            <RotateCcw :size="12" /> 重试
          </button>
        </div>
      </div>

      <div v-else class="designer-page">
        <!-- 第一步：定身份 -->
        <section v-if="step === 0">
          <h2 class="page-heading">定身份</h2>
          <p class="page-note">先决定这个角色是谁。角色名为必填，其余越具体，角色越立体。</p>
          <div class="form-grid">
            <label v-for="field in identityFields" :key="field.key" class="block" :class="isLongField(field) ? 'span-2' : ''">
              <span class="label">
                {{ fieldLabel(field) }}
                <em v-if="field.required || field.key === 'name'" class="required">必填</em>
              </span>
              <select v-if="fieldOptions(field)" v-model="identity[field.key]" class="field h-10 px-2.5 text-[13px]">
                <option v-for="option in fieldOptions(field)" :key="option">{{ option }}</option>
              </select>
              <textarea
                v-else-if="isLongField(field)"
                v-model="identity[field.key]"
                class="field h-16 p-2 text-[13px]"
                :placeholder="field.placeholder"
              />
              <input v-else v-model="identity[field.key]" class="field h-10 px-2.5 text-[13px]" :placeholder="field.placeholder" />
            </label>
          </div>
        </section>

        <!-- 第二步：语料 -->
        <section v-else-if="step === 1">
          <h2 class="page-heading">语料</h2>
          <p class="page-note">喂给设计器的原料：原著片段、官方设定、你的印象或参照角色。最多 {{ MAX_CORPUS }} 条，可留空。</p>
          <div class="space-y-2.5">
            <div v-for="(item, index) in corpus" :key="index" class="corpus-card">
              <div class="flex items-center gap-2">
                <select v-model="item.kind" class="field h-8 w-36 shrink-0 px-2 text-[12px]">
                  <option v-for="kind in corpusKinds" :key="kind.id" :value="kind.id">{{ kind.label }}</option>
                </select>
                <span class="ml-auto shrink-0 text-[10px]" :class="corpusCount(item) >= MAX_CORPUS_CHARS ? 'text-(--fe-danger)' : 'text-(--fe-ink-3)'">
                  {{ corpusCount(item) }}/{{ MAX_CORPUS_CHARS }}
                </span>
                <button type="button" class="icon-mini" title="删除该条" @click="removeCorpus(index)">
                  <Trash2 :size="13" />
                </button>
              </div>
              <p v-if="corpusHint(item.kind)" class="mt-1 text-[10px] leading-4 text-(--fe-border-strong)">{{ corpusHint(item.kind) }}</p>
              <textarea
                v-model="item.text"
                class="field mt-2 h-28 p-2 text-[12.5px] leading-6"
                placeholder="粘贴语料正文…"
                @input="onCorpusInput(item)"
              />
            </div>
          </div>
          <button type="button" class="btn mt-2.5" :disabled="corpus.length >= MAX_CORPUS" @click="addCorpus">
            <Plus :size="13" /> 添加语料（{{ corpus.length }}/{{ MAX_CORPUS }}）
          </button>
        </section>

        <!-- 第三步：选择题 -->
        <section v-else-if="step === 2">
          <h2 class="page-heading">选择题</h2>
          <p class="page-note">用直觉作答，每题都可跳过。已答 {{ answeredCount }}/{{ questions.length }}。</p>
          <p v-if="!questions.length" class="page-note">当前 schema 未提供选择题，可直接进入下一步。</p>
          <div class="space-y-4">
            <div v-for="(question, index) in questions" :key="question.id" class="question-card">
              <div class="flex items-start justify-between gap-2">
                <strong class="text-[13px] leading-6">{{ index + 1 }}. {{ question.question }}</strong>
                <button type="button" class="skip-btn" @click="skipQuestion(question.id)">跳过</button>
              </div>
              <div class="mt-2 grid gap-1.5 sm:grid-cols-2">
                <button
                  v-for="(option, optionIndex) in question.options"
                  :key="option.key"
                  type="button"
                  class="choice-card"
                  :class="answers[question.id] === option.key ? 'selected' : ''"
                  @click="chooseAnswer(question.id, option.key)"
                >
                  <span class="choice-key">{{ String.fromCharCode(65 + optionIndex) }}</span>
                  <span class="min-w-0 flex-1 text-left">{{ option.text }}</span>
                </button>
              </div>
            </div>
          </div>
        </section>

        <!-- 第四步：生成 -->
        <section v-else>
          <h2 class="page-heading">生成</h2>
          <div class="richness-card">
            <Sparkles :size="15" class="mt-0.5 shrink-0 text-(--fe-warn)" />
            <div class="min-w-0 flex-1 text-[12px] leading-6">
              <p>
                输入充实度：语料 {{ corpusFilledCount }} 条 · 已答 {{ answeredCount }}/{{ questions.length }} 题
              </p>
              <p class="text-(--fe-ink-3)">输入越少角色越接近及格线，越多越有灵魂。</p>
            </div>
          </div>

          <div v-if="generating" class="generating-box">
            <LoaderCircle class="animate-spin text-(--fe-accent)" :size="22" />
            <p>正在融合角色灵魂…</p>
          </div>

          <div v-if="generateError" class="error-banner mt-3">
            <CircleAlert :size="15" class="mt-0.5 shrink-0" />
            <span class="min-w-0 flex-1">{{ generateError }}</span>
            <button type="button" class="btn" @click="generate"><RotateCcw :size="12" /> 重试</button>
          </div>

          <div v-if="result && !generating" class="mt-4">
            <div v-if="quality" class="quality-row">
              <span class="quality-badge" :class="qualityBadgeClass">{{ qualityLabel }}</span>
              <span v-if="qualityScore !== null" class="text-[12px] font-bold text-(--fe-ink-2)">评分 {{ qualityScore }}</span>
              <p v-if="qualityMissing.length" class="w-full text-[11px] leading-5 text-(--fe-ink-3)">
                补料提示：{{ qualityMissing.join('、') }}
              </p>
            </div>

            <h3 class="result-heading">角色卡</h3>
            <dl v-if="cardEntries.length" class="card-list">
              <div v-for="entry in cardEntries" :key="entry[0]">
                <dt>{{ entry[0] }}</dt>
                <dd>{{ entry[1] }}</dd>
              </div>
            </dl>
            <p v-else class="page-note">后端未返回结构化角色卡。</p>

            <template v-if="personaMarkdown">
              <button type="button" class="persona-toggle" @click="personaOpen = !personaOpen">
                <FileText :size="13" /> Persona Markdown 预览
                <span class="ml-auto text-[10px] text-(--fe-ink-3)">{{ personaOpen ? '收起' : '展开' }}</span>
              </button>
              <pre v-if="personaOpen" class="persona-block scrollbar">{{ personaMarkdown }}</pre>
            </template>

            <div class="save-row">
              <input v-model="saveFilename" class="field h-10 flex-1 px-2.5 text-[13px]" placeholder="模型文件名（默认角色名）" />
              <button type="button" class="btn primary h-10" :disabled="saving || !(saveFilename.trim() || identity.name.trim())" @click="saveToLibrary">
                <LoaderCircle v-if="saving" class="animate-spin" :size="13" />
                <Save v-else :size="13" /> 保存到主角模型库
              </button>
            </div>
            <div class="save-row">
              <button type="button" class="btn h-10 flex-1 justify-center" :disabled="savingToPool || !result" @click="saveCardToCharacterLibrary">
                <LoaderCircle v-if="savingToPool" class="animate-spin" :size="13" />
                <BookUser v-else :size="13" /> 保存到角色库（四栏选卡可用）
              </button>
              <select v-model="poolRoleChoice" class="field h-10 px-2.5 text-[12px]" title="入库 role：主角→主角，女主→single_heroine，宿敌→反派">
                <option value="主角">主角</option>
                <option value="伙伴">伙伴</option>
                <option value="女主">女主</option>
                <option value="宿敌">宿敌（存为反派）</option>
              </select>
            </div>
            <p v-if="saveError" class="mt-2 text-[11px] text-(--fe-danger)">{{ saveError }}</p>
            <p v-if="saveDone" class="mt-2 text-[11px] font-bold text-(--fe-ok)">{{ saveDone }}</p>
          </div>
        </section>
      </div>
    </div>

    <footer v-if="!schemaLoading && !schemaError" class="designer-footer">
      <button type="button" class="btn" :disabled="step === 0" @click="prevStep">
        <ArrowLeft :size="13" /> 上一步
      </button>
      <button v-if="step < 3" type="button" class="btn primary" :disabled="step === 0 && !nameValid" @click="nextStep">
        下一步 <ArrowRight :size="13" />
      </button>
      <button v-else type="button" class="btn primary" :disabled="generating || !nameValid" @click="generate">
        <LoaderCircle v-if="generating" class="animate-spin" :size="13" />
        <Sparkles v-else :size="13" /> {{ result ? '重新生成' : '生成角色' }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.designer-root {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  flex-direction: column;
  background: var(--fe-bg);
  color: var(--fe-ink);
}
.designer-header {
  display: flex;
  height: 56px;
  flex-shrink: 0;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid var(--fe-border);
  background: var(--fe-panel);
  padding: 0 14px;
}
.designer-title { display: flex; align-items: center; gap: 7px; }
.designer-title h1 { font-size: 14px; font-weight: 800; letter-spacing: .04em; }
.step-bar { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.step-item { display: flex; align-items: center; gap: 5px; padding: 3px 6px; color: var(--fe-ink-3); font-size: 11px; font-weight: 700; }
.step-dot {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border: 1.5px solid var(--fe-border);
  border-radius: 999px;
  background: var(--fe-panel);
  font-size: 10px;
}
.step-item.active { color: var(--fe-accent); }
.step-item.active .step-dot { border-color: var(--fe-accent); color: var(--fe-accent); }
.step-item.done { color: var(--fe-ok); }
.step-item.done .step-dot { border-color: var(--fe-ok); background: var(--fe-ok); color: var(--fe-accent-ink); }
@media (max-width: 640px) {
  .step-label { display: none; }
}

.designer-scroll { flex: 1; overflow-y: auto; }
.designer-page {
  margin: 0 auto;
  width: 100%;
  max-width: 780px;
  padding: 26px clamp(16px, 4vw, 40px) 40px;
}
.page-heading {
  font-family: var(--font-serif);
  font-size: 21px;
  font-weight: 700;
  letter-spacing: .1em;
  color: var(--fe-ink);
  text-align: center;
}
.page-note { margin-top: 8px; text-align: center; font-size: 12px; line-height: 1.8; color: var(--fe-ink-3); }

.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
.form-grid .span-2 { grid-column: span 2; }
@media (max-width: 560px) {
  .form-grid { grid-template-columns: 1fr; }
  .form-grid .span-2 { grid-column: span 1; }
}
.required { margin-left: 6px; font-size: 10px; font-style: normal; font-weight: 400; color: var(--fe-accent); }

.corpus-card { border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 10px; }
.question-card { border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 12px; }
.skip-btn { flex-shrink: 0; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 3px 9px; font-size: 10px; font-weight: 700; color: var(--fe-ink-3); }
.skip-btn:hover { border-color: var(--fe-accent); color: var(--fe-accent); }
.choice-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel);
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--fe-ink);
  transition: border-color 140ms ease, background-color 140ms ease;
}
.choice-card:hover { border-color: var(--fe-border-strong); }
.choice-card.selected { border-color: var(--fe-accent); background: color-mix(in srgb, var(--fe-accent) 8%, var(--fe-panel)); }
.choice-key {
  display: grid;
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  place-items: center;
  border: 1.5px solid var(--fe-border-strong);
  border-radius: 5px;
  font-size: 10px;
  font-weight: 800;
  color: var(--fe-ink-3);
}
.choice-card.selected .choice-key { border-color: var(--fe-accent); color: var(--fe-accent); }

.richness-card {
  display: flex;
  gap: 9px;
  margin-top: 16px;
  border: 1px solid color-mix(in srgb, var(--fe-warn) 32%, var(--fe-panel));
  border-radius: var(--fe-radius);
  background: color-mix(in srgb, var(--fe-warn) 10%, var(--fe-panel));
  padding: 10px 12px;
  color: var(--fe-ink-2);
}
.generating-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 44px 0;
  color: var(--fe-ink-3);
  font-family: var(--font-serif);
  font-size: 14px;
  letter-spacing: .12em;
}
.quality-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.quality-badge { border-radius: 4px; padding: 3px 10px; font-size: 12px; font-weight: 800; }
.quality-badge.pass { border: 1px solid color-mix(in srgb, var(--fe-warn) 45%, var(--fe-panel)); background: color-mix(in srgb, var(--fe-accent) 6%, var(--fe-panel)); color: color-mix(in srgb, var(--fe-warn) 78%, var(--fe-ink)); }
.quality-badge.soul { border: 1px solid var(--fe-accent); background: var(--fe-accent); color: var(--fe-accent-ink); }
.quality-badge.flat { border: 1px solid var(--fe-border); background: var(--fe-panel-2); color: var(--fe-ink-3); }
.result-heading { margin-top: 16px; margin-bottom: 8px; font-size: 13px; font-weight: 800; }
.card-list { overflow: hidden; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); }
.card-list > div { display: grid; grid-template-columns: minmax(90px, .8fr) minmax(0, 1.6fr); gap: 10px; border-bottom: 1px solid color-mix(in srgb, var(--fe-border) 60%, var(--fe-panel)); padding: 8px 11px; font-size: 12px; }
.card-list > div:last-child { border-bottom: 0; }
.card-list dt { color: var(--fe-ink-3); }
.card-list dd { min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fe-ink); }
.persona-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 7px;
  margin-top: 14px;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel);
  padding: 8px 11px;
  font-size: 12px;
  font-weight: 700;
  color: var(--fe-ink-2);
}
.persona-block {
  max-height: 300px;
  overflow-y: auto;
  margin-top: 6px;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel-2);
  padding: 12px;
  font-family: "JetBrains Mono", "Consolas", monospace;
  font-size: 11px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--fe-ink-2);
}
.save-row { display: flex; gap: 8px; margin-top: 16px; }

.error-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  border: 1px solid color-mix(in srgb, var(--fe-danger) 28%, var(--fe-panel));
  border-radius: var(--fe-radius);
  background: color-mix(in srgb, var(--fe-danger) 6%, var(--fe-panel));
  padding: 10px 12px;
  font-size: 12px;
  color: var(--fe-danger);
}
.icon-mini { display: grid; width: 26px; height: 26px; place-items: center; border-radius: 5px; color: var(--fe-ink-3); }
.icon-mini:hover { background: var(--fe-panel-2); color: var(--fe-danger); }

.btn {
  display: inline-flex;
  height: 32px;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius);
  background: var(--fe-panel-2);
  padding: 0 12px;
  color: var(--fe-ink-2);
  font-size: 12px;
  font-weight: 700;
  transition: background-color 140ms ease, border-color 140ms ease, transform 100ms ease;
}
.btn:hover:not(:disabled) { border-color: var(--fe-border-strong); }
.btn:active:not(:disabled) { transform: scale(.97); }
.btn.primary { border-color: var(--fe-accent); background: var(--fe-accent); color: var(--fe-accent-ink); }
.btn.primary:hover:not(:disabled) { background: var(--fe-accent-strong); }
.btn.ghost { background: transparent; }
.btn:disabled { border-color: var(--fe-border); background: var(--fe-panel-3); color: var(--fe-border-strong); }

.designer-footer {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--fe-border);
  background: var(--fe-panel);
  padding: 10px 14px;
}
</style>
