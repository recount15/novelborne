<script setup lang="ts">
defineProps<{ value: number; label?: string }>()
</script>
<template><div class="theme-progress"><div class="theme-progress__meta"><span>{{ label || '进度' }}</span><strong>{{ Math.round(Math.max(0, Math.min(1, value)) * 100) }}%</strong></div><div class="theme-progress__track"><span :style="{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }"></span></div></div></template>
<style scoped>
.theme-progress { color: var(--fe-ink-2); font-size: 10px; }.theme-progress__meta { display: flex; justify-content: space-between; margin-bottom: 5px; }.theme-progress strong { color: var(--fe-accent); }.theme-progress__track { height: 6px; overflow: hidden; border: 1px solid var(--fe-border); border-radius: 999px; background: var(--fe-panel-3); }.theme-progress__track span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--fe-accent), var(--fe-accent-strong)); transition: width var(--fe-motion, 180ms) ease; }
</style>
