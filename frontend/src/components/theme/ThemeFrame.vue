<script setup lang="ts">
withDefaults(defineProps<{
  eyebrow?: string
  title?: string
  compact?: boolean
  variant?: 'plain' | 'quiet' | 'elevated'
}>(), { variant: 'plain' })
</script>

<template>
  <section class="theme-frame" :class="[`theme-frame--${variant}`, { 'theme-frame--compact': compact }]">
    <header v-if="eyebrow || title" class="theme-frame__heading">
      <small v-if="eyebrow">{{ eyebrow }}</small>
      <h3 v-if="title">{{ title }}</h3>
    </header>
    <div class="theme-frame__body"><slot /></div>
  </section>
</template>

<style scoped>
.theme-frame { min-width: 0; padding: 20px; color: var(--fe-ink); background: transparent; border: 0; box-shadow: none; overflow-wrap: anywhere; }
.theme-frame--quiet { border-radius: var(--fe-radius); background: var(--fe-panel-2); }
.theme-frame--elevated { border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: var(--fe-panel); box-shadow: var(--fe-shadow-2); }
.theme-frame__heading { margin-bottom: 16px; }
.theme-frame__heading small { display: block; margin-bottom: 4px; color: var(--fe-ink-3); font-family: var(--fe-font-sans); font-size: 12px; line-height: 1.5; }
.theme-frame__heading h3 { margin: 0; color: var(--fe-ink); font-family: var(--fe-font-serif); font-size: 20px; font-weight: 650; line-height: 1.5; }
.theme-frame__body { min-width: 0; }
.theme-frame--compact { padding: 12px; }
.theme-frame--compact .theme-frame__heading { margin-bottom: 12px; }
</style>
