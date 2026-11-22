<template>
  <section class="card-block online-source-probes" aria-label="视频源探测结果">
    <p class="hint">从运行 jzmedia 的服务器测试候选来源。媒体取样速度反映本次网络状况，晚高峰可重新探测。</p>
    <EmptyState v-if="loading" state="loading" title="正在发现视频源" text="正在读取候选来源。" />
    <EmptyState v-else-if="directoryError" state="error" title="无法读取候选来源" :text="directoryError" retry @retry="start" />
    <EmptyState v-else-if="!candidates.length" state="empty" title="暂无候选来源" text="可以稍后重新探测，或返回手动添加来源。">
      <JzButton icon="refresh" @click="start">重新探测</JzButton>
    </EmptyState>
    <template v-else>
      <p v-if="directoryStatus === 'cached' || directoryStatus === 'fallback'" class="hint" role="status">
        {{ directoryStatus === 'cached' ? '本轮使用最近保存的候选目录。' : '社区目录暂不可用，本轮探测内置候选和已配置来源。' }}
      </p>
      <div class="section-heading">
        <p class="online-loading-label" role="status"><Spinner v-if="running" :size="16" />{{ progress }}</p>
        <JzButton v-if="running" @click="stop">停止探测</JzButton>
        <JzButton v-else icon="refresh" :disabled="!!enabling" @click="start">重新探测</JzButton>
      </div>
      <p v-if="feedback" class="online-source-feedback" :class="{ 'is-error': saveError }" :role="saveError ? 'alert' : 'status'">{{ feedback }}</p>
      <div class="online-source-list">
        <article v-for="item in ranked" :key="item.id" class="online-source-row" :data-discovery-candidate="item.id">
          <div>
            <h4>{{ item.name }}</h4>
            <p>{{ item.origin }} · {{ hostName(item.api_base_url) }}</p>
            <p>{{ resultLabel(item.id) }}</p>
            <p v-if="reports[item.id]?.allowed_hosts?.length">保存后允许访问 {{ reports[item.id].allowed_hosts.join('、') }}</p>
            <template v-if="reports[item.id]?.missing_hosts?.length">
              <p>试读还需要访问 {{ reports[item.id].missing_hosts.join('、') }}</p>
              <JzButton :disabled="running || rowBusy(item.id) || !!enabling" @click="recheck(item, true)">允许本次试读并复测</JzButton>
            </template>
            <label v-for="host in reports[item.id]?.poster_hosts" :key="host" class="online-source-check">
              <input type="checkbox" :checked="posterSelection[item.id]?.includes(host) || false"
                :disabled="!!enabling || rowBusy(item.id)" @change="selectPoster(item.id, host, $event.target.checked)" />
              保存时允许海报主机 {{ host }}
            </label>
            <JzButton v-if="!running && !reports[item.id]?.missing_hosts?.length && reports[item.id]?.status !== 'measured'"
              :disabled="rowBusy(item.id) || !!enabling" @click="recheck(item)">复测此源</JzButton>
          </div>
          <div v-if="item.enabled && item.source_id" class="online-discovery-choice">
            <span class="hint">已启用</span>
            <JzButton v-if="pendingHosts(item)" :loading="enabling === item.id" :disabled="!!enabling || rowBusy(item.id)"
              @click="enable(item)">保存已选主机</JzButton>
            <RouterLink :to="{ path: '/online', query: { source: item.source_id } }" :aria-label="'使用此源 ' + item.name">使用此源</RouterLink>
          </div>
          <JzButton v-else :loading="enabling === item.id" :disabled="!!enabling || !canEnable(item)"
            :aria-label="'启用来源 ' + item.name" @click="enable(item)">启用来源</JzButton>
        </article>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import EmptyState from './EmptyState.vue'
import JzButton from './JzButton.vue'
import Spinner from './Spinner.vue'

const props = defineProps({ open: { type: Boolean, default: true } })
const emit = defineEmits(['enabled', 'saving'])
const candidates = ref([]), reports = ref({}), activeIds = ref(new Set())
const queuedIds = ref(new Set()), posterSelection = ref({})
const loading = ref(true), running = ref(false), directoryError = ref(''), directoryStatus = ref('')
const enabling = ref(''), feedback = ref(''), saveError = ref(false)
let scan = null, write = null

const order = { measured: 0, catalog_only: 1, empty: 3, failed: 4 }
const ranked = computed(() => [...candidates.value].sort((first, second) => {
  const a = reports.value[first.id], b = reports.value[second.id]
  const status = (order[a?.status] ?? 2) - (order[b?.status] ?? 2)
  if (status || a?.status !== 'measured') return status
  return Number(belowPlaybackRate(a)) - Number(belowPlaybackRate(b))
    || (b.sample_kbps ?? -1) - (a.sample_kbps ?? -1)
    || (a.media_ms ?? Infinity) - (b.media_ms ?? Infinity)
}))
const reasons = {
  no_media_sample: '目录中未找到可取样的视频',
  target_host_not_allowed: '媒体主机尚未获准访问',
  unsupported_media: '取样不支持此媒体格式',
  invalid_media_sample: '媒体样本无法识别',
  media_unavailable: '媒体暂时不可达',
  catalog_unavailable: '目录暂时不可达',
  provider_timeout: '探测超时',
  response_byte_limit: '完整视频分片超过播放网关限制',
  media_host_limit: '样片引用的主机数量超出限制',
}
const progress = computed(() => {
  const count = Object.keys(reports.value).length, total = candidates.value.length
  const state = running.value ? '正在探测' : count === total ? '探测完成' : '探测已停止'
  const pending = running.value ? queuedIds.value.size : total - count
  return state + ' · ' + count + ' / ' + total + (pending ? ' · ' + (running.value ? '排队 ' : '未测 ') + pending : '')
})
function hostName(url) {
  try { return new URL(url).hostname } catch { return '地址待核对' }
}
function belowPlaybackRate(report) {
  return report.realtime_ratio != null && report.realtime_ratio < 1
}
function resultLabel(id) {
  const report = reports.value[id]
  if (queuedIds.value.has(id)) return '排队中，等待探测名额'
  if (activeIds.value.has(id)) return '正在检测目录和媒体…'
  if (!report) return running.value ? '等待探测' : '未测，可重试'
  if (report.status === 'empty') return '空目录 · 暂未列出影片，可稍后复测'
  if (report.status === 'failed') return reasons[report.reason] || '探测失败'
  const catalog = report.catalog_ms != null ? '目录 ' + Math.round(report.catalog_ms) + ' ms' : '目录可读'
  if (report.status === 'catalog_only') return '仅目录可读 · ' + catalog + ' · ' + (reasons[report.reason] || '尚未验证媒体')
  const timing = report.media_ms != null ? ' · 媒体取样 ' + Math.round(report.media_ms) + ' ms' : ' · 已取样'
  const speed = report.sample_kbps > 0 ? ' · ' + (report.sample_kbps / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' Mbps' : ' · 样本不足以估算速度'
  if (belowPlaybackRate(report)) return catalog + timing + speed + ' · 取样速度低于该片段播放所需'
  const recommendation = report.sample_kbps > 0 && ranked.value[0]?.id === id
    ? (running.value ? '当前优先试用 · ' : '本轮优先试用 · ') : ''
  return recommendation + catalog + timing + speed
}
function canEnable(item) {
  const report = reports.value[item.id]
  return (report?.status === 'measured' || report?.status === 'catalog_only') && !!report.probe_token && !rowBusy(item.id)
}
function rowBusy(id) { return activeIds.value.has(id) || queuedIds.value.has(id) }
function selectPoster(id, host, selected) {
  const hosts = new Set(posterSelection.value[id])
  if (selected) hosts.add(host)
  else hosts.delete(host)
  posterSelection.value[id] = [...hosts]
}
function pendingHosts(item) {
  return canEnable(item) && (reports.value[item.id]?.allowed_hosts?.length || posterSelection.value[item.id]?.length)
}
function stop() {
  scan?.abort()
  scan = null
  running.value = false
  activeIds.value.clear()
  queuedIds.value.clear()
}
function delay(signal, milliseconds) {
  if (signal.aborted) return Promise.resolve()
  return new Promise(resolve => {
    const timer = setTimeout(done, milliseconds)
    function done() {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    signal.addEventListener('abort', done, { once: true })
  })
}
async function sample(item, current, body = {}) {
  let waitUntil = 0, attempts = 0
  const id = item.id
  try {
    while (!current.signal.aborted) {
      queuedIds.value.delete(id)
      activeIds.value.add(id)
      try {
        const result = await api('/api/online/discovery/' + encodeURIComponent(id) + '/probe',
          { method: 'POST', body: JSON.stringify(body), signal: current.signal, timeout: 20000 })
        if (!current.signal.aborted) {
          reports.value[id] = result
          delete posterSelection.value[id]
        }
        return true
      } catch (error) {
        if (current.signal.aborted) return false
        if (error.status === 401 || error.status === 403) {
          directoryError.value = '请重新验证管理权限，再读取候选来源。'
          stop()
          return false
        }
        if (error.status !== 429 || error.code !== 'provider_catalog_busy') {
          reports.value[id] = { status: 'failed', reason: error.code }
          return true
        }
        if (!waitUntil) waitUntil = Date.now() + 20000
        const remaining = waitUntil - Date.now()
        if (remaining <= 0) { delete reports.value[id]; return false }
        activeIds.value.delete(id)
        queuedIds.value.add(id)
        const backoff = Math.max(error.retryAfterSeconds || 1, Math.min(4, 2 ** attempts++))
        await delay(current.signal, Math.min(remaining, backoff * 1000))
        if (!current.signal.aborted && Date.now() >= waitUntil) { delete reports.value[id]; return false }
      }
    }
    return false
  } finally {
    if (!current.signal.aborted) { queuedIds.value.delete(id); activeIds.value.delete(id) }
  }
}
async function recheck(item, confirm = false) {
  if (running.value || enabling.value || rowBusy(item.id)) return
  const current = scan ??= new AbortController()
  const report = reports.value[item.id]
  const body = confirm ? { probe_token: report.probe_token, hosts: report.missing_hosts } : {}
  delete reports.value[item.id]
  running.value = true
  try { await sample(item, current, body) }
  finally { if (scan === current) running.value = false }
}
async function start() {
  if (enabling.value) return
  stop()
  const current = new AbortController()
  scan = current
  loading.value = true
  directoryError.value = ''
  directoryStatus.value = ''
  reports.value = {}
  posterSelection.value = {}
  feedback.value = ''
  try {
    const response = await api('/api/online/discovery/refresh', {
      method: 'POST', body: '{}', signal: current.signal, timeout: 12000,
    })
    if (current.signal.aborted) return
    candidates.value = response.items
    directoryStatus.value = response.directory_status
    loading.value = false
    running.value = !!candidates.value.length
    const queue = [...candidates.value]
    queuedIds.value = new Set(queue.map(item => item.id))
    async function worker() {
      while (!current.signal.aborted && queue.length) {
        if (!await sample(queue.shift(), current)) break
      }
    }
    await Promise.all([worker(), worker()])
  } catch (error) {
    if (!current.signal.aborted) directoryError.value = error.status === 401 || error.status === 403
      ? '请确认在线点播已启用并重新验证管理权限。' : '候选目录暂时无法读取，请重试。'
  } finally {
    if (scan === current) { loading.value = false; running.value = false; queuedIds.value.clear() }
  }
}
async function enable(item) {
  if (enabling.value || !canEnable(item) || (item.enabled && !pendingHosts(item))) return
  enabling.value = item.id
  emit('saving', true)
  feedback.value = ''
  saveError.value = false
  write = new AbortController()
  const signal = write.signal
  try {
    const body = { probe_token: reports.value[item.id].probe_token }
    if (posterSelection.value[item.id]?.length) body.poster_hosts = posterSelection.value[item.id]
    const source = await api('/api/online/discovery/' + encodeURIComponent(item.id) + '/enable',
      { method: 'POST', body: JSON.stringify(body), signal })
    if (signal.aborted) return
    const wasEnabled = item.enabled
    reports.value[item.id] = { ...reports.value[item.id], allowed_hosts: [], missing_hosts: [], poster_hosts: [], probe_token: null }
    delete posterSelection.value[item.id]
    item.enabled = source.enabled
    item.source_id = source.id
    emit('enabled', source)
    feedback.value = wasEnabled ? '已保存「' + source.name + '」的主机。' : '已启用「' + source.name + '」。'
  } catch (error) {
    if (signal.aborted) return
    saveError.value = true
    if (['online_discovery_probe_expired', 'online_source_changed'].includes(error.code)) {
      reports.value[item.id] = { status: 'failed', reason: 'online_source_changed' }
      feedback.value = error.code === 'online_source_changed' ? '来源或网络配置已改变，请重新探测后启用。' : '探测结果已失效，请重新探测后启用。'
    } else {
      feedback.value = error.status === 409 ? '来源配置有冲突，请返回视频源列表核对已有配置。'
        : '无法确认来源是否已启用，请返回列表核对后重试。'
    }
  } finally {
    if (!signal.aborted) { enabling.value = ''; write = null; emit('saving', false) }
  }
}
onMounted(start)
watch(() => props.open, open => { if (!open) stop() })
onBeforeUnmount(() => { stop(); write?.abort() })
</script>
