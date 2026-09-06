<script setup lang="ts">
defineProps<{ eyebrow?: string; title?: string; compact?: boolean }>()
</script>
<template>
  <section class="theme-frame" :class="{ 'theme-frame--compact': compact }">
    <div v-if="eyebrow || title" class="theme-frame__heading">
      <span class="theme-frame__ornament" aria-hidden="true">✦</span>
      <div><small v-if="eyebrow">{{ eyebrow }}</small><h3 v-if="title">{{ title }}</h3></div>
    </div>
    <div class="theme-frame__body"><slot /></div>
  </section>
</template>
<style scoped>
.theme-frame { position: relative; overflow: hidden; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: linear-gradient(135deg, color-mix(in srgb, var(--fe-panel-highlight, var(--fe-accent-soft)) 18%, var(--fe-panel)), var(--fe-panel)); box-shadow: var(--fe-shadow-1); }
.theme-frame::before { content: ''; position: absolute; inset: 0; pointer-events: none; opacity: .55; background: var(--fe-decor-overlay); }
.theme-frame__heading, .theme-frame__body { position: relative; z-index: 1; }
.theme-frame__heading { display: flex; align-items: center; gap: 9px; border-bottom: 1px solid var(--fe-border); padding: 10px 12px; }
.theme-frame__ornament { color: var(--fe-accent); font-size: 16px; }
.theme-frame__heading small { display: block; color: var(--fe-ink-3); font-size: 9px; letter-spacing: .12em; text-transform: uppercase; }
.theme-frame__heading h3 { margin: 2px 0 0; color: var(--fe-ink); font-size: 13px; font-weight: 800; }
.theme-frame__body { padding: 12px; }
.theme-frame--compact .theme-frame__body { padding: 8px; }
</style>
