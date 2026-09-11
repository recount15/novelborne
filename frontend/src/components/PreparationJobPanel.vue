<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { cancelPreparationJob, getPreparationJob, resumePreparationJob, type ModelCredentials, type PreparationJob } from '../v3Api'
const props = defineProps<{ job: PreparationJob | null; credentials: ModelCredentials; label: string }>()
const emit = defineEmits<{ update: [job: PreparationJob]; ready: [] }>()
const error = ref('')
const acting = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
const terminal = (status: string) => ['READY', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(status)
const active = computed(() => !!props.job && !terminal(props.job.status))
const stages: Record<string, string> = { QUEUED: '排队中', INDEXING: '建立索引', DISTILLING: '提炼原著证据', RESOLVING_IDENTITIES: '核对身份', EXTRACTING_CHARACTERS: '提取人物', VALIDATING: '复验结果', CANCEL_REQUESTED: '正在安全取消', CANCELLED: '已取消', INTERRUPTED: '任务中断', FAILED: '准备失败', READY: '证据准备就绪' }
const progress = computed(() => {
  // 全书蒸馏阶段优先展示章级进度：一章的全部块验证完成才计入，绝不虚报。
  const job = props.job
  if (job && job.stage === 'DISTILLING' && job.chapters_total && job.chapters_total > 0) {
    return Math.max(0, Math.min(100, Math.round((job.chapters_done ?? 0) / job.chapters_total * 100)))
  }
  return job && job.total_units !== null && Number.isFinite(job.total_units) && job.total_units > 0 && Number.isFinite(job.completed_units)
    ? Math.max(0, Math.min(100, Math.round(job.completed_units / job.total_units * 100))) : null
})
const chapterLine = computed(() => {
  const job = props.job
  if (!job?.chapters_total || job.chapters_total <= 0) return null
  const done = job.chapters_done ?? 0
  const percent = Math.min(100, Math.round(done / (job.chapters_total || 1) * 100))
  const distillingChapter = job.stage === 'DISTILLING' && job.current_chapter != null && job.current_chapter > done
  return `章节进度 · 第 ${done} / ${job.chapters_total} 章 · ${percent}%` +
    (distillingChapter ? ` · 正在蒸馏第 ${job.current_chapter} 章` : '')
})
const refreshing = ref(false)
let generation = 0
async function refresh() {
  if (refreshing.value || acting.value) return
  const request = generation
  clearTimeout(timer)
  const id = props.job?.job_id
  if (!id || disposed) return
  refreshing.value = true
  try {
    const job = await getPreparationJob(id)
    if (disposed || request !== generation || id !== props.job?.job_id || job.sequence < props.job.sequence) return
    error.value = ''
    const becameReady = job.status === 'READY' && props.job.status !== 'READY'
    emit('update', job)
    if (becameReady) emit('ready')
  } catch (cause) { if (!disposed && request === generation) error.value = cause instanceof Error ? cause.message : '任务状态获取失败' }
  finally { if (request === generation) { refreshing.value = false; if (!disposed && active.value) { clearTimeout(timer); timer = setTimeout(refresh, 1800) } } }
}
watch(() => props.job?.job_id, () => { ++generation; refreshing.value = false; acting.value = false; clearTimeout(timer); error.value = ''; if (active.value) timer = setTimeout(refresh, 800) }, { immediate: true })
async function act(action: 'cancel' | 'resume') {
  if (!props.job || acting.value) return
  const id = props.job.job_id
  const request = ++generation
  refreshing.value = false
  acting.value = true
  error.value = ''
  clearTimeout(timer)
  try {
    const job = action === 'cancel' ? await cancelPreparationJob(id) : await resumePreparationJob(id, props.credentials)
    if (disposed || request !== generation || id !== props.job?.job_id || job.sequence < props.job.sequence) return
    const becameReady = job.status === 'READY' && props.job.status !== 'READY'
    emit('update', job)
    if (becameReady) emit('ready')
  } catch (cause) { if (!disposed && request === generation) error.value = cause instanceof Error ? cause.message : '操作失败' }
  finally { if (request === generation) { acting.value = false; if (!disposed) timer = setTimeout(refresh, 800) } }
}
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>

<template>
  <section v-if="job" class="preparation-job" aria-label="原著准备任务" aria-live="polite">
    <header><strong>{{ label }}</strong><span>{{ stages[job.status] || job.status }}</span></header>
    <p>阶段 · {{ stages[job.stage] || job.stage }}</p>
    <progress v-if="progress !== null" :value="progress" max="100" :aria-label="`当前阶段进度 ${progress}%`" />
    <p v-if="progress === null">{{ active ? '当前阶段总量尚未提供，等待服务器更新；不估算完成比例。' : '服务器未提供当前阶段完成比例。' }}</p>
    <p v-if="progress === 100 && job.status !== 'READY'">阶段计数已完成，任务尚未就绪，以服务器状态为准。</p>
    <p v-if="error">状态更新暂时失败，以下保留最后一次服务器状态。</p>
    <p v-if="chapterLine">{{ chapterLine }}</p>
    <p>{{ job.completed_units }} / {{ job.total_units ?? '待统计' }} {{ job.unit_kind }}<span v-if="progress !== null && job.stage !== 'DISTILLING'"> · {{ progress }}%</span></p>
    <p v-if="job.coverage">已复验证据 {{ job.coverage.verified_blocks ?? '未知' }} / {{ job.coverage.expected_blocks ?? '未知' }}</p>
    <p v-if="job.character_counts">已验证人物卡 · {{ job.character_counts.verified_cards ?? '未提供；不表示已完成' }}</p>
    <p v-if="job.status === 'READY'">{{ job.result_id ? `准备结果 ${job.result_id}` : '服务器已复验准备证据' }}。不会创建游戏或标记为已玩。</p>
    <p v-if="job.gap_report?.card_publication === 'not_implemented'">当前任务只准备原著证据，不包含完整人物卡发布。</p>
    <p v-if="job.needs_credentials">恢复任务需使用当前模型设置中的连接凭据；密钥不会保存。</p>
    <p v-if="job.error || error" class="job-error" role="alert">{{ error || job.error?.message || job.error?.code }}</p>
    <ul v-if="!error && job.error?.issues?.length" class="job-issues">
      <li v-for="(issue, index) in job.error.issues" :key="index">
        {{ issue.code }}<template v-if="issue.chapter_no != null"> · 第 {{ issue.chapter_no }} 章</template><template v-if="issue.field"> · {{ issue.field }}</template><template v-if="issue.message"> · {{ issue.message }}</template>
      </li>
    </ul>
    <details><summary>任务与来源</summary><p class="identifier">{{ job.job_id }}</p><p class="identifier">{{ job.source_hash }}</p></details>
    <footer>
      <button v-if="active" :disabled="acting || job.status === 'CANCEL_REQUESTED'" @click="act('cancel')">取消任务</button>
      <button v-if="['FAILED', 'CANCELLED', 'INTERRUPTED'].includes(job.status)" :disabled="acting" @click="act('resume')">使用当前凭据恢复</button>
      <button v-if="error" :disabled="acting || refreshing" @click="refresh()">重试状态查询</button>
    </footer>
  </section>
</template>

<style scoped>
.preparation-job { margin-top: 16px; padding: 16px; background: var(--fe-panel-2); border-radius: 10px; color: var(--fe-ink); font-size: 12px; line-height: 1.7; }
header, footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; }
header span, p, summary { color: var(--fe-ink-3); }
p { margin: 8px 0; }
progress { width: 100%; height: 7px; accent-color: var(--fe-accent); }
.identifier { overflow-wrap: anywhere; font-size: 10px; }
.job-error { color: var(--fe-danger, var(--fe-warn)); }
.job-issues { margin: 8px 0; padding-left: 18px; color: var(--fe-ink-3); }
.job-issues li { overflow-wrap: anywhere; }
button { padding: 6px 10px; background: var(--fe-panel); color: var(--fe-ink); border: 1px solid var(--fe-border); border-radius: 6px; }
button:disabled { opacity: .5; }
footer { justify-content: flex-start; margin-top: 12px; }
</style>
