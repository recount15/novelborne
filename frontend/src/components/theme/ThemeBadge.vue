<script setup lang="ts">
withDefaults(defineProps<{ label: string; tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' }>(), { tone: 'accent' })
</script>

<template>
  <span class="theme-badge" :class="`theme-badge--${tone}`">{{ label }}</span>
</template>

<style scoped>
.theme-badge { --badge-tone: var(--fe-accent); display: inline-block; max-width: 100%; border: 0; border-radius: 4px; padding: 3px 8px; color: var(--badge-tone); background: color-mix(in srgb, var(--badge-tone) 8%, var(--fe-panel)); font-family: var(--fe-font-sans); font-size: 12px; font-weight: 600; line-height: 1.5; vertical-align: middle; overflow-wrap: anywhere; }
.theme-badge--neutral { --badge-tone: var(--fe-ink-2); }
.theme-badge--ok { --badge-tone: var(--fe-ok); }
.theme-badge--warn { --badge-tone: var(--fe-warn); }
.theme-badge--danger { --badge-tone: var(--fe-danger); }
</style>
