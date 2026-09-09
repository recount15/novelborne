<script setup lang="ts">
import { KeyRound, ListChecks, LoaderCircle, PlugZap, X } from 'lucide-vue-next'

export interface ModelConfigForm {
  provider: string
  api_key: string
  model: string
  base_url: string
  thinking_mode: string
  thinking_param: string
  distill_enabled: boolean
}
export interface ProviderInfo { id: string; label: string; base_url: string; models: string[] }

defineProps<{
  open: boolean
  form: ModelConfigForm
  providers: ProviderInfo[]
  availableModels: string[]
  modelLocked: boolean
  enhanced: boolean
  fetchingModels: boolean
  testingConnection: boolean
  connectionResult: { ok: boolean; message: string } | null
}>()

const emit = defineEmits<{
  close: []
  'provider-changed': []
  'pull-models': []
  'test-connection': []
}>()
</script>

<template>
  <Transition name="pop">
    <div v-if="open" class="mc-overlay" @click.self="emit('close')">
      <section class="mc-dialog" role="dialog" aria-modal="true" aria-label="AI 配置（模型与参数）">
        <header class="mc-head">
          <KeyRound :size="15" class="text-(--fe-accent)" />
          <div class="min-w-0 flex-1">
            <h2>AI 配置</h2>
            <p>提供商、凭据与生成参数；开局后锁定，如需更换请先结束当前对局。</p>
          </div>
          <button class="mc-close" title="关闭" aria-label="关闭" @click="emit('close')"><X :size="16" /></button>
        </header>

        <div class="mc-body">
          <label class="block">
            <span class="label">提供商</span>
            <select v-model="form.provider" class="mc-field" :disabled="modelLocked" @change="emit('provider-changed')">
              <option v-for="item in providers" :key="item.id" :value="item.id">{{ item.label }}</option>
            </select>
          </label>
          <label class="block">
            <span class="label">API Key（留空则使用服务端环境变量）</span>
            <input v-model="form.api_key" type="password" name="fate-api-key" autocomplete="new-password" autocapitalize="off" spellcheck="false" class="mc-field" placeholder="仅保存在当前页面内存" :disabled="modelLocked" />
          </label>
          <label class="block">
            <span class="label flex items-center justify-between">
              <span>模型</span>
              <span class="hint">优先选带思考模式的模型</span>
            </span>
            <div class="flex gap-1.5">
              <select v-if="availableModels.length" v-model="form.model" class="mc-field flex-1" :disabled="modelLocked">
                <option v-for="item in availableModels" :key="item">{{ item }}</option>
              </select>
              <input v-else v-model="form.model" class="mc-field flex-1" :disabled="modelLocked" />
              <button type="button" class="mc-action" :disabled="fetchingModels" title="拉取模型列表" @click="emit('pull-models')">
                <LoaderCircle v-if="fetchingModels" class="animate-spin" :size="12" />
                <ListChecks v-else :size="12" /> 拉取
              </button>
            </div>
          </label>
          <label class="block">
            <span class="label">接口地址</span>
            <div class="flex gap-1.5">
              <input v-model="form.base_url" class="mc-field flex-1" placeholder="自定义服务的 Base URL" :disabled="modelLocked" />
              <button type="button" class="mc-action" :disabled="testingConnection" title="测试连接" @click="emit('test-connection')">
                <LoaderCircle v-if="testingConnection" class="animate-spin" :size="12" />
                <PlugZap v-else :size="12" /> 测试
              </button>
            </div>
          </label>
          <Transition name="pop">
            <p v-if="connectionResult" class="mc-result" :class="connectionResult.ok ? 'ok' : 'fail'">{{ connectionResult.message }}</p>
          </Transition>
          <div class="grid grid-cols-2 gap-2">
            <label>
              <span class="label">思考模式</span>
              <select v-model="form.thinking_mode" class="mc-field" :disabled="modelLocked">
                <option value="auto">自动</option>
                <option value="on">开启</option>
                <option value="off">关闭</option>
              </select>
            </label>
            <label>
              <span class="label">思考参数</span>
              <input v-model="form.thinking_param" class="mc-field" placeholder="如 budget_tokens" :disabled="modelLocked" />
            </label>
          </div>
          <label class="flex cursor-pointer items-center gap-2 text-xs font-bold" :class="enhanced ? 'opacity-80' : ''">
            <input v-model="form.distill_enabled" type="checkbox" class="size-4 accent-(--fe-accent)" :disabled="modelLocked || enhanced" />
            启用锚点蒸馏{{ enhanced ? '（强化模式必需）' : '' }}
          </label>
        </div>

        <footer class="mc-foot">
          <button type="button" class="mc-done" @click="emit('close')">完成</button>
        </footer>
      </section>
    </div>
  </Transition>
</template>

<style scoped>
.mc-overlay {
  position: fixed; inset: 0; z-index: 60;
  background: color-mix(in srgb, var(--fe-ink) 24%, transparent);
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.mc-dialog {
  width: min(520px, 96vw); max-height: 90vh; display: flex; flex-direction: column;
  background: var(--fe-panel); border: 1px solid var(--fe-border); border-radius: 10px;
  box-shadow: 0 18px 48px color-mix(in srgb, var(--fe-ink) 18%, transparent);
}
.mc-head {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 12px 14px; border-bottom: 1px solid var(--fe-border);
}
.mc-head h2 { font-size: 13px; font-weight: 700; color: var(--fe-ink); }
.mc-head p { font-size: 10.5px; color: var(--fe-ink-3); margin-top: 2px; }
.mc-close {
  display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
  width: 26px; height: 26px; border-radius: 6px; color: var(--fe-ink-3);
  border: none; background: transparent; cursor: pointer;
}
.mc-close:hover { background: var(--fe-panel-2); color: var(--fe-ink); }
.mc-body { padding: 12px 14px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.label { display: block; font-size: 10.5px; font-weight: 700; color: var(--fe-ink-3); margin-bottom: 3px; }
.hint { font-size: 10px; font-weight: 400; color: var(--fe-ink-3); }
.mc-field {
  width: 100%; height: 34px; border-radius: 6px; font-size: 13px;
  border: 1px solid var(--fe-border); background: var(--fe-panel-2);
  color: var(--fe-ink); padding: 0 9px;
}
.mc-field:focus { outline: none; border-color: var(--fe-accent); }
.mc-action {
  display: inline-flex; align-items: center; justify-content: center; gap: 4px; flex-shrink: 0;
  height: 34px; padding: 0 10px; border-radius: 6px; font-size: 12px; font-weight: 700;
  border: 1px solid var(--fe-border); background: var(--fe-panel-2);
  color: var(--fe-ink-2); cursor: pointer;
}
.mc-action:hover:not(:disabled) { border-color: var(--fe-accent); color: var(--fe-accent); }
.mc-result { font-size: 11px; border-radius: 6px; padding: 5px 9px; }
.mc-result.ok {
  color: color-mix(in srgb, var(--fe-ok) 82%, var(--fe-ink));
  border: 1px solid color-mix(in srgb, var(--fe-ok) 30%, var(--fe-panel));
  background: color-mix(in srgb, var(--fe-ok) 8%, var(--fe-panel));
}
.mc-result.fail {
  color: var(--fe-danger);
  border: 1px solid color-mix(in srgb, var(--fe-danger) 28%, var(--fe-panel));
  background: color-mix(in srgb, var(--fe-danger) 6%, var(--fe-panel));
}
.mc-foot { padding: 10px 14px 12px; border-top: 1px solid var(--fe-border); display: flex; justify-content: flex-end; }
.mc-done {
  height: 32px; padding: 0 18px; border-radius: 6px; font-size: 12.5px; font-weight: 700;
  border: none; background: var(--fe-accent); color: var(--fe-accent-ink); cursor: pointer;
}
.mc-done:hover { background: var(--fe-accent-strong); }
</style>
