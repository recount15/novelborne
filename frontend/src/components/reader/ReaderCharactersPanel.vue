<script setup lang="ts">
import { CircleAlert, LoaderCircle, Users } from 'lucide-vue-next'

defineProps<{ characters: Array<{ name: string; detail?: string }>; loading?: boolean; currentChapterIndex: number }>()
</script>

<template>
  <div class="reader-insight-panel">
    <div class="drawer-head"><strong><Users :size="14" /> 本章活跃人物</strong><span>{{ characters.length }} 人</span></div>
    <div v-if="loading" class="character-empty"><LoaderCircle class="animate-spin" :size="22" /><p>正在读取人物信息</p></div>
    <div v-else-if="!characters.length" class="character-empty"><CircleAlert :size="22" /><p>本章暂无活跃人物</p><small>锚点蒸馏完成后会显示人物摘要</small></div>
    <div v-else class="character-list"><article v-for="character in characters" :key="character.name" class="character-item"><strong>{{ character.name }}</strong><p v-if="character.detail">{{ character.detail }}</p></article></div>
  </div>
</template>

<style scoped>
.reader-insight-panel{display:flex;flex-direction:column;min-height:0}.drawer-head{display:flex;min-height:42px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--fe-border);padding:0 14px;font-size:12px}.drawer-head strong{display:inline-flex;align-items:center;gap:5px}.drawer-head span{color:var(--fe-ink-3);font-size:10px}.character-empty{display:grid;min-height:40vh;place-content:center;justify-items:center;gap:8px;color:var(--fe-ink-3);padding:20px}.character-empty p{margin:0;font-size:13px;font-weight:600}.character-empty small{font-size:10px;line-height:1.6;text-align:center}.character-list{display:flex;flex-direction:column;gap:6px;padding:12px}.character-item{border:1px solid var(--fe-border);border-radius:9px;padding:10px;background:color-mix(in srgb,var(--fe-accent-soft) 28%,transparent)}.character-item strong{color:var(--fe-ink);font-size:12px}.character-item p{margin:4px 0 0;color:var(--fe-ink-2);font-size:11px;line-height:1.55}
</style>
