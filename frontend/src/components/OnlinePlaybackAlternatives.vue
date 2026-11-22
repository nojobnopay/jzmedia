<template>
  <div v-if="supported !== false && (phase !== 'idle' || failed)" class="online-alternative" @click.stop @dblclick.stop>
    <span role="status">{{ message }}</span>
    <div v-for="item in choices" :key="item.recommendation_token" class="online-alternative-choice">
      <JzButton size="compact" variant="primary" :loading="selected === item.recommendation_token" :disabled="!!selected" @click="choose(item)">
        切换到 {{ item.source_name }} · {{ item.line_label }}
      </JzButton>
      <span v-if="item.throughput_kbps > 0">抽测 {{ (item.throughput_kbps / 1000).toFixed(1) }} Mbps</span>
    </div>
    <JzButton v-if="selected" size="compact" @click="cancelChoice">取消切换</JzButton>
    <JzButton v-if="!choices.length && !checking && !selected" size="compact" @click="probe">
      {{ phase === 'idle' ? '查找备用线路' : '重新探测' }}
    </JzButton>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import JzButton from './JzButton.vue'

const props = defineProps({
  identity: { type: Object, required: true },
  waiting: Boolean,
  failed: Boolean,
  switchSource: { type: Function, required: true },
})
const tasks = '/api/online/playback/alternative-tasks'
const supported = ref(null), phase = ref('idle'), choices = ref([]), selected = ref('')
const checking = computed(() => ['probing', 'queued'].includes(phase.value))
const message = computed(() => {
  if (selected.value) return '正在核对节目并准备所选线路…'
  if (choices.value.length) return '已读到备用线路的视频数据。切换后使用该线路自己的观看进度。'
  if (phase.value === 'queued') return '正在等待探测名额…'
  if (phase.value === 'probing') return '正在探测同一节目的备用线路…'
  if (phase.value === 'empty') return '暂未找到可推荐的同版本线路，可在设置中探测更多视频源。'
  if (phase.value === 'partial') return '部分线路未能完成探测，暂时没有推荐。'
  if (phase.value === 'changed') return '所选线路已变化或推荐已过期，请重新探测。'
  if (phase.value === 'busy') return '播放会话已满，稍后可再试。'
  if (phase.value === 'prepare-error') return '暂时无法准备所选线路，仍保留原播放。'
  if (phase.value === 'error') return '备用线路探测未完成。'
  if (phase.value === 'cooldown') return '刚刚已探测过备用线路。'
  return ''
})
let timer, advanceTimer, request, taskId, selection, lastProbe = -Infinity
function releaseTask(id) {
  if (/^alt_[0-9a-f]{32}$/.test(id || '')) {
    void api(tasks + '/' + id, { method: 'DELETE', timeout: 5000, keepalive: true }).catch(() => {})
  }
}
function stopProbe() {
  clearTimeout(timer)
  clearTimeout(advanceTimer)
  request?.abort()
  request = null
  releaseTask(taskId)
  taskId = null
}
function clear() {
  stopProbe()
  selection?.abort()
  choices.value = []
  selected.value = ''
  phase.value = 'idle'
}
async function probe() {
  if (checking.value || selected.value || supported.value === false) return
  stopProbe()
  choices.value = []
  phase.value = 'probing'
  lastProbe = Date.now()
  const pending = request = new AbortController()
  const signal = pending.signal
  const started = Date.now()
  function schedule(first, delay) {
    if (Date.now() - started >= 45000) {
      phase.value = choices.value.length ? 'ready' : 'partial'
      stopProbe()
    } else advanceTimer = setTimeout(() => { if (!signal.aborted) void step(first) }, delay)
  }
  async function step(first) {
    try {
      if (supported.value === null) {
        const info = await api('/api/stream/client-info', { signal, timeout: 5000 })
        if (signal.aborted) return
        supported.value = info.features?.includes('online_alternatives_v2') === true
        if (!supported.value) { phase.value = 'idle'; return }
      }
      const path = first ? tasks : tasks + '/' + taskId + '/advance'
      const result = await api(path, { method: 'POST', signal, timeout: 30000,
        body: JSON.stringify(first ? props.identity : {}) })
      if (signal.aborted) { if (first) releaseTask(result.task_id); return }
      if (!/^alt_[0-9a-f]{32}$/.test(result.task_id) || (!first && taskId !== result.task_id)
          || !['queued', 'running', 'partial', 'done'].includes(result.status)) throw new Error()
      taskId = result.task_id
      // Keep each visible button bound to its original result while later samples arrive.
      const best = result.items[0]
      if (best && /^[A-Za-z0-9_-]{32,128}$/.test(best.recommendation_token)
          && !choices.value.some(row => row.recommendation_token === best.recommendation_token)) choices.value.push(best)
      if (result.status === 'done' || result.status === 'partial') {
        phase.value = choices.value.length ? 'ready' : result.status === 'partial' ? 'partial' : 'empty'
        stopProbe()
        return
      }
      phase.value = result.status === 'queued' ? 'queued' : 'probing'
      const wait = Math.max(result.status === 'queued' ? 150 : 0, Math.min(Number(result.retry_after_ms) || 0, 5000))
      schedule(false, wait)
    } catch (error) {
      if (signal.aborted) return
      if (error?.status === 429) {
        phase.value = 'queued'
        schedule(first, Math.min(45000, Math.max(1, error.retryAfterSeconds || 1) * 1000))
      } else {
        if ([401, 403, 409].includes(error?.status)) choices.value = []
        phase.value = choices.value.length ? 'ready' : 'error'
        stopProbe()
      }
    }
  }
  await step(true)
}
function cancelChoice() {
  selection?.abort()
  if (!props.waiting && !props.failed) clear()
  else phase.value = 'ready'
}
async function choose(item) {
  if (selected.value) return
  stopProbe()
  selected.value = item.recommendation_token
  const pending = selection = new AbortController()
  try { await props.switchSource(item, pending.signal) }
  catch (error) {
    if (!pending.signal.aborted) {
      choices.value = []
      if (['online_recommendation_changed', 'online_recommendation_expired'].includes(error?.code)) phase.value = 'changed'
      else phase.value = error?.status === 429 ? 'busy' : 'prepare-error'
    }
  } finally {
    if (selection === pending) {
      selection = null
      selected.value = ''
      if (!props.waiting && !props.failed) clear()
    }
  }
}
watch(() => [props.waiting, props.failed], ([waiting, failed]) => {
  clearTimeout(timer)
  if (selected.value) return
  if (!waiting && !failed) { clear(); return }
  if (waiting && phase.value === 'idle' && supported.value !== false) {
    const delay = Math.max(5000, 60000 - (Date.now() - lastProbe))
    if (delay > 5000) phase.value = 'cooldown'
    timer = setTimeout(() => { void probe() }, delay)
  }
}, { immediate: true })
onMounted(() => globalThis.window?.addEventListener('pagehide', clear))
onBeforeUnmount(() => {
  globalThis.window?.removeEventListener('pagehide', clear)
  clear()
})
</script>
