<script setup lang="ts">
import { LoaderCircle, Smartphone, X } from 'lucide-vue-next'
import type { LanInfo } from '../api'

defineProps<{
  info: LanInfo | null
  loading: boolean
}>()

const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="lan-overlay" @click.self="emit('close')">
    <section class="lan-dialog" role="dialog" aria-modal="true" aria-labelledby="lan-title">
      <header class="lan-header">
        <Smartphone :size="15" class="text-(--fe-ok)" />
        <h2 id="lan-title">手机扫码远程使用</h2>
        <button class="lan-close" title="关闭" aria-label="关闭" @click="emit('close')"><X :size="15" /></button>
      </header>
      <div class="lan-body">
        <LoaderCircle v-if="loading" class="mx-auto animate-spin text-(--fe-ink-3)" :size="22" />
        <template v-else-if="info?.url">
          <img :src="`/api/lan-qrcode.png?port=${info.port}${info.session_id ? `&session_id=${encodeURIComponent(info.session_id)}` : ''}`" alt="局域网访问二维码" class="lan-qr" />
          <p class="lan-url">{{ info.url }}</p>
          <p class="lan-hint">{{ info.hint }}</p>
          <p v-if="!info.listening_lan" class="lan-error">当前服务只监听本机回环地址，手机无法访问：请去掉 --host 127.0.0.1（默认 0.0.0.0）后重启。</p>
        </template>
        <p v-else class="lan-empty">未检测到局域网地址：请确认电脑已连接 Wi-Fi / 路由器。</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.lan-overlay { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; background: rgb(0 0 0 / 40%); padding: 16px; }
.lan-dialog { width: min(100%, 320px); border: 1px solid var(--fe-border); border-radius: calc(var(--fe-radius) + 2px); background: var(--fe-panel); box-shadow: var(--fe-shadow-2); }
.lan-header { display: flex; align-items: center; gap: 8px; border-bottom: 1px solid var(--fe-border); padding: 11px 13px; }.lan-header h2 { margin: 0; font-size: 12px; font-weight: 700; }.lan-close { display: grid; width: 28px; height: 28px; place-items: center; margin-left: auto; border-radius: 50%; color: var(--fe-ink-3); }.lan-close:hover { background: var(--fe-panel-2); color: var(--fe-ink); }
.lan-body { padding: 14px; text-align: center; }.lan-qr { display: block; width: 176px; margin: 0 auto; border: 1px solid var(--fe-border); border-radius: var(--fe-radius); background: white; padding: 6px; }.lan-url { margin: 10px 0 0; overflow-wrap: anywhere; color: var(--fe-ink-2); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; font-weight: 700; }.lan-hint, .lan-empty { margin: 7px 0 0; color: var(--fe-ink-3); font-size: 11px; line-height: 1.55; }.lan-empty { padding: 16px 0; }.lan-error { margin: 7px 0 0; color: var(--fe-danger); font-size: 11px; line-height: 1.5; }
</style>
