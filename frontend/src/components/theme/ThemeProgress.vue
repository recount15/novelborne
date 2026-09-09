<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  /** Legacy callers supply a measured ratio in [0, 1]. */
  value?: number
  label?: string
  mode?: 'measured' | 'indeterminate'
  completed?: number
  total?: number
  unit?: string
  phase?: string
}>(), { label: '进度', mode: 'measured', unit: '' })

const measurement = computed(() => {
  if (props.mode === 'indeterminate') return null
  // An explicitly supplied count pair takes precedence over the legacy ratio.
  if (props.completed !== undefined || props.total !== undefined) {
    if (!Number.isFinite(props.completed) || !Number.isFinite(props.total)
      || props.total! <= 0 || props.completed! < 0 || props.completed! > props.total!) return null
    return { ratio: props.completed! / props.total!, counts: true }
  }
  if (!Number.isFinite(props.value) || props.value! < 0 || props.value! > 1) return null
  return { ratio: props.value!, counts: false }
})
// Never round a still-incomplete task up to a displayed 100%.
const percent = computed(() => measurement.value ? Math.floor(measurement.value.ratio * 100) : undefined)
const amount = computed(() => measurement.value?.counts
  ? `${props.completed} / ${props.total}${props.unit ? ` ${props.unit}` : ''}`
  : percent.value === undefined ? '' : `${percent.value}%`)
const activityText = computed(() => props.phase || '总量未知')
</script>

<template>
  <div class="theme-progress" :class="{ 'theme-progress--indeterminate': !measurement }">
    <div class="theme-progress__meta">
      <span>{{ label }}<span v-if="measurement && phase" class="theme-progress__phase"> · {{ phase }}</span></span>
      <span v-if="measurement" class="theme-progress__amount">{{ amount }}</span>
      <span v-else>{{ activityText }}</span>
    </div>
    <div class="theme-progress__track" role="progressbar" :aria-label="label"
      :aria-valuemin="measurement ? 0 : undefined" :aria-valuemax="measurement ? 100 : undefined"
      :aria-valuenow="measurement ? measurement.ratio * 100 : undefined"
      :aria-valuetext="measurement ? [phase, amount].filter(Boolean).join(' · ') : activityText">
      <span :style="measurement ? { width: `${measurement.ratio * 100}%` } : undefined"></span>
    </div>
  </div>
</template>

<style scoped>
.theme-progress { min-width: 0; color: var(--fe-ink-2); font-family: var(--fe-font-sans); font-size: 12px; line-height: 1.5; }
.theme-progress__meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; margin-bottom: 8px; overflow-wrap: anywhere; }
.theme-progress__amount { color: var(--fe-ink); font-variant-numeric: tabular-nums; }
.theme-progress__phase { color: var(--fe-ink-3); }
.theme-progress__track { height: 4px; overflow: hidden; border-radius: 4px; background: var(--fe-panel-3); }
.theme-progress__track > span { display: block; height: 100%; border-radius: inherit; background: var(--fe-accent); transition: width var(--fe-motion, 140ms) ease; }
.theme-progress--indeterminate .theme-progress__track > span { width: 28%; animation: progress-activity 1.8s ease-in-out infinite alternate; }
@keyframes progress-activity { from { transform: translateX(0); } to { transform: translateX(257%); } }
@media (prefers-reduced-motion: reduce) {
  .theme-progress__track > span { transition: none; }
  .theme-progress--indeterminate .theme-progress__track > span { animation: none; transform: none; }
}
@media (forced-colors: active) {
  .theme-progress__track { outline: 1px solid CanvasText; }
  .theme-progress__track > span { background: Highlight; forced-color-adjust: none; }
}
</style>
