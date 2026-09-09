<script setup lang="ts">
import type { LocatorCandidate, LocateSelectResult, PreparationPackage } from '../types'
defineProps<{
  preparation: PreparationPackage | null
  candidates: LocatorCandidate[]
  selectedId: string
  selection: LocateSelectResult | null
  timepoint: string
  busy: boolean
  stage: '' | 'prepare' | 'locate' | 'select'
  locked: boolean
  error: string
}>()
defineEmits<{
  prepare: []
  locate: []
  select: [id: string]
  timepoint: [value: string]
}>()
const labels = { before: '事件之前', during: '事件之中', after: '事件之后' }
</script>

<template>
  <section class="mt-3 rounded border border-(--fe-border) p-2 text-xs" aria-label="开局证据定位">
    <strong>原著准备与开局证据</strong>
    <p class="mt-1 text-(--fe-ink-3)">先验证原著，再选择服务器检索出的证据。自由描述不等于已确认的开局事实。</p>
    <button type="button" class="field mt-2 h-9 p-2" :disabled="busy || locked" @click="$emit('prepare')">{{ stage === 'prepare' ? '正在验证' : '验证原著准备' }}</button>
    <div v-if="preparation" class="mt-2" role="status">
      <p>{{ preparation.mode === 'fullbook' ? '全书' : '窗口' }}覆盖 {{ preparation.coverage.verified_blocks }}/{{ preparation.coverage.expected_blocks }} · {{ preparation.ready ? '准备就绪' : '尚不可开局' }}</p>
      <p class="break-all text-[10px] text-(--fe-ink-3)">准备编号 {{ preparation.preparation_id }}</p>
      <p v-for="(item, index) in preparation.errors" :key="index" class="text-(--fe-warn)">{{ typeof item === 'string' ? item : `${item.block_id || ''} ${item.error}` }}</p>
    </div>
    <p v-if="error" role="alert" class="mt-2 text-(--fe-warn)">{{ error }}</p>
    <p v-if="selection" role="status" class="mt-2 text-(--fe-ink-3)">
      已确认时点：{{ labels[selection.timepoint] }} · 知识截止 第 {{ selection.knowledge_cutoff.chapter_no }} 章
    </p>
    <button type="button" class="field mt-2 h-9 p-2" :disabled="busy || locked || !preparation?.ready" @click="$emit('locate')">{{ stage === 'locate' ? '正在检索' : '检索开局证据' }}</button>
    <label v-for="candidate in candidates" :key="candidate.id" class="mt-2 block rounded border p-2 transition-colors" :class="selectedId === candidate.id ? 'border-(--fe-accent)' : 'border-(--fe-border)'">
      <input type="radio" class="accent-(--fe-accent)" name="opening-evidence" :value="candidate.id" :checked="selectedId === candidate.id" :disabled="locked || busy" @change="$emit('select', candidate.id)" />
      第 {{ candidate.chapter_no }} 章 · {{ candidate.start }}–{{ candidate.end }}
      <blockquote class="mt-1 border-l-2 border-(--fe-border-strong) pl-2 whitespace-pre-wrap text-(--fe-ink-2)">{{ candidate.excerpt_display }}</blockquote>
      <p v-if="candidate.excerpt_total > candidate.excerpt_display.length" class="text-[10px] text-(--fe-ink-3)">证据共 {{ candidate.excerpt_total }} 字，已按服务端上限截断显示</p>
      <select v-if="selectedId === candidate.id" aria-label="相对于证据的开局时间" class="field mt-2 h-9 p-1" :value="timepoint" :disabled="locked || busy" @change="$emit('timepoint', ($event.target as HTMLSelectElement).value)">
        <option v-for="point in candidate.timepoints" :key="point" :value="point">{{ labels[point] }}</option>
      </select>
    </label>
  </section>
</template>
