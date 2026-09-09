import { computed, onMounted, ref, type ComputedRef } from 'vue'
import { fetchCharacterPool, type PoolCardEntry } from '../api'
type PoolSlotKey = '主角栏' | '伴侣栏' | '伙伴栏' | '宿敌栏'

export function useCharacterPools(currentWorkTitle: ComputedRef<string>) {
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
  { slot: '伴侣栏', label: '伴侣栏', note: '先选来源，再选类型；人格卡不限性别；身体策略单独配置' },
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

// v2.0.3 跨书防线：默认只显示当前作品的角色卡（书名取自作品库上传文件或
// 基础模式书名输入），避免其他作品的人物/设定乱入。匹配容忍《》与扩展名
// 差异；无出处卡（原创/通用）不受限。用户可主动关闭过滤做跨书选择。
const poolWorkFilterEnabled = ref(true)
function poolWorkMatches(card: PoolCardEntry): boolean {
  if (!poolWorkFilterEnabled.value || !currentWorkTitle.value) return true
  const cardWork = String(card.work || '').replace(/[《》\s]/g, '')
  if (!cardWork) return true
  return cardWork === currentWorkTitle.value
    || cardWork.includes(currentWorkTitle.value)
    || currentWorkTitle.value.includes(cardWork)
}

function filteredPoolGroups(slot: PoolSlotKey): Array<{ key: string; sub_groups: Array<{ key: string; cards: PoolCardEntry[] }> }> {
  const state = poolSlots.value[slot]
  const query = state.query.trim().toLowerCase()
  const match = (card: PoolCardEntry) =>
    (!query || card.name.toLowerCase().includes(query)) && poolWorkMatches(card)
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


return { loadAllPoolSlots, togglePoolCard, selectedPoolCards, poolSlots, poolCardById, duplicateNameWarnings, poolWorkFilterEnabled, filteredPoolGroups, poolCategoryOptions, poolSubtypeOptions, onPoolCategoryChanged, onPoolQueryInput, poolPreviewCard, poolPreviewText }
}
