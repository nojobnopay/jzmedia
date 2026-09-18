<template>
  <div class="dlg-mask" @click.self="closeDlg">
    <div ref="dlgRef" class="dlg" role="dialog" aria-modal="true">
      <h3>{{ upStep === 'organize' ? '归档整理（第 2 步）' : upStep === 'done' ? '完成' : '上传到媒体库（第 1 步）' }}</h3>
      <template v-if="upStep === 'upload'">
      <div class="bar">
        <label><input type="radio" value="files" v-model="upMode" :disabled="uploading" @change="onUpModeChange" /> 多选文件</label>
        <label><input type="radio" value="dir" v-model="upMode" :disabled="uploading" @change="onUpModeChange" /> 整个文件夹</label>
      </div>
      <div class="bar">
        <input v-if="upMode === 'files'" type="file" multiple ref="upFiles" :disabled="uploading" @change="onUpInputChange" />
        <input v-else type="file" webkitdirectory ref="upDir" :disabled="uploading" @change="onUpInputChange" />
      </div>
      <div class="bar"><span class="fhint">上传到 待整理/，文件夹结构原样保留；字幕/花絮自动归属；同名文件跳过不覆盖；&gt;2GB 建议局域网操作，可随时取消</span></div>
      <div v-if="upFolderHead" class="bar"><span>已选文件夹：{{ upFolderHead }}</span></div>
      <div v-if="upQueue.length" class="bar"><span class="fhint">共 {{ upQueue.length }} 个文件 · {{ fmtBytes(upTotalSize) }}{{ upDoneCount ? ` · 已传 ${upDoneCount}` : '' }}{{ upScanning ? ' · 当前已传完 · 刮削中…' : (upCurPct != null ? ` · 当前 ${upCurPct}%` : '') }}</span></div>
      <div v-if="upQueue.length" class="up-progress"><div class="up-progress-fill" :style="{ width: upTotalPct + '%' }"></div></div>
      <div v-if="upScanning" class="bar"><span class="up-scan">{{ upScanHint }}</span></div>
      <ul v-if="upQueue.length" class="collist">
        <li v-for="(t, i) in visibleUpQueue" :key="i"><span :title="t.rel">{{ midEllipsis(t.rel) }}</span><span class="fhint">{{ upTaskState(t) }}</span></li>
      </ul>
      <p v-if="upQueue.length > 50" class="fhint">等共 {{ upQueue.length }} 个<span v-if="!showAllUp">（仅列前 50）</span> <button v-if="!showAllUp" @click="showAllUp = true">展开全部</button></p>
      <div v-if="upSummary" class="bar"><span class="fhint">{{ upSummary }}</span></div>
      <div v-if="upNeedsMatch.length" class="bar"><span class="fhint">以下需处理（共 {{ upNeedsMatch.length }} 部）：</span></div>
      <ul v-if="upNeedsMatch.length" class="collist">
        <li v-for="t in upNeedsMatch" :key="t.rel">
          <span :title="t.rel">{{ midEllipsis(t.rel) }}（{{ noteText(t) }}）</span>
          <button v-if="t.movieId" @click="router.push('/m/' + t.movieId)">去详情匹配</button>
          <button v-if="t.movieId && canRetry(t)" @click="rescanTask(t)" :disabled="t._rescuing">
            {{ t._rescuing ? '重试中…' : '重试刮削' }}
          </button>
        </li>
      </ul>
      <div class="bar">
        <button v-if="!uploading && !upFinished" @click="startUpload" :disabled="!canStartUpload">开始上传</button>
        <button v-if="uploading" @click="cancelUpload">取消上传</button>
        <button v-if="upFinished && upOrganizable" @click="goUpOrganize">下一步：归档整理</button>
        <button v-if="!uploading" @click="closeDlg">{{ upFinished ? '关闭' : '取消' }}</button>
        <span>{{ upMsg }}</span>
      </div>
      </template>
      <template v-if="upStep === 'organize'">
      <div class="bar"><span class="fhint">待整理 → 电影（按大区），仅本次上传的 {{ upOrganizableIds.length }} 部影片，先预览再执行</span></div>
      <div class="bar"><span>{{ upOrgMsg }}</span></div>
      <ul v-if="upOrgPlans.length" class="collist">
        <li v-for="p in upOrgPlans" :key="p.id"><span :title="p.from + ' → ' + p.to">{{ midEllipsis(p.from, 40) }} → {{ midEllipsis(p.to, 40) }}</span><span v-if="p.status" class="fhint">{{ p.status }}</span></li>
      </ul>
      <div v-if="upOrgConflicts.length" class="bar"><span class="fhint">冲突 {{ upOrgConflicts.length }} 项，需先去详情匹配：</span></div>
      <ul v-if="upOrgConflicts.length" class="collist">
        <li v-for="c in upOrgConflicts" :key="'c' + c.id"><span :title="(c.title || '') + ' ' + c.from">{{ midEllipsis(c.title || c.from) }}（{{ c.status }}）</span><button @click="router.push('/m/' + c.id)">去详情匹配</button></li>
      </ul>
      <div class="bar">
        <button v-if="!upOrgDone" @click="doUpOrganize" :disabled="upOrgBusy || !upOrgPlans.length">{{ upOrgBusy ? '执行中…' : '确认搬迁' }}</button>
        <button @click="closeDlg" :disabled="upOrgBusy">{{ upOrgDone ? '完成' : '稍后整理' }}</button>
        <span>{{ upOrgMsg }}</span>
      </div>
      </template>
      <template v-if="upStep === 'done'">
      <div class="bar"><span class="fhint">{{ upDoneSummary }}</span></div>
      <div class="bar"><button @click="closeDlg">关闭</button></div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api, apiUpload } from '../api.js'
import { libParam } from '../libraries.js'
import { fmtBytes, midEllipsis } from '../format.js'
import { useFocusTrap } from '../useFocusTrap.js'

const emit = defineEmits(['close', 'done'])
const router = useRouter()
const dlgRef = ref(null)
useFocusTrap(ref(true), dlgRef)

const upMode = ref('files')
const upFiles = ref(null)
const upDir = ref(null)
const upFolderHead = ref('')
const upQueue = ref([])
const uploading = ref(false)
const upMsg = ref('')
const upSummary = ref('')
const showAllUp = ref(false)
const upCurPct = ref(null)
let upAbort = null
let upCancelled = false
const upTotalSize = computed(() => upQueue.value.reduce((a, t) => a + (t.file.size || 0), 0))
const upDoneCount = computed(() => upQueue.value.filter(t => t.state === 'done' || t.state === 'skipped' || t.state === 'error').length)
const upTotalPct = computed(() => {
  const n = upQueue.value.length
  if (!n) return 0
  return Math.min(100, Math.round((upDoneCount.value * 100 + (upCurPct.value || 0)) / n))
})
const upFinished = computed(() => upQueue.value.length > 0 && !uploading.value && upQueue.value.every(t => t.state !== 'queued' && t.state !== 'active'))
const canStartUpload = computed(() => !uploading.value && upQueue.value.some(t => t.state === 'queued'))
const visibleUpQueue = computed(() => showAllUp.value ? upQueue.value : upQueue.value.slice(0, 50))
// 状态文案（评审 B9 后续）：失败可重试、未匹配可去匹配，别只丢裸状态码
const NOTE_TEXT = {
  ok: '已入库', ok_needs_review: '待确认', no_match: 'TMDB 未匹配',
  scan_failed: '刮削失败（可重试）', skipped_cached: '已入库', skipped_unchanged: '未变化',
  skipped_sample: '样片跳过', skipped_episode_v1: '剧集跳过'
}
function noteText(t) {
  const n = String((t && t.note) || '')
  if (n.startsWith('stored_scan_warn')) return '刮削出错（可重试）'
  return NOTE_TEXT[n] || n || '完成'
}
function canRetry(t) {
  const n = String((t && t.note) || '')
  return n === 'no_match' || n === 'scan_failed' || n.startsWith('stored_scan_warn')
}
async function rescanTask(t) {
  if (!t || !t.movieId || t._rescuing) return
  t._rescuing = true
  upMsg.value = ''
  try {
    const d = await api('/api/movies/' + t.movieId + '/rescan', { method: 'POST' })
    const st = (d && d.status) || ''
    if (st === 'ok' || st === 'ok_needs_review') {
      t.note = d.tmdb_id ? 'ok' : st
      upMsg.value = `「${d.title || t.rel}」刮削完成`
      emit('done')
    } else if (st === 'scan_failed') {
      t.note = 'scan_failed'
      upMsg.value = '仍失败：' + ((d && d.error) || '网络/接口异常，可稍后再试')
    } else {
      t.note = st || 'no_match'
      upMsg.value = '仍无匹配结果，可去详情手动搜索'
    }
  } catch (e) {
    upMsg.value = '重试失败：' + e.message
  } finally {
    t._rescuing = false
  }
}
function upTaskState(t) {
  if (t.state === 'queued') return fmtBytes(t.file.size)
  if (t.state === 'active') {
    if (t.phase === 'scanning') return '已传完 · 联网刮削中…'
    return (upCurPct.value != null ? upCurPct.value + '% · ' : '') + '上传中…'
  }
  if (t.state === 'skipped') return '已存在·跳过'
  if (t.state === 'error') return '失败：' + (t.note || '')
  return noteText(t)
}
// 字节传完后的等待提示（服务端在响应前同步跑 scan_one：TMDB 搜索/详情/海报头像）
const upScanSecs = ref(0)
const scanSamples = ref([])
let upScanTimer = null
function startScanTicker() {
  if (upScanTimer) clearInterval(upScanTimer)
  upScanSecs.value = 0
  upScanTimer = setInterval(() => { upScanSecs.value++ }, 1000)
}
function stopScanTicker() {
  if (upScanTimer) { clearInterval(upScanTimer); upScanTimer = null }
}
const upScanning = computed(() => upQueue.value.some(t => t.state === 'active' && t.phase === 'scanning'))
const upScanHint = computed(() => {
  const base = `已传完，正在联网匹配 TMDB 元数据并下载海报，请耐心等待（已等待 ${upScanSecs.value}s）`
  const samples = scanSamples.value
  if (!samples.length) return base   // 本会话还没有样本：不给臆测耗时（评审 R04-Q4）
  const avg = Math.max(1, Math.round(samples.reduce((a, b) => a + b, 0) / samples.length / 1000))
  const left = upQueue.value.filter(t => t.state === 'queued' || (t.state === 'active' && t.phase === 'scanning')).length
  return `${base}；本会话平均约 ${avg} 秒/部，共 ${left} 部待刮削，预计还需约 ${avg * left} 秒`
})
function stageName(f) {
  const rel = (upMode.value === 'dir' && f.webkitRelativePath) ? f.webkitRelativePath : f.name
  return String(rel || f.name || '').replace(/\\/g, '/')
}
function closeDlg() {
  if (uploading.value || upOrgBusy.value) return
  stopScanTicker()
  emit('close')
}
function collectStaged() {
  const input = upMode.value === 'dir' ? upDir.value : upFiles.value
  const files = (input && input.files) ? Array.from(input.files) : []
  const out = []
  for (const f of files) {
    const rel = stageName(f)
    // 跳过隐藏文件（.DS_Store 等）与空路径
    if (!rel || rel.split('/').some(s => s.startsWith('.'))) continue
    out.push({ file: f, rel, state: 'queued', phase: 'uploading', note: '', movieId: null })
  }
  return out
}
function syncFolderHead() {
  upFolderHead.value = ''
  if (upMode.value !== 'dir' || !upQueue.value.length) return
  const top = (upQueue.value[0].rel.split('/')[0] || '').trim()
  if (!top) return
  upFolderHead.value = `${top}（${upQueue.value.length} 个文件 · ${fmtBytes(upTotalSize.value)}）`
}
function onUpInputChange() {
  if (uploading.value) return
  upQueue.value = collectStaged()
  showAllUp.value = false
  upSummary.value = ''
  syncFolderHead()
  if (!upQueue.value.length) {
    upMsg.value = upMode.value === 'dir' ? '所选文件夹没有可上传的文件' : '先选择文件'
  } else {
    upMsg.value = ''
  }
}
function onUpModeChange() {
  if (uploading.value) return
  upQueue.value = []
  upFolderHead.value = ''
  upSummary.value = ''
  upMsg.value = ''
  showAllUp.value = false
}
async function startUpload() {
  const staged = upQueue.value.length ? upQueue.value : collectStaged()
  if (!staged.length) {
    upMsg.value = upMode.value === 'dir' ? '先选择文件夹' : '先选择文件'
    return
  }
  if (staged.length > 500) {
    upMsg.value = `一次最多 500 个文件（当前 ${staged.length} 个），请分批上传`
    return
  }
  upQueue.value = staged
  syncFolderHead()
  upMsg.value = ''
  upSummary.value = ''
  uploading.value = true
  upCancelled = false
  scanSamples.value = []
  stopScanTicker()
  let ok = 0, skipped = 0, failed = 0, review = 0, nomatch = 0, scanfail = 0
  for (const t of upQueue.value) {
    if (upCancelled) break
    if (t.state !== 'queued') continue
    t.state = 'active'
    t.phase = 'uploading'
    t.scanStartedAt = 0
    upCurPct.value = 0
    const h = apiUpload('/api/uploads', t.file, {
      fields: { relpath: t.rel, library_id: libParam() },
      onProgress: (p) => { upCurPct.value = p },
      onUploaded: () => {
        // 延迟 800ms 再切“刮削中”，字幕/花絮等本地快路径不会闪提示
        if (t._hintTimer) clearTimeout(t._hintTimer)
        t._hintTimer = setTimeout(() => {
          if (t.state === 'active' && t.phase === 'uploading') {
            t.phase = 'scanning'
            t.scanStartedAt = Date.now()
            startScanTicker()
          }
        }, 800)
      }
    })
    upAbort = h.abort
    try {
      const r = await h.promise
      clearTimeout(t._hintTimer)
      t._hintTimer = null
      if (t.scanStartedAt) {
        scanSamples.value.push(Date.now() - t.scanStartedAt)
        t.scanStartedAt = 0
      }
      stopScanTicker()
      t.phase = 'uploading'
      const st = (r && r.status) || 'stored'
      t.state = 'done'
      t.note = st
      t.movieId = (r && r.movie_id) || null
      ok++
      if (st === 'ok_needs_review') review++
      else if (st === 'no_match') nomatch++
      else if (st === 'scan_failed' || st.startsWith('stored_scan_warn')) scanfail++
    } catch (e) {
      clearTimeout(t._hintTimer)
      t._hintTimer = null
      t.scanStartedAt = 0
      stopScanTicker()
      t.phase = 'uploading'
      const m = String((e && e.message) || e)
      if (m === '已取消' || upCancelled) {
        t.state = 'queued'
        t.note = ''
      } else if (/^409\b/.test(m)) {
        t.state = 'skipped'
        skipped++
      } else {
        t.state = 'error'
        t.note = m.slice(0, 120)
        failed++
      }
    } finally {
      upAbort = null
    }
  }
  uploading.value = false
  upCurPct.value = null
  stopScanTicker()
  const parts = [`上传完成：成功 ${ok}`]
  if (skipped) parts.push(`跳过 ${skipped}`)
  if (nomatch) parts.push(`未匹配 ${nomatch}`)
  if (review) parts.push(`待确认 ${review}`)
  if (scanfail) parts.push(`刮削失败 ${scanfail}（可重试）`)
  if (failed) parts.push(`失败 ${failed}`)
  if (upCancelled) parts.push('（已取消）')
  upSummary.value = parts.join(' · ')
  emit('done')
}
function cancelUpload() {
  upCancelled = true
  if (upAbort) upAbort()
}
// 归档整理（第 2 步）：仅本次上传影片，待整理 → 电影（按大区），先预览再执行
const upStep = ref('upload')
const upOrgPlans = ref([])
const upOrgConflicts = ref([])
const upOrgMsg = ref('')
const upOrgBusy = ref(false)
const upOrgDone = ref(false)
const upDoneSummary = ref('')
const upNeedsMatch = computed(() => upQueue.value.filter(t => {
  if (t.state !== 'done') return false
  const s = t.note || ''
  return s === 'no_match' || s === 'scan_failed' || s === 'ok_needs_review'
    || s.startsWith('stored_scan_warn')
}))
const upOrganizableIds = computed(() => [...new Set(
  upQueue.value.filter(t => t.movieId).map(t => t.movieId))])
const upOrganizable = computed(() => upOrganizableIds.value.length > 0)
function upOrgBody(dry_run) {
  return JSON.stringify({ mode: 'relocate', from_prefix: '待整理', to_dir: '电影',
    library_id: libParam(),
    ids: upOrganizableIds.value, dry_run })
}
async function goUpOrganize() {
  upStep.value = 'organize'
  upOrgPlans.value = []
  upOrgConflicts.value = []
  upOrgDone.value = false
  upOrgMsg.value = '预览中…'
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: upOrgBody(true) })
    upOrgPlans.value = d.plans || []
    upOrgConflicts.value = d.conflicts || []
    upOrgMsg.value = upOrgPlans.value.length ? `可搬迁 ${upOrgPlans.value.length} 项`
      : (upOrgConflicts.value.length ? `无可搬迁，冲突 ${upOrgConflicts.value.length} 项` : '没有需要整理的')
  } catch (e) {
    upOrgMsg.value = '预览失败：' + e.message
  }
}
async function doUpOrganize() {
  upOrgBusy.value = true
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: upOrgBody(false) })
    upOrgPlans.value = d.results || []
    upOrgConflicts.value = d.conflicts || []
    const ok = upOrgPlans.value.filter(r => r.status === 'moved').length
    upOrgDone.value = true
    upDoneSummary.value = `搬迁完成：${ok}/${upOrgPlans.value.length}，已归档到正式库`
    upStep.value = 'done'
    emit('done')
  } catch (e) {
    upOrgMsg.value = '执行失败：' + e.message
  } finally {
    upOrgBusy.value = false
  }
}
</script>

<style scoped>
/* 对话框基础样式（R14-Q5 待统一：与 Library/Detail/CollectionDetail 的同名 scoped 规则一致） */
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: #1c1c1c; border-radius: 10px; padding: 16px; min-width: 320px; max-width: 560px; max-height: 80vh; overflow: auto; }
.dlg h3 { margin: 0 0 8px; }
.fhint { color: #777; font-size: 0.75rem; }
.collist { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 30vh; overflow: auto; }
.collist li { display: flex; justify-content: space-between; gap: 8px; align-items: center; background: #262626; border-radius: 8px; padding: 6px 10px; }
.up-scan { color: #e0a63c; font-size: 0.8125rem; }
.up-progress { height: 8px; border-radius: 999px; background: #2c2c2c; overflow: hidden; margin: 0 12px; }
.up-progress-fill { height: 100%; background: #e50914; border-radius: 999px; transition: width .2s; }
</style>
