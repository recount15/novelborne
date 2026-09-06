<script setup lang="ts">
defineProps<{ label: string; tone?: 'accent' | 'ok' | 'warn' | 'danger' }>()
</script>
<template><span class="theme-badge" :class="`theme-badge--${tone || 'accent'}`"><i aria-hidden="true"></i>{{ label }}</span></template>
<style scoped>
.theme-badge { display: inline-flex; align-items: center; gap: 5px; border: 1px solid color-mix(in srgb, var(--fe-accent) 42%, var(--fe-border)); border-radius: 999px; padding: 3px 8px; color: var(--fe-accent); background: color-mix(in srgb, var(--fe-accent-soft) 52%, var(--fe-panel)); font-size: 10px; font-weight: 750; }
.theme-badge i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 14%, transparent); }
.theme-badge--ok { color: var(--fe-ok); }.theme-badge--warn { color: var(--fe-warn); }.theme-badge--danger { color: var(--fe-danger); }
</style>
