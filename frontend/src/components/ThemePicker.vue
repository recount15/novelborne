<script setup lang="ts">
import type { ThemeId } from '../themeSwitch'

type ThemeMeta = Record<ThemeId, { label: string; swatch: string }>

const props = defineProps<{
  themes: ThemeMeta
  modelValue: ThemeId
  compact?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: ThemeId]
}>()
</script>

<template>
  <div class="theme-picker" :class="{ compact }" role="radiogroup" aria-label="主题选择">
    <button
      v-for="(meta, id) in props.themes"
      :key="id"
      type="button"
      class="theme-choice"
      :class="{ active: props.modelValue === id }"
      :aria-label="meta.label"
      :aria-pressed="props.modelValue === id"
      :title="meta.label"
      @click="emit('update:modelValue', id)"
    >
      <span class="theme-swatch" :style="{ background: meta.swatch }" />
      <span class="theme-label">{{ meta.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.theme-picker { display: flex; max-width: min(48vw, 560px); align-items: center; gap: 6px; overflow-x: auto; }
.theme-choice { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); padding: 3px 6px; color: var(--fe-ink-2); font-size: 10px; white-space: nowrap; transition: border-color var(--fe-motion) ease, background-color var(--fe-motion) ease, color var(--fe-motion) ease; }
.theme-choice:hover, .theme-choice.active { border-color: var(--fe-accent); color: var(--fe-accent); }
.theme-choice.active { background: var(--fe-accent-soft); color: color-mix(in srgb, var(--fe-accent) 78%, var(--fe-ink)); font-weight: 700; }
.theme-swatch { width: 14px; height: 14px; flex: 0 0 auto; border: 1px solid color-mix(in srgb, var(--fe-ink) 20%, transparent); border-radius: 999px; }
@media (max-width: 640px) { .theme-picker:not(.compact) { display: none; } }
</style>
