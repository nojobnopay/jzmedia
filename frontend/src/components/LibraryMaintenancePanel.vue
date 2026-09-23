<template>
  <section :id="active ? 'sec-meta' : undefined" class="card-block">
    <h3>高级维护 <span class="fhint">作用于「{{ media.name }}」的 {{ movieLibs.length }} 个电影视频库</span></h3>
    <p class="hint">库级批量修复，日常无需操作：补产地、刷新 TMDB、重写 NFO/海报、清理历史脏行。剧集库用下方「剧集刮削」。</p>

    <div v-if="tvLibs.length" class="bar">
      <button @click="doTvScrape(false)" :disabled="!!busy">
        {{ busy === 'tv' ? `剧集刮削中 ${tvDone}/${tvTotal}…` : '剧集刮削（未匹配/未刮）' }}
      </button>
      <button @click="doTvScrape(true)" :disabled="!!busy">强制重刮</button>
      <button v-if="busy === 'tv'" @click="cancelTv">取消</button>
      <span>{{ tvMsg }}</span>
    </div>
    <p v-if="tvLibs.length" class="hint">TMDB 拉取剧/季/集元数据与海报；绝对集号按 TMDB 季集数自动映射。
      只写数据库与 data/posters，不改 NAS 文件；未匹配的剧可在剧集详情页手动匹配。</p>

    <div v-if="tvLibs.length" class="bar">
      <button @click="doTvNfo(false)" :disabled="!!busy">
        {{ busy === 'tvnfo' ? `写 NFO 中 ${tvNfoDone}/${tvNfoTotal}…` : '重建剧集 NFO/海报' }}
      </button>
      <button @click="doTvNfo(true)" :disabled="!!busy">预览写入清单</button>
      <button v-if="busy === 'tvnfo'" @click="cancelTvNfo">取消</button>
      <span>{{ tvNfoMsg }}</span>
    </div>
    <p v-if="tvLibs.length" class="hint">写 tvshow.nfo + 季 season.nfo 到 NAS；视频库「海报」模式为「NFO+海报」时
      另写 poster.jpg/fanart.jpg/季海报（不改名、不动视频文件）。远程库逐集 NFO 默认关（SMB 单文件写 ~1.6s，
      千集级耗时过长），需要时设 env TV_EPISODE_NFO=1。</p>

    <div class="bar">
      <button @click="doBackfill" :disabled="!!busy || !movieLibs.length">{{ busy === 'backfill' ? '补数据中…' : '补产地信息' }}</button>
      <span>{{ backfillMsg }}</span>
    </div>

    <div class="bar">
      <button @click="doRefreshAll" :disabled="!!busy || !movieLibs.length">
        {{ busy === 'refresh' ? '刷新中…' : (armRefresh ? '确认刷新全部 TMDB' : '刷新全部 TMDB 数据') }}
      </button>
      <span>{{ refreshMsg }}</span>
    </div>
    <p v-if="armRefresh" class="hint warn-text">将逐部请求 TMDB（以 limit 截断），无变化的不动，手工标题不受影响。再点一次执行。</p>

    <div class="bar">
      <button @click="doRebuildNfo" :disabled="!!busy || !movieLibs.length">{{ busy === 'nfo' ? '重建中…' : '重建全部 NFO' }}</button>
      <span>{{ nfoMsg }}</span>
    </div>

    <div class="bar">
      <button @click="doRebuildMeta" :disabled="!!busy || !movieLibs.length">
        {{ busy === 'meta' ? `重建元数据中 ${metaDone}/${metaTotal}…` : (armMeta ? '确认重建元数据' : '重建元数据（NFO+海报）') }}
      </button>
      <button v-if="busy === 'meta'" @click="cancelMeta">取消</button>
      <span>{{ metaMsg }}</span>
    </div>
    <p v-if="armMeta" class="hint warn-text">按现有匹配从镜像缓存重写 NFO 与 poster/fanart（不触网、不覆盖手工标题；远程库直接写 NAS）。再点一次执行。</p>

    <div class="bar">
      <button @click="doCleanBdmv" :disabled="!!busy || !movieLibs.length">
        {{ busy === 'bdmv' ? '清理中…' : (armBdmv ? '确认清理 BDMV 碎片' : '清理 BDMV 碎片') }}
      </button>
      <span>{{ bdmvMsg }}</span>
    </div>
    <p v-if="armBdmv" class="hint warn-text">删除原盘结构（BDMV/VIDEO_TS）里的碎片记录（只删库记录，不动物理文件）。再点一次执行。</p>

    <div class="bar">
      <button @click="doCleanSamples" :disabled="!!busy || !movieLibs.length">
        {{ busy === 'samples' ? '清理中…' : (armSamples ? '确认清理误入库样片' : '清理误入库样片') }}
      </button>
      <span>{{ samplesMsg }}</span>
    </div>
    <p v-if="armSamples" class="hint warn-text">删除路径属于 Sample/Screens/Behind The Scenes 等样片/花絮目录的影片记录（只删库记录，不动物理文件）。再点一次执行。</p>

    <div class="bar">
      <button @click="doCleanCache" :disabled="!!busy">
        {{ busy === 'cache' ? '清理中…' : (armCache ? '确认清理转码缓存' : '清理转码缓存') }}
      </button>
      <span>{{ cacheMsg }}</span>
    </div>
    <p v-if="armCache" class="hint warn-text">全局（不限本媒体库）：删除 data/transcode 下可回收的转码/预缓存产物，跳过正在播放的会话；海报/NFO 不动。再点一次执行。</p>

    <div v-if="media.source !== 'local'" class="bar">
      <button @click="doCleanMount" :disabled="!!busy">
        {{ busy === 'mount' ? '清理中…' : (armMount ? '确认清理挂载残留' : '清理挂载残留') }}
      </button>
      <span>{{ mountMsg }}</span>
    </div>
    <p v-if="armMount" class="hint warn-text">清理挂载点目录里被历史误写的 NFO/图片（仅在未真正挂载时执行，绝不动 NAS）。再点一次执行。</p>
  </section>
</template>
<script setup>
import { onUnmounted, ref, computed } from 'vue'
import { api } from '../api.js'
import { usePolling } from '../usePolling.js'

const props = defineProps({
  media: { type: Object, required: true },
  libFilter: { type: Number, default: null },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const movieLibs = computed(() => (props.media.video_libraries || [])
  .filter(v => (v.kind || 'movie') !== 'tv'))
const tvLibs = computed(() => (props.media.video_libraries || [])
  .filter(v => (v.kind || 'movie') === 'tv'))

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
      body: JSON.stringify({ media_library_id: props.media.id, force }),
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
      body: JSON.stringify({ media_library_id: props.media.id, dry_run: dryRun }),
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
const backfillMsg = ref('')
const refreshMsg = ref('')
const nfoMsg = ref('')
const metaMsg = ref('')
const bdmvMsg = ref('')
const mountMsg = ref('')

function libBody(extra = {}) {
  return JSON.stringify({ ...extra, media_library_id: props.media.id })
}

async function doBackfill() {
  busy.value = 'backfill'
  backfillMsg.value = ''
  try {
    const d = await api('/api/jobs/backfill-meta', { method: 'POST', body: libBody() })
    backfillMsg.value = `回填完成：${d.ok}/${d.total}，失败 ${d.failed.length}`
    emit('changed')
  } catch (e) {
    backfillMsg.value = '回填失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armRefresh = ref(false)
async function doRefreshAll() {
  if (!armRefresh.value) {
    armRefresh.value = true
    refreshMsg.value = '再点一次确认执行'
    return
  }
  armRefresh.value = false
  busy.value = 'refresh'
  refreshMsg.value = ''
  try {
    const d = await api('/api/jobs/tmdb-refresh', {
      method: 'POST', body: libBody({ limit: 5000 })
    })
    const changed = d.results.filter(r => r.changed).length
    refreshMsg.value = `完成：${d.total} 部中有变化 ${changed} 部，失败 ${d.failed.length}`
    emit('changed')
  } catch (e) {
    refreshMsg.value = '刷新失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doRebuildNfo() {
  busy.value = 'nfo'
  nfoMsg.value = ''
  try {
    const d = await api('/api/jobs/rebuild-nfo', { method: 'POST', body: libBody() })
    nfoMsg.value = `完成：重写 ${d.ok}/${d.total}，跳过缺失 ${d.skipped_missing}，失败 ${d.failed.length}`
    emit('changed')
  } catch (e) {
    nfoMsg.value = '重建失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 重建元数据：jobkit 后台任务（进度轮询，可取消）
const armMeta = ref(false)
const metaDone = ref(0)
const metaTotal = ref(0)
let metaJobId = ''
const metaPoll = usePolling(pollMetaJob, { interval: 1000 })
async function doRebuildMeta() {
  if (!armMeta.value) {
    armMeta.value = true
    metaMsg.value = '再点一次确认执行'
    return
  }
  armMeta.value = false
  busy.value = 'meta'
  metaMsg.value = ''
  metaDone.value = 0
  metaTotal.value = 0
  try {
    const d = await api('/api/jobs/rebuild-meta', {
      method: 'POST', body: libBody({ dry_run: false, artwork: true, backdrops: false })
    })
    metaJobId = d.job_id || ''
    metaTotal.value = d.total || 0
    if (d.resumed) metaMsg.value = '已有重建任务在跑，跟踪进度…'
    if (!metaJobId) return finishMeta('没有可重建的影片（都需要 TMDB 匹配）')
    metaPoll.start()
  } catch (e) {
    metaMsg.value = '重建失败：' + e.message
    busy.value = null
  }
}
async function pollMetaJob() {
  if (!metaJobId) return
  try {
    const st = await api('/api/jobs/rebuild-meta/' + metaJobId)
    if (st.state === 'running') {
      metaDone.value = st.done || 0
      if (st.total) metaTotal.value = st.total
      return
    }
    if (st.state === 'done') {
      finishMeta(`完成：重写 ${st.done || 0}/${st.total || 0}`
        + ((st.failed || []).length ? `，失败 ${(st.failed || []).length}` : ''))
      emit('changed')
    } else if (st.state === 'cancelled') {
      finishMeta(`已取消（${st.done || 0}/${st.total || 0}）`)
    } else {
      finishMeta('重建失败：' + (st.error || '未知错误'))
    }
  } catch (e) { /* 轮询失败下次继续 */ }
}
function finishMeta(msg) {
  metaPoll.stop()
  metaJobId = ''
  metaMsg.value = msg
  busy.value = null
}
async function cancelMeta() {
  if (!metaJobId) return
  try { await api('/api/jobs/rebuild-meta/' + metaJobId + '/cancel', { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

const armBdmv = ref(false)
async function doCleanBdmv() {
  if (!armBdmv.value) {
    armBdmv.value = true
    bdmvMsg.value = '再点一次确认执行'
    return
  }
  armBdmv.value = false
  busy.value = 'bdmv'
  bdmvMsg.value = ''
  try {
    const d = await api('/api/jobs/clean-bdmv', { method: 'POST', body: libBody() })
    bdmvMsg.value = d.total ? `已删除 ${d.deleted}/${d.total} 条碎片记录` : '没有需要清理的记录'
    emit('changed')
  } catch (e) {
    bdmvMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armSamples = ref(false)
const samplesMsg = ref('')
async function doCleanSamples() {
  if (!armSamples.value) {
    armSamples.value = true
    samplesMsg.value = '再点一次确认执行'
    return
  }
  armSamples.value = false
  busy.value = 'samples'
  samplesMsg.value = ''
  try {
    const d = await api('/api/jobs/clean-samples', { method: 'POST', body: libBody() })
    samplesMsg.value = d.total ? `已删除 ${d.deleted}/${d.total} 条误入库记录` : '没有需要清理的记录'
    emit('changed')
  } catch (e) {
    samplesMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 转码缓存手动清理（全局）：dry_run 预览 → 再点执行（删可回收目录，跳过在播会话）
const armCache = ref(false)
const cacheMsg = ref('')
function fmtBytes(n) {
  const x = Number(n) || 0
  if (x >= 1 << 30) return (x / (1 << 30)).toFixed(1) + ' GB'
  if (x >= 1 << 20) return (x / (1 << 20)).toFixed(0) + ' MB'
  return Math.max(0, Math.round(x / 1024)) + ' KB'
}
async function doCleanCache() {
  if (!armCache.value) {
    busy.value = 'cache'
    cacheMsg.value = ''
    try {
      const d = await api('/api/stream/cache/clean', {
        method: 'POST', body: JSON.stringify({ dry_run: true })
      })
      armCache.value = true
      cacheMsg.value = d.candidates
        ? `可清理 ${d.candidates} 个目录 / ${fmtBytes(d.candidate_bytes)}（缓存共 ${fmtBytes(d.total)}，上限 ${fmtBytes(d.cap)}）——再点一次执行`
        : `没有可清理的转码缓存（共 ${fmtBytes(d.total)}）`
    } catch (e) {
      cacheMsg.value = '预览失败：' + e.message
    } finally {
      busy.value = null
    }
    return
  }
  armCache.value = false
  busy.value = 'cache'
  try {
    const d = await api('/api/stream/cache/clean', {
      method: 'POST', body: JSON.stringify({ dry_run: false })
    })
    cacheMsg.value = `已清理 ${d.removed} 个目录，释放 ${fmtBytes(d.freed)}（剩余 ${fmtBytes(Math.max(0, d.total - d.freed))}）`
  } catch (e) {
    cacheMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armMount = ref(false)
async function doCleanMount() {
  if (!armMount.value) {
    armMount.value = true
    mountMsg.value = '再点一次确认执行'
    return
  }
  armMount.value = false
  busy.value = 'mount'
  mountMsg.value = ''
  try {
    const d = await api('/api/jobs/clean-mount-artifacts', { method: 'POST', body: libBody() })
    mountMsg.value = d.total ? `已清理 ${d.removed}/${d.total} 个文件` : '没有挂载残留'
    emit('changed')
  } catch (e) {
    mountMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
</style>
