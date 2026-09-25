<template>
  <section class="card-block">
    <h3>剧集维护 <span class="fhint">作用于「{{ library.name }}」视频库</span></h3>
    <p class="hint">扫描会自动补新剧元数据；这里用于手动补刮/强制重刮与 NFO/海报落盘，日常无需操作。</p>

    <div class="bar">
      <button @click="doTvScrape(false)" :disabled="!!busy">
        {{ busy === 'tv' ? `剧集刮削中 ${tvDone}/${tvTotal}…` : '剧集刮削（未匹配/未刮）' }}
      </button>
      <button @click="doTvScrape(true)" :disabled="!!busy">强制重刮</button>
      <button v-if="busy === 'tv'" @click="cancelTv">取消</button>
      <span>{{ tvMsg }}</span>
    </div>
    <p class="hint">TMDB 拉取剧/季/集元数据与海报；绝对集号按 TMDB 季集数自动映射。
      只写数据库与 data/posters，不改 NAS 文件；未匹配的剧可在剧集详情页手动匹配。</p>

    <div class="bar">
      <button @click="doTvNfo(false)" :disabled="!!busy">
        {{ busy === 'tvnfo' ? `写 NFO 中 ${tvNfoDone}/${tvNfoTotal}…` : '重建剧集 NFO/海报' }}
      </button>
      <button @click="doTvNfo(true)" :disabled="!!busy">预览写入清单</button>
      <button v-if="busy === 'tvnfo'" @click="cancelTvNfo">取消</button>
      <span>{{ tvNfoMsg }}</span>
    </div>
    <p class="hint">写 tvshow.nfo + 季 season.nfo 到 NAS；视频库「海报」模式为「NFO+海报」时
      另写 poster.jpg/fanart.jpg/季海报（不改名、不动视频文件）。远程库逐集 NFO 默认关（SMB 单文件写 ~1.6s，
      千集级耗时过长），需要时设 env TV_EPISODE_NFO=1。</p>
  </section>
</template>
<script setup>
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
      const parts = Object.entries(c).map(([k, v]) => `${k} ${v}`)
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
