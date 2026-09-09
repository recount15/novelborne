<script setup lang="ts">
import type { WishEffectRow } from '../types'
defineProps<{ busy: boolean; phase: string; draft: string; revision?: unknown; wishEffects?: WishEffectRow[] }>()
</script>

<template>
  <section class="generation-state" aria-label="权威状态与生成草稿">
    <div class="generation-state__overview">
      <span role="status" aria-live="polite" aria-atomic="true">{{ busy ? `生成中${phase ? ` · ${phase}` : ''}` : '以服务器已提交状态为准' }}</span>
      <small>正式状态版本 {{ revision ?? '尚未提交' }}</small>
    </div>
    <details v-if="wishEffects?.length" class="generation-state__details">
      <summary>已生效铁律（{{ wishEffects.length }}）</summary>
      <ul class="generation-state__effects">
        <li v-for="(effect, index) in wishEffects" :key="effect.directive_id ?? index">
          <small>#{{ effect.round ?? '?' }} · {{ effect.scope ?? '?' }}{{ effect.characters_touched?.length ? ` · ${effect.characters_touched.join('、')}` : '' }}</small>
          <p>{{ effect.fact }}</p>
        </li>
      </ul>
    </details>
    <details v-if="draft" class="generation-state__details generation-state__draft" open>
      <summary>生成草稿 <span>未提交，不影响人物、选项或存档</span></summary>
      <p class="generation-state__draft-text">{{ draft }}</p>
    </details>
  </section>
</template>

<style scoped>
.generation-state { min-width: 0; padding: 12px 16px; border-radius: var(--fe-radius); background: var(--fe-panel-2); color: var(--fe-ink-2); font-family: var(--fe-font-sans); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.generation-state__overview { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; }
.generation-state small { font-size: 12px; color: var(--fe-ink-3); }
.generation-state__details { margin-top: 8px; }
.generation-state summary { padding-block: 8px; cursor: pointer; color: var(--fe-ink); }
.generation-state summary::marker { color: var(--fe-ink-3); }
.generation-state__effects { display: grid; gap: 12px; margin: 8px 0 12px; padding-left: 20px; }
.generation-state__effects small { display: block; }
.generation-state__effects p { margin: 4px 0 0; }
.generation-state__draft summary { color: var(--fe-warn); }
.generation-state__draft summary span { margin-left: 8px; color: var(--fe-ink-2); font-size: 12px; }
.generation-state__draft-text { margin: 8px 0 4px; padding: 16px; border-radius: var(--fe-radius); background: var(--fe-panel); color: var(--fe-ink-2); font-size: 14px; line-height: 1.8; white-space: pre-wrap; }
@media (pointer: coarse) { .generation-state summary { min-height: var(--fe-touch-target, 44px); } }
</style>
