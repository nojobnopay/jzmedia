<template>
  <section :id="active ? 'sec-meta' : undefined" class="card-block">
    <h3>剧集资料维护</h3>
    <p class="hint">以下操作仅作用于「{{ library.name }}」。</p>

    <div class="bar">
      <button @click="doTvScrape(false)" :disabled="!!busy">
        {{ busy === 'tv' ? `剧集刮削中 ${tvDone}/${tvTotal}…` : '补全缺失剧集资料' }}
      </button>
      <button @click="doTvScrape(true)" :disabled="!!busy">重新获取全部剧集资料</button>
      <button v-if="busy === 'tv'" @click="cancelTv">取消</button>
      <span>{{ tvMsg }}</span>
    </div>
    <details class="settings-details"><summary>资料更新会影响哪些内容</summary><p class="hint">获取剧、季、集资料与海报，更新应用内资料。未匹配剧集和不一致的集号可在详情页核对。</p></details>

    <div class="bar">
      <button @click="doTvNfo(false)" :disabled="!!busy">
        {{ busy === 'tvnfo' ? `写 NFO 中 ${tvNfoDone}/${tvNfoTotal}…` : '重写剧集 NFO 与海报' }}
      </button>
      <button @click="doTvNfo(true)" :disabled="!!busy">预览写入清单</button>
      <button v-if="busy === 'tvnfo'" @click="cancelTvNfo">取消</button>
      <span>{{ tvNfoMsg }}</span>
    </div>
    <details class="settings-details"><summary>NFO 与海报写入规则</summary><p class="hint">向媒体目录写入剧集与季 NFO，是否写海报取决于视频库设置。远程库默认不写逐集 NFO；此操作不移动或重命名视频。</p></details>
    <PreviewMaintenance :library-id="library.id" :active="active" />
  </section>
</template>
<script setup>
import PreviewMaintenance from './PreviewMaintenance.vue'
import { onUnmounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  library: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const busy = ref(null)
const tvMsg = ref('')
const tvDone = ref(0)
const tvTotal = ref(0)
// 刮削结果状态中文（ok_external/ok_offline 为离线/无 token 降级路径）
const SCRAPE_STATUS_TEXT = {
  ok: '已刮削', ok_offline: '离线补全', ok_external: '离线外源',
  ok_needs_review: '待确认', no_match: '未匹配', skipped_cached: '已缓存',
  error: '失败',
}
let tvJob = ''
let tvTimer = null

async function doTvScrape(force) {
  busy.value = 'tv'
  tvMsg.value = ''
  tvDone.value = 0
  tvTotal.value = 0
  try {
    const d = await api('/api/jobs/tv-scrape', {
      method: 'POST',
      body: JSON.stringify({ library_id: props.library.id, force }),
    })
    tvJob = d.job_id
    clearInterval(tvTimer)
    tvTimer = setInterval(pollTv, 1500)
  } catch (e) {
    tvMsg.value = '启动失败：' + e.message
    busy.value = null
  }
}
async function pollTv() {
  try {
    const j = await api('/api/jobs/tv-scrape/' + tvJob)
    tvDone.value = j.done || 0
    tvTotal.value = j.total || 0
    if (j.state === 'done') {
      clearInterval(tvTimer); tvTimer = null; busy.value = null
      const c = (j.summary && j.summary.counts) || {}
      const parts = Object.entries(c).map(([k, v]) => `${SCRAPE_STATUS_TEXT[k] || k} ${v}`)
      tvMsg.value = '完成：' + (parts.join('，') || '无待刮剧')
      emit('changed')
    } else if (j.state === 'failed' || j.state === 'cancelled') {
      clearInterval(tvTimer); tvTimer = null; busy.value = null
      tvMsg.value = j.error || j.state
    }
  } catch (e) { /* 下一轮再试 */ }
}
async function cancelTv() {
  if (!tvJob) return
  try { await api(`/api/jobs/tv-scrape/${tvJob}/cancel`, { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

const tvNfoMsg = ref('')
const tvNfoDone = ref(0)
const tvNfoTotal = ref(0)
let tvNfoJob = ''
let tvNfoTimer = null

async function doTvNfo(dryRun) {
  busy.value = 'tvnfo'
  tvNfoMsg.value = ''
  tvNfoDone.value = 0
  tvNfoTotal.value = 0
  try {
    const d = await api('/api/jobs/rebuild-tv-nfo', {
      method: 'POST',
      body: JSON.stringify({ library_id: props.library.id, dry_run: dryRun }),
    })
    tvNfoJob = d.job_id
    clearInterval(tvNfoTimer)
    tvNfoTimer = setInterval(pollTvNfo, 1200)
  } catch (e) {
    tvNfoMsg.value = '启动失败：' + e.message
    busy.value = null
  }
}
async function pollTvNfo() {
  try {
    const j = await api('/api/jobs/rebuild-tv-nfo/' + tvNfoJob)
    tvNfoDone.value = j.done || 0
    tvNfoTotal.value = j.total || 0
    if (j.state === 'done') {
      clearInterval(tvNfoTimer); tvNfoTimer = null; busy.value = null
      const s = (j.summary && j.summary.totals) || {}
      tvNfoMsg.value = `完成：NFO 写 ${s.nfo_wrote || 0}，海报 ${s.artwork_wrote || 0}`
        + (s.nfo_failed ? `，失败 ${s.nfo_failed}` : '')
      emit('changed')
    } else if (j.state === 'failed' || j.state === 'cancelled') {
      clearInterval(tvNfoTimer); tvNfoTimer = null; busy.value = null
      tvNfoMsg.value = j.error || j.state
    }
  } catch (e) { /* 下一轮再试 */ }
}
async function cancelTvNfo() {
  if (!tvNfoJob) return
  try { await api(`/api/jobs/rebuild-tv-nfo/${tvNfoJob}/cancel`, { method: 'POST' }) } catch (e) { /* 忽略 */ }
}
onUnmounted(() => {
  if (tvTimer) clearInterval(tvTimer)
  if (tvNfoTimer) clearInterval(tvNfoTimer)
})
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
</style>
