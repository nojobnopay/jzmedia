<template>
  <section :id="active ? 'sec-tvorganize' : undefined" class="card-block">
    <p class="lead">检查本库新增分集，自动补全资料，并汇总目录与文件名的整理预览。确认后执行；整理历史可用于撤销。</p>
    <div class="bar">
      <JzButton class="tool-action" variant="primary" icon="organize" :loading="ingestBusy" :disabled="!!busy2 || !library.enabled" @click="check">检查资料与整理</JzButton>
      <JzButton icon="history" :disabled="ingestBusy || !!busy2" @click="openPending">查看待办<span v-if="pendingCount">（{{ pendingCount }}）</span></JzButton>
      <label class="pending-choice"><input type="checkbox" v-model="includeIgnored" :disabled="ingestBusy" /> 显示已忽略</label>
    </div>
    <p v-if="ingestMessage" class="hint" role="status">{{ ingestMessage }}</p>
    <p v-if="pendingError" class="hint" role="status">{{ pendingError }}</p>
    <details class="undo" :open="!!restoreMsg">
      <summary>整理历史 / 撤销（{{ batches.length }} 个批次）</summary>
      <p class="hint">
        每次执行都会记录每一步，可在这里整批撤销。选批次 → 预览撤销 → 执行；
        源文件缺失或原路径被占用时会跳过并提示。
      </p>
      <div class="bar">
        <select v-model="batchId">
          <option value="">（最近一次批次）</option>
          <option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">
            {{ fmtBatch(b) }}
          </option>
        </select>
      </div>
      <div class="chips">
        <JzButton v-for="k in KIND_OPTS" :key="k.value" type="button" class="chip toggle"
          :class="{ on: kinds[k.value] }" :aria-pressed="!!kinds[k.value]" @click="kinds[k.value] = !kinds[k.value]">{{ k.label }}</JzButton>
        <JzButton type="button" class="mini" @click="toggleAllKinds" size="compact">{{ allKinds ? '全不选' : '全选' }}</JzButton>
      </div>
      <div class="bar">
        <JzButton @click="restore(true)" :disabled="!!busy2 || !selectedKinds.length" type="button" icon="eye">
          {{ busy2 === 'plan' ? '预览中…' : '预览撤销' }}
        </JzButton>
        <JzButton icon="undo" @click="restore(false)" :disabled="!!busy2 || !selectedKinds.length"
          :class="{ danger: armUndo }" :variant="armUndo ? 'danger' : 'secondary'" type="button">
          {{ busy2 === 'exec' ? `撤销中 ${done2}/${total2}…` : (armUndo ? '确认执行撤销' : '执行撤销') }}
        </JzButton>
        <JzButton v-if="busy2" @click="cancelRestore" type="button">取消</JzButton>
        <span class="dim">{{ restoreMsg }}</span>
      </div>
      <p v-if="armUndo" class="hint warn-text">将按记录把上述文件反向搬回原路径（不改文件名）。再点一次执行。</p>
      <table v-if="restorePlans.length" class="org-table">
        <thead>
          <tr><th>剧</th><th>还原</th><th>冲突</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in restorePlans" :key="p.show_id">
            <td>{{ p.title }}</td>
            <td>{{ (p.moves || []).length }}</td>
            <td>{{ (p.conflicts || []).length }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>
<script setup>
import JzButton from './JzButton.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { useTvIngestFlow } from '../useTvIngestFlow.js'
const props = defineProps({ library: { type: Object, required: true }, active: { type: Boolean, default: false } })
const emit = defineEmits(['changed', 'status'])
const includeIgnored = ref(false), pendingCount = ref(0), pendingError = ref('')
let disposed = false, pendingGeneration = 0
const { busy: ingestBusy, message: ingestMessage, start, pending, flow } = useTvIngestFlow({
  isCurrent: () => props.active && !disposed,
  scopeKey: () => `${props.library.id}|${props.active}`,
  onCompleted: () => { emit('changed'); reload() },
})
async function check() { await start({ library_id: Number(props.library.id) }) }
async function openPending() { await pending({ library_id: Number(props.library.id) }, { includeIgnored: includeIgnored.value }) }
async function reload() {
  if (!props.active || disposed) return
  const seq = ++pendingGeneration, library = props.library.id
  try {
    const rows = await flow.reviews({ library_id: library }, includeIgnored.value, true)
    if (disposed || seq !== pendingGeneration || library !== props.library.id || !props.active) return
    pendingCount.value = rows.length
    pendingError.value = ''
    emit('status', { plans: rows.filter(row => row.state !== 'ignored').length })
    await loadHistory()
  } catch (error) { if (!disposed && seq === pendingGeneration) pendingError.value = '待办暂不可用，可点击“查看待办”重试：' + error.message }
}
const KIND_OPTS = [
  { value: 'extra', label: '花絮' },
  { value: 'episode', label: '剧集' },
  { value: 'file', label: '附属文件' },
  { value: 'dir', label: '目录' },
  { value: 'rmdir', label: '空目录' },
]
const batches = ref([])
const batchId = ref('')
const kinds = ref({ extra: true, episode: true, file: true, dir: true, rmdir: true })
const allKinds = computed(() => KIND_OPTS.every((k) => kinds.value[k.value]))
const busy2 = ref(null)
const restoreMsg = ref('')
const restorePlans = ref([])
const done2 = ref(0)
const total2 = ref(0)
const armUndo = ref(false)
let job2 = ''
let timer2 = null

const selectedKinds = computed(() => Object.entries(kinds.value)
  .filter(([, v]) => v).map(([k]) => k))

function toggleAllKinds() {
  const want = !allKinds.value
  for (const k of KIND_OPTS) kinds.value[k.value] = want
}

function fmtBatch(b) {
  const t = b.updated_at ? new Date(b.updated_at * 1000).toLocaleString() : ''
  const undone = Number(b.undone || 0)
  return `${b.batch_id} · ${b.total} 条${undone ? `（已撤销 ${undone}）` : ''}${t ? ' · ' + t : ''}`
}

async function loadHistory() {
  try {
    const d = await api('/api/jobs/tv-organize/history?limit=30')
    batches.value = d.batches || []
  } catch (e) { /* 忽略 */ }
}

async function restore(dryRun) {
  if (!selectedKinds.value.length) return
  if (!dryRun && !armUndo.value) {
    armUndo.value = true
    restoreMsg.value = ''
    return
  }
  armUndo.value = false
  busy2.value = dryRun ? 'plan' : 'exec'
  restoreMsg.value = ''
  restorePlans.value = []
  done2.value = 0
  total2.value = 0
  try {
    const d = await api('/api/jobs/tv-organize-restore', {
      method: 'POST',
      body: JSON.stringify({
        batch_id: batchId.value || null,
        library_id: props.library.id,
        kinds: selectedKinds.value,
        dry_run: dryRun,
      }),
    })
    job2 = d.job_id
    clearInterval(timer2)
    timer2 = setInterval(pollRestore, 1200)
  } catch (e) {
    restoreMsg.value = '启动失败：' + e.message
    busy2.value = null
  }
}

async function pollRestore() {
  try {
    const j = await api('/api/jobs/tv-organize-restore/' + job2)
    done2.value = j.done || 0
    total2.value = j.total || 0
    if (j.state === 'done') {
      clearInterval(timer2); timer2 = null; busy2.value = null
      const s = j.summary || {}
      restorePlans.value = s.plans || []
      if (s.dry_run) {
        restoreMsg.value = `批次 ${s.batch_id || '（最近）'}：可还原 ${s.total || 0} 条，`
          + `冲突 ${s.conflicts || 0}`
      } else {
        restoreMsg.value = `完成：还原 ${s.restored || 0}，跳过 ${s.skipped || 0}`
          + (s.failed ? `，失败 ${s.failed}` : '')
        emit('changed')
        loadHistory()
      }
    } else if (j.state === 'failed' || j.state === 'cancelled') {
      clearInterval(timer2); timer2 = null; busy2.value = null
      restoreMsg.value = j.error || j.state
    }
  } catch (e) { /* 下一轮 */ }
}

async function cancelRestore() {
  if (!job2) return
  try { await api(`/api/jobs/tv-organize-restore/${job2}/cancel`, { method: 'POST' }) } catch (e) { /* 忽略 */ }
}


onMounted(() => { reload(); window.addEventListener('jzmedia:content-changed', reload) })
watch([() => props.active, () => props.library.id, includeIgnored], reload)
onUnmounted(() => { disposed = true; pendingGeneration++; if (timer2) clearInterval(timer2); window.removeEventListener('jzmedia:content-changed', reload) })
</script>
<style scoped>
.lead, .hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.6; }
.bar, .chips { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.pending-choice { display: flex; align-items: center; gap: 8px; min-height: 44px; }
.chip.toggle.on { border-color: var(--jz-accent); color: var(--jz-accent); }
.undo { border-top: 1px solid var(--jz-border); margin-top: 20px; padding-top: 12px; }
.undo summary { cursor: pointer; min-height: 44px; line-height: 44px; }
.org-table { width: 100%; border-collapse: collapse; font-size: var(--jz-font-s); }
.org-table th, .org-table td { padding: 8px; text-align: left; border-bottom: 1px solid var(--jz-border); overflow-wrap: anywhere; }
.warn-text { color: var(--jz-warn); }
</style>
