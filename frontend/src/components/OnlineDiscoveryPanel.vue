<template>
  <section class="card-block online-source-probes" aria-label="视频源探测结果">
    <JzField id="online-discovery-route" class="online-connection-choice" label="本次候选目录出口"
      hint="切换出口会重新读取社区名单并开始探测。批量探测逐个来源进行，目录和视频使用各来源的网络设置。">
      <template #default="field"><select :id="field.id" v-model="directoryRoute" :aria-describedby="field.describedby" :disabled="!!enabling" @change="start">
        <option value="default">服务默认（{{ defaultSummary }}）</option>
        <option value="direct">直连</option>
        <option v-for="profile in proxyProfiles" :key="profile.id" :value="profile.id">代理 · {{ profile.name }}</option>
      </select></template>
    </JzField>
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
      <p class="hint">目录耗时为读取首页的时间。媒体取样包括下载视频样本，HLS 还需先读取播放清单。1 倍表示下载速度刚好跟得上该片段播放。</p>
      <p v-if="feedback" class="online-source-feedback" :class="{ 'is-error': saveError }" :role="saveError ? 'alert' : 'status'">{{ feedback }}</p>
      <div class="online-source-list">
        <article v-for="item in ranked" :key="item.id" class="online-source-row" :data-discovery-candidate="item.id">
          <div>
            <h4>{{ item.name }}</h4>
            <p>{{ item.origin }} · {{ hostName(item.api_base_url) }}</p>
            <p>{{ resultLabel(item.id) }}</p>
            <p v-if="reports[item.id]">本次探测出口 · {{ routeLabel(reports[item.id].tested_route) }}</p>
            <p v-if="sampleSummaries[item.id]">{{ sampleSummaries[item.id] }}</p>
            <p v-if="needsHostConfirmation(reports[item.id])">试读还需要访问 {{ reports[item.id].missing_hosts.join('、') }}</p>
            <JzField :id="'online-probe-route-' + item.id" class="online-connection-choice" label="复测出口">
              <template #default="field"><select :id="field.id" v-model="probeRoutes[item.id]" :disabled="running || !!enabling" @change="discardApproval(item.id)">
                <option value="default">当前来源设置</option>
                <option value="direct">直连</option>
                <option v-for="profile in proxyProfiles" :key="profile.id" :value="profile.id">代理 · {{ profile.name }}</option>
              </select></template>
              <template #actions>
                <JzButton v-if="needsHostConfirmation(reports[item.id])" :disabled="running || !!enabling" @click="recheck(item, true)">{{ canExtend(reports[item.id]) ? '允许本次试读并延长复测' : '允许本次试读并复测' }}</JzButton>
                <JzButton v-else-if="!running" :disabled="!!enabling" @click="recheck(item)">{{ canExtend(selectedReport(item.id)) ? '延长取样并复测' : '复测此源' }}</JzButton>
              </template>
            </JzField>
            <p v-if="selectedReport(item.id)?.tested_route && reports[item.id].status !== 'measured'">此出口尚未读到视频样片，成功取样后可保存。</p>
            <p v-if="canEnable(item) && reports[item.id].tested_route">保存后，目录和媒体均使用{{ routeLabel(reports[item.id].tested_route) }}。</p>
            <p v-if="canEnable(item) && reports[item.id].allowed_hosts?.length">保存后允许访问 {{ reports[item.id].allowed_hosts.join('、') }}</p>
            <label v-for="host in canEnable(item) ? reports[item.id].poster_hosts : []" :key="host" class="online-source-check">
              <input type="checkbox" :checked="posterSelection[item.id]?.includes(host) || false"
                :disabled="!!enabling || rowBusy(item.id)" @change="selectPoster(item.id, host, $event.target.checked)" />
              保存时允许海报主机 {{ host }}
            </label>
          </div>
          <div v-if="item.enabled && item.source_id" class="online-discovery-choice">
            <span class="hint">已启用</span>
            <JzButton v-if="canEnable(item) && (reports[item.id].tested_route || pendingHosts(item))" :loading="enabling === item.id" :disabled="!!enabling || rowBusy(item.id)"
              @click="enable(item)">{{ reports[item.id].tested_route ? (pendingHosts(item) ? '保存出口和主机' : '保存实测出口') : '保存已选主机' }}</JzButton>
            <RouterLink :to="{ path: '/online', query: { source: item.source_id } }" :aria-label="'使用此源 ' + item.name">使用此源</RouterLink>
          </div>
          <JzButton v-else :loading="enabling === item.id" :disabled="!!enabling || !canEnable(item)"
            :aria-label="(canEnable(item) && reports[item.id].tested_route ? '启用并保存实测出口 ' : '启用来源 ') + item.name" @click="enable(item)">{{ canEnable(item) && reports[item.id].tested_route ? '启用并保存实测出口' : '启用来源' }}</JzButton>
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
import JzField from './JzField.vue'
import Spinner from './Spinner.vue'

const props = defineProps({
  open: { type: Boolean, default: true },
  proxyProfiles: { type: Array, required: true },
  defaultSummary: { type: String, required: true },
})
const emit = defineEmits(['enabled', 'saving'])
const candidates = ref([]), reports = ref({}), activeIds = ref(new Set())
const queuedIds = ref(new Set()), posterSelection = ref({})
const loading = ref(true), running = ref(false), directoryError = ref(''), directoryStatus = ref('')
const directoryRoute = ref('default')
const probeRoutes = ref({})
const enabling = ref(''), feedback = ref(''), saveError = ref(false)
let scan = null, write = null

const order = { measured: 0, catalog_only: 1, empty: 3, failed: 4 }
const ranked = computed(() => [...candidates.value].sort((first, second) => {
  const a = reports.value[first.id], b = reports.value[second.id]
  const status = (order[a?.status] ?? 2) - (order[b?.status] ?? 2)
  if (status || a?.status !== 'measured') return status
  return playbackGroup(a) - playbackGroup(b)
    || (b.realtime_ratio ?? 0) - (a.realtime_ratio ?? 0)
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
  online_discovery_probe_expired: '探测结果已失效，请重新探测后再确认主机',
  online_source_changed: '来源或网络配置已改变，请重新探测',
  provider_timeout: '探测超时',
  response_byte_limit: '媒体响应超过读取上限',
  manifest_limit: '播放清单超过读取上限',
  invalid_range_response: '服务器返回的分段范围不兼容',
  target_dns_failed: '域名解析失败',
  upstream_http_error: '上游服务返回 HTTP 错误',
  unsupported_manifest: '播放清单格式不受支持',
  http_401: '上游要求认证（HTTP 401）',
  http_403: '上游拒绝访问（HTTP 403）',
  http_404: '上游资源不存在（HTTP 404）',
  http_429: '上游请求过多（HTTP 429）',
  media_host_limit: '样片引用的主机数量超出限制',
}
const stages = { entry: '播放地址', manifest: '播放清单', media: '视频数据' }
const attemptReasons = {
  provider_timeout: '读取超时', manifest_limit: '大小或条目数超限',
  response_byte_limit: '响应超限', unsupported_manifest: '格式不受支持',
}
const sampleSummaries = computed(() => {
  const summaries = {}
  for (const [id, report] of Object.entries(reports.value)) {
    const counts = new Map()
    for (const attempt of report.attempts || []) {
      if (attempt.reason === 'ok') continue
      const detail = attemptReasons[attempt.reason] || reasons[attempt.reason] || '读取失败'
      const label = (stages[attempt.stage] || '试读') + ' · ' + detail
      counts.set(label, (counts.get(label) || 0) + 1)
    }
    if (counts.size) {
      const failures = [...counts].map(([label, count]) => label + '（' + count + ' 次）')
      summaries[id] = '已试 ' + report.attempts.length + ' 个样片，' + failures.join('，')
    }
  }
  return summaries
})
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
function playbackGroup(report) {
  if (report.realtime_ratio == null) return 1
  return belowPlaybackRate(report) ? 2 : 0
}
function canExtend(report) {
  return report && (belowPlaybackRate(report) || report.reason === 'provider_timeout'
    || report.attempts?.some(attempt => attempt.reason === 'provider_timeout'))
}
function routeChoice(route) {
  return route?.profile_id || route?.mode || 'default'
}
function routeLabel(route) {
  if (!route) return '当前来源设置'
  if (route.mode === 'direct') return '直连'
  const profile = props.proxyProfiles.find(profile => profile.id === route.profile_id)
  return profile ? '代理 · ' + profile.name : '代理（档案已移除）'
}
function selectedReport(id) {
  const report = reports.value[id]
  return report && routeChoice(report.tested_route) === probeRoutes.value[id] ? report : null
}
function discardApproval(id) {
  if (reports.value[id]) reports.value[id] = { ...reports.value[id], probe_token: null }
  delete posterSelection.value[id]
}
function needsHostConfirmation(report) {
  return !!(report?.probe_token && report.missing_hosts?.length)
}
function resultLabel(id) {
  const report = reports.value[id]
  if (queuedIds.value.has(id)) return '排队中，等待探测名额'
  if (activeIds.value.has(id)) return '正在检测目录和媒体…'
  if (!report) return running.value ? '等待探测' : '未测，可重试'
  if (report.status === 'empty') return '空目录 · 暂未列出影片，可稍后复测'
  if (report.status === 'failed') return reasons[report.reason] || '探测失败'
  const catalog = report.catalog_ms != null ? '目录读取 ' + Math.round(report.catalog_ms) + ' ms' : '目录可读'
  if (report.status === 'catalog_only') return '仅目录可读 · ' + catalog + (sampleSummaries.value[id] ? '' : ' · ' + (reasons[report.reason] || '尚未验证媒体'))
  const timing = report.media_ms != null ? ' · 媒体取样 ' + Math.round(report.media_ms) + ' ms' : ' · 已取样'
  const speed = report.sample_kbps > 0 ? ' · ' + (report.sample_kbps / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' Mbps' : ' · 样本不足以估算速度'
  const relative = report.realtime_ratio == null ? '' : ' · 约为播放所需的 ' + report.realtime_ratio + ' 倍'
  if (belowPlaybackRate(report)) return catalog + timing + speed + ' · 取样速度低于该片段播放所需（' + report.realtime_ratio + ' 倍）'
  const recommendation = report.sample_kbps > 0 && ranked.value[0]?.id === id
    ? (running.value ? '当前优先试用 · ' : '本轮优先试用 · ') : ''
  return recommendation + catalog + timing + speed + relative
}
function canEnable(item) {
  const report = selectedReport(item.id)
  return (report?.status === 'measured' || (report?.status === 'catalog_only' && !report.tested_route))
    && !!report.probe_token && !rowBusy(item.id)
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
        const result = await api('/api/online/discovery/' + encodeURIComponent(item.source_id || id) + '/probe',
          { method: 'POST', body: JSON.stringify(body), signal: current.signal, timeout: body.extended ? 28000 : 20000 })
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
          reports.value[id] = { status: 'failed', reason: error.code, tested_route: body.probe_route }
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
  const report = selectedReport(item.id)
  const body = confirm ? { probe_token: report.probe_token, hosts: report.missing_hosts } : {}
  const choice = probeRoutes.value[item.id]
  if (choice !== 'default') body.probe_route = choice === 'direct' ? { mode: 'direct' } : { mode: 'proxy', profile_id: choice }
  if (canExtend(report)) body.extended = true
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
    const choice = directoryRoute.value
    const body = choice === 'default' ? {} : { directory_route: choice === 'direct'
      ? { mode: 'direct' } : { mode: 'proxy', profile_id: choice } }
    const response = await api('/api/online/discovery/refresh', {
      method: 'POST', body: JSON.stringify(body), signal: current.signal, timeout: 12000,
    })
    if (current.signal.aborted) return
    candidates.value = response.items
    probeRoutes.value = Object.fromEntries(response.items.map(item => [item.id, 'default']))
    directoryStatus.value = response.directory_status
    loading.value = false
    running.value = !!candidates.value.length
    queuedIds.value = new Set(response.items.map(item => item.id))
    for (const item of response.items) {
      if (current.signal.aborted || !await sample(item, current)) break
    }
  } catch (error) {
    if (current.signal.aborted) return
    if (error.status === 401 || error.status === 403 || error.code === 'online_source_disabled') {
      directoryError.value = '请确认在线点播已启用并重新验证管理权限。'
    } else if (error.code === 'online_source_changed') {
      directoryError.value = '候选目录的网络配置已改变，请选择其他出口或刷新设置页。'
    } else {
      directoryError.value = '候选目录暂时无法读取，请重试。'
    }
  } finally {
    if (scan === current) { loading.value = false; running.value = false; queuedIds.value.clear() }
  }
}
async function enable(item) {
  const report = selectedReport(item.id)
  if (enabling.value || !canEnable(item) || (item.enabled && !report.tested_route && !pendingHosts(item))) return
  enabling.value = item.id
  emit('saving', true)
  feedback.value = ''
  saveError.value = false
  write = new AbortController()
  const signal = write.signal
  try {
    const body = { probe_token: report.probe_token }
    if (report.tested_route) body.apply_route = true
    if (posterSelection.value[item.id]?.length) body.poster_hosts = posterSelection.value[item.id]
    const source = await api('/api/online/discovery/' + encodeURIComponent(item.source_id || item.id) + '/enable',
      { method: 'POST', body: JSON.stringify(body), signal })
    if (signal.aborted) return
    const wasEnabled = item.enabled
    reports.value[item.id] = { ...reports.value[item.id], allowed_hosts: [], missing_hosts: [], poster_hosts: [], probe_token: null }
    delete posterSelection.value[item.id]
    item.enabled = source.enabled
    item.source_id = source.id
    emit('enabled', source)
    feedback.value = report.tested_route ? '已为「' + source.name + '」保存目录和媒体出口。'
      : wasEnabled ? '已保存「' + source.name + '」的主机。' : '已启用「' + source.name + '」。'
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
