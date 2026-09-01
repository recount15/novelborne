<script setup lang="ts">
import { Anchor, CircleAlert, LoaderCircle } from 'lucide-vue-next'
import type { UserBookAnchor } from '../../types'

defineProps<{ anchor: UserBookAnchor | null; loading?: boolean; currentChapterIndex: number }>()
</script>

<template>
  <div class="reader-insight-panel">
    <div class="drawer-head"><strong><Anchor :size="14" /> 本章锚点</strong><span>第 {{ currentChapterIndex }} 章</span></div>
    <div v-if="loading" class="anchor-empty"><LoaderCircle class="animate-spin" :size="22" /><p>正在读取剧情锚点</p></div>
    <div v-else-if="!anchor" class="anchor-empty"><CircleAlert :size="22" /><p>本章暂无剧情锚点</p><small>锚点蒸馏完成后会显示在这里</small></div>
    <div v-else class="insight-content">
      <h3>{{ anchor.title || `第 ${currentChapterIndex} 章` }}</h3>
      <p v-if="anchor.summary" class="insight-summary">{{ anchor.summary }}</p>
      <section v-if="anchor.events?.length"><strong>关键事件</strong><ul><li v-for="event in anchor.events" :key="event">{{ event }}</li></ul></section>
      <section v-if="anchor.world"><strong>世界变化</strong><p>{{ anchor.world }}</p></section>
      <section v-if="anchor.foreshadowing?.length"><strong>伏笔</strong><ul><li v-for="item in anchor.foreshadowing" :key="item">{{ item }}</li></ul></section>
      <section v-if="anchor.ripple"><strong>涟漪</strong><p>{{ anchor.ripple }}</p></section>
    </div>
  </div>
</template>

<style scoped>
.reader-insight-panel{display:flex;flex-direction:column;min-height:0}.drawer-head{display:flex;min-height:42px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--fe-border);padding:0 14px;font-size:12px}.drawer-head strong{display:inline-flex;align-items:center;gap:5px}.drawer-head span{color:var(--fe-ink-3);font-size:10px}.anchor-empty{display:grid;min-height:40vh;place-content:center;justify-items:center;gap:8px;color:var(--fe-ink-3);padding:20px}.anchor-empty p{margin:0;font-size:13px;font-weight:600}.anchor-empty small{font-size:10px;line-height:1.6;text-align:center}.insight-content{padding:14px;color:var(--fe-ink-2);font-size:11px;line-height:1.65}.insight-content h3{margin:0 0 10px;color:var(--fe-ink);font-size:14px}.insight-summary{margin:0 0 14px}.insight-content section{margin:13px 0}.insight-content section>strong{color:var(--fe-accent-strong);font-size:11px}.insight-content p{margin:4px 0}.insight-content ul{margin:4px 0;padding-left:18px}
</style>
