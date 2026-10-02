<template>
  <JzDialog v-if="!tvPlan" class="upload-dialog" mask-class="upload-mask" :busy="uploading || upOrgBusy || tvBusy" close-label="关闭上传窗口"
    :title="upStep === 'organize' ? '归档整理（第 2 步）' : upStep === 'done' ? '完成' : (kind === 'tv' ? '上传剧集' : '上传影片（第 1 步）')" @close="closeDlg">
      <template v-if="upStep === 'upload'">
      <div class="bar upload-target">
        <label>上传到视频库
          <select v-model.number="upLibId" :disabled="libraryId != null || uploading || tvBusy || uploadCounts.done > 0">
            <option v-for="l in upLibCandidates" :key="l.id" :value="l.id">
              {{ l.media_name ? l.media_name + ' · ' : '' }}{{ l.name }}{{ l.subpath ? '（' + l.subpath + '）' : '' }}
            </option>
          </select>
        </label>
        <span v-if="!upLibCandidates.length" class="fhint">没有启用且可写的{{ kind === 'tv' ? '剧集' : '电影' }}库。<router-link to="/settings?sec=sec-libraries">添加视频库</router-link></span>
      </div>
      <div class="bar upload-mode">
        <label><input type="radio" value="files" v-model="upMode" :disabled="uploading || tvBusy" @change="onUpModeChange" /> 多选文件</label>
        <label><input type="radio" value="dir" v-model="upMode" :disabled="uploading || tvBusy" @change="onUpModeChange" /> 整个文件夹</label>
      </div>
      <div v-if="kind === 'tv' && upMode === 'files'" class="tv-target">
        <label>所属剧 <select v-model.number="targetShow" :disabled="uploading || tvBusy || uploadCounts.done > 0">
          <option :value="0">新剧集</option><option v-for="show in availableShows" :key="show.id" :value="show.id">{{ show.title }}</option>
        </select></label>
        <label v-if="!targetShow">剧名 <input v-model="targetTitle" maxlength="200" placeholder="用于创建剧集文件夹" :disabled="uploading || tvBusy || uploadCounts.done > 0" /></label>
        <label>季号 <input v-model.number="targetSeason" type="number" min="0" max="99" :disabled="uploading || tvBusy || uploadCounts.done > 0" /></label>
        <span class="fhint">0 为特典；保留文件名，文件名季号须与所选季一致。</span>
        <p v-if="showsError" role="status">{{ showsError }}</p>
      </div>
      <div class="bar upload-picker">
        <input v-if="upMode === 'files'" type="file" multiple ref="upFiles" aria-label="选择要上传的文件" :disabled="uploading || tvBusy" @change="onUpInputChange" />
        <input v-else type="file" webkitdirectory ref="upDir" aria-label="选择要上传的文件夹" :disabled="uploading || tvBusy" @change="onUpInputChange" />
      </div>
      <div class="bar"><span class="fhint">{{ kind === 'tv' ? '选择包含剧名的完整文件夹，或指定剧和季上传散文件；无法识别的编号会提示待处理。' : '上传到视频库根，保留文件夹结构；字幕和花絮自动归属。' }}同名文件跳过不覆盖，可取消上传。</span></div>
      <div v-if="upFolderHead" class="bar"><span>已选文件夹：{{ upFolderHead }}</span></div>
      <div v-if="upQueue.length" class="bar"><span class="fhint">共 {{ upQueue.length }} 个文件 · {{ fmtBytes(upTotalSize) }}{{ upDoneCount ? ` · 已处理 ${upDoneCount}` : '' }}{{ upScanning ? ' · 当前已传完 · 刮削中…' : (upCurPct != null ? ` · 当前 ${upCurPct}%` : '') }}</span></div>
      <p v-if="upQueue.length" class="fhint">已传输 {{ fmtBytes(transferredBytes) }} / {{ fmtBytes(upTotalSize) }} · 成功 {{ uploadCounts.done }} · 跳过 {{ uploadCounts.skipped }} · 失败 {{ uploadCounts.error }}</p>
      <div v-if="upQueue.length" class="up-progress" role="progressbar" aria-label="上传进度" :aria-valuenow="upTotalPct" aria-valuemin="0" aria-valuemax="100"><div class="up-progress-fill" :style="{ width: upTotalPct + '%' }"></div></div>
      <div v-if="upScanning" class="bar"><span class="up-scan">{{ upScanHint }}</span></div>
      <ul v-if="upQueue.length" class="collist">
        <li v-for="(t, i) in visibleUpQueue" :key="i"><span :title="t.rel">{{ midEllipsis(t.rel) }}</span><span class="fhint">{{ upTaskState(t) }}</span></li>
      </ul>
      <p v-if="upQueue.length > 50" class="fhint">等共 {{ upQueue.length }} 个<span v-if="!showAllUp">（仅列前 50）</span> <JzButton v-if="!showAllUp" @click="showAllUp = true">展开全部</JzButton></p>
      <div v-if="upSummary" class="bar"><span class="fhint">{{ upSummary }}</span></div>
      <div v-if="upNeedsMatch.length" class="bar"><span class="fhint">以下需处理（共 {{ upNeedsMatch.length }} 部）：</span></div>
      <ul v-if="upNeedsMatch.length" class="collist">
        <li v-for="t in upNeedsMatch" :key="t.rel">
          <span :title="t.rel">{{ midEllipsis(t.rel) }}（{{ noteText(t) }}）</span>
          <JzButton v-if="t.movieId" @click="router.push('/m/' + t.movieId)">去详情匹配</JzButton>
          <JzButton v-if="t.movieId && canRetry(t)" @click="rescanTask(t)" :disabled="t._rescuing">
            {{ t._rescuing ? '重试中…' : '重试刮削' }}
          </JzButton>
        </li>
      </ul>
      <p v-if="kind === 'tv' && upNeedsMatch.length"><router-link :to="{path:'/settings', query:{sec:'sec-files', library:upLibId}}">检查本库文件与编号</router-link></p>
      <section v-if="kind === 'tv' && uploadedShows.length" class="tv-results">
        <h4>本次上传的剧集</h4><p v-if="tvMessage" role="status">{{ tvMessage }}</p>
        <div v-for="show in uploadedShows" :key="show.id" class="tv-result">
          <span>{{ show.title }} · {{ show.status || '已登记，待补全资料' }}</span>
          <router-link :to="'/tv/' + show.id">核对匹配</router-link>
          <JzButton :disabled="tvBusy || uploading" @click="previewTv(show.id)">预览目录整理</JzButton>
        </div>
        <JzButton v-if="!tvBusy && !uploading" @click="scrapeUploadedShows">补全剧集资料</JzButton>
        <JzButton v-if="tvBusy" @click="cancelTvMetadata">取消资料补全</JzButton>
      </section>

      </template>
      <template v-if="upStep === 'organize'">
      <div class="bar"><span class="fhint">规范命名并收敛到视频库根（按命名档平铺），仅本次上传的 {{ upOrganizableIds.length }} 部影片，先预览再执行</span></div>
      <div class="bar"><span v-if="upOrgMsg" class="upload-feedback" role="status">{{ upOrgMsg }}</span></div>
      <ul v-if="upOrgPlans.length" class="collist">
        <li v-for="p in upOrgPlans" :key="p.id"><span :title="p.from + ' → ' + p.to">{{ midEllipsis(p.from, 40) }} → {{ midEllipsis(p.to, 40) }}</span><span v-if="p.status" class="fhint">{{ p.status }}</span></li>
      </ul>
      <div v-if="upOrgConflicts.length" class="bar"><span class="fhint">冲突 {{ upOrgConflicts.length }} 项，需先去详情匹配：</span></div>
      <ul v-if="upOrgConflicts.length" class="collist">
        <li v-for="c in upOrgConflicts" :key="'c' + c.id"><span :title="(c.title || '') + ' ' + c.from">{{ midEllipsis(c.title || c.from) }}（{{ c.status }}）</span><JzButton @click="router.push('/m/' + c.id)">去详情匹配</JzButton></li>
      </ul>

      </template>
      <template v-if="upStep === 'done'">
      <div class="bar"><span class="fhint">{{ upDoneSummary }}</span></div>

      </template>
    <template #footer>
      <template v-if="upStep === 'upload'">
        <JzButton v-if="!uploading && !upFinished" variant="primary" @click="startUpload" :disabled="!canStartUpload">开始上传</JzButton>
        <JzButton v-if="uploading" @click="cancelUpload">取消上传</JzButton>
        <JzButton v-if="!uploading && uploadCounts.error" :disabled="tvBusy" @click="retryFailed">重试失败文件</JzButton>
        <JzButton v-if="kind === 'movie' && upFinished && upOrganizable" variant="primary" @click="goUpOrganize">下一步：归档整理</JzButton>
        <JzButton v-if="!uploading" :disabled="tvBusy" @click="closeDlg">{{ upFinished ? '关闭' : '取消' }}</JzButton>
        <span v-if="upMsg" class="upload-feedback" role="status">{{ upMsg }}</span>
      </template>
      <template v-else-if="upStep === 'organize'">
        <JzButton v-if="!upOrgDone" variant="primary" :loading="upOrgBusy" @click="doUpOrganize" :disabled="upOrgBusy || !upOrgPlans.length">{{ upOrgBusy ? '执行中…' : '确认搬迁' }}</JzButton>
        <JzButton @click="closeDlg" :disabled="upOrgBusy">{{ upOrgDone ? '完成' : '稍后整理' }}</JzButton>
        <span v-if="upOrgMsg" class="upload-feedback" role="status">{{ upOrgMsg }}</span>
      </template>
      <JzButton v-else @click="closeDlg">关闭</JzButton>
    </template>
  </JzDialog>
  <TvOrganizeDialog v-if="tvPlan" :key="tvPlan.show_id" :initial-plan="tvPlan" :title="tvPlan.title || ''"
    @close="tvPlan = null" @finished="emit('done')" @settings="openTvTools" />
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, onBeforeRouteLeave } from 'vue-router'
import { api, apiUpload } from '../api.js'
import { currentMediaId, currentMediaVideoLibs, listLibs, uploadTargets, onLibChange } from '../libraries.js'
import { fmtBytes, midEllipsis } from '../format.js'
import { useTvUpload } from '../useTvUpload.js'
import TvOrganizeDialog from './TvOrganizeDialog.vue'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'

const props = defineProps({ kind: { type: String, default: 'movie' }, libraryId: { type: Number, default: null } })
const emit = defineEmits(['close', 'done', 'result', 'busy'])
const router = useRouter()


const libRevision = ref(0)
const upLibCandidates = computed(() => { libRevision.value; return uploadTargets(listLibs(), currentMediaVideoLibs(props.kind), props.kind, props.libraryId) })
const upLibId = ref(_storedUploadLib())
function _storedUploadLib () {
  const cands = upLibCandidates.value
  if (props.libraryId != null) return cands[0]?.id ?? null
  try {
    const mid = currentMediaId()
    const v = mid != null ? Number(localStorage.getItem('jzmedia.uploadLib.' + props.kind + '.' + mid)) : NaN
    if (cands.some((l) => Number(l.id) === v)) return v
  } catch (e) { /* 忽略 */ }
  return cands[0]?.id ?? null
}
const upMode = ref(props.kind === 'tv' ? 'dir' : 'files')
const upFiles = ref(null)
const upDir = ref(null)
const upFolderHead = ref('')
const upQueue = ref([])
const { targetShow, targetTitle, targetSeason, availableShows, showsError, tvBusy, tvMessage,
  tvPlan, uploadedShows, scrapeUploadedShows, cancelTvMetadata, previewTv } = useTvUpload({
  kind: props.kind, libraryId: upLibId, queue: upQueue, done: () => emit('done'),
})
const uploading = ref(false)
const upMsg = ref('')
const upSummary = ref('')
const showAllUp = ref(false)
const upCurPct = ref(null)
let upAbort = null
let upCancelled = false
const upTotalSize = computed(() => upQueue.value.reduce((a, t) => a + (t.file.size || 0), 0))
const upDoneCount = computed(() => upQueue.value.filter(t => t.state === 'done' || t.state === 'skipped' || t.state === 'error').length)
const transferredBytes = computed(() => upQueue.value.reduce((sum, t) => sum + (t.transferred || 0), 0))
const uploadCounts = computed(() => Object.fromEntries(['done', 'skipped', 'error'].map(state => [state, upQueue.value.filter(t => t.state === state).length])))
const upTotalPct = computed(() => upTotalSize.value ? Math.min(100, Math.round(transferredBytes.value / upTotalSize.value * 100)) : 0)
const upFinished = computed(() => upQueue.value.length > 0 && !uploading.value && upQueue.value.every(t => t.state !== 'queued' && t.state !== 'active'))
const canStartUpload = computed(() => !uploading.value && !tvBusy.value && upLibCandidates.value.some(l => Number(l.id) === Number(upLibId.value)) && (props.kind !== 'tv' || upMode.value === 'dir' || ((targetShow.value || targetTitle.value.trim()) && Number.isInteger(targetSeason.value) && targetSeason.value >= 0 && targetSeason.value <= 99)) && upQueue.value.some(t => t.state === 'queued'))
const visibleUpQueue = computed(() => showAllUp.value ? upQueue.value : upQueue.value.slice(0, 50))
// 状态文案（评审 B9 后续）：失败可重试、未匹配可去匹配，别只丢裸状态码
const NOTE_TEXT = {
  tv_ok: '分集已登记', stored: '文件已保存', skipped_tv_unknown: '文件已保存，集号未识别，请核对文件名后扫描', extra_attached: '花絮已关联',
  ok: '已入库', ok_needs_review: '待确认', no_match: 'TMDB 未匹配',
  scan_failed: '刮削失败（可重试）', skipped_cached: '已入库', skipped_unchanged: '未变化',
  skipped_sample: '样片跳过', skipped_episode_v1: '剧集跳过',
  ok_external: '离线入库（外源元数据）', skipped_external: '已入库（离线外源）'
}
function noteText(t) {
  const n = String((t && t.note) || '')
  if (n.startsWith('stored_scan_warn')) return '刮削出错（可重试）'
  return NOTE_TEXT[n] || n || '完成'
}
function canRetry(t) {
  const n = String((t && t.note) || '')
  return n === 'no_match' || n === 'scan_failed' || n === 'ok_external'
    || n === 'skipped_external' || n.startsWith('stored_scan_warn')
}
async function rescanTask(t) {
  if (!t || !t.movieId || t._rescuing) return
  t._rescuing = true
  upMsg.value = ''
  try {
    const d = await api('/api/movies/' + t.movieId + '/rescan', { method: 'POST' })
    const st = (d && d.status) || ''
    if (st === 'ok' || st === 'ok_needs_review' || st === 'ok_external') {
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
    if (t.phase === 'scanning') return '已传完 · 识别处理中…'
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
  const base = `文件已传完，正在识别文件与补全资料（已等待 ${upScanSecs.value}s）`
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
  if (uploading.value || upOrgBusy.value || tvBusy.value) return
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
  if (!canStartUpload.value) return
  const staged = upQueue.value.length ? upQueue.value : collectStaged()
  if (!staged.length) {
    upMsg.value = upMode.value === 'dir' ? '先选择文件夹' : '先选择文件'
    return
  }
  try {
    const mid = currentMediaId()
    if (props.libraryId == null && mid != null && upLibId.value != null) {
      localStorage.setItem('jzmedia.uploadLib.' + props.kind + '.' + mid, String(upLibId.value))
    }
  } catch (e) { /* 忽略 */ }
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
    t.transferred = 0
    t.phase = 'uploading'
    t.scanStartedAt = 0
    upCurPct.value = 0
    const h = apiUpload('/api/uploads', t.file, {
      fields: { relpath: t.rel, library_id: upLibId.value, media_type: props.kind, mode: upMode.value, ...(props.kind === 'tv' && upMode.value === 'files' ? { show_id: targetShow.value || undefined, show_title: targetShow.value ? '' : targetTitle.value.trim(), season: targetSeason.value } : {}) },
      onProgress: (p, bytes) => { upCurPct.value = p; t.transferred = bytes ?? Math.round(t.file.size * p / 100) },
      onUploaded: () => {
        t.transferred = t.file.size
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
      t.episodeId = r?.episode_id || null
      t.showId = r?.show_id || null
      t.showTitle = r?.show || availableShows.value.find(s => s.id === t.showId)?.title || targetTitle.value || t.rel.split('/')[0]
      if (r?.error) t.note += ': ' + r.error
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
      } else if (/^409\b.*already exists/.test(m)) {
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
  const parts = [`${upCancelled ? '上传已取消' : '上传处理结束'}：成功 ${ok}`]
  if (skipped) parts.push(`跳过 ${skipped}`)
  if (nomatch) parts.push(`未匹配 ${nomatch}`)
  if (review) parts.push(`待确认 ${review}`)
  if (scanfail) parts.push(`刮削失败 ${scanfail}（可重试）`)
  if (failed) parts.push(`失败 ${failed}`)
  if (upCancelled) parts.push('已取消；已送达服务器的文件可能仍在入库，可扫描确认')
  upSummary.value = parts.join(' · ')
  emit('done')
  if (props.kind === 'tv' && !upCancelled && uploadedShows.value.length) await scrapeUploadedShows()
  window.dispatchEvent(new CustomEvent('jzmedia:content-changed'))
  emit('result', {
    library_id: upLibId.value, kind: props.kind, uploaded: uploadCounts.value.done,
    skipped: uploadCounts.value.skipped, failed: uploadCounts.value.error, cancelled: upCancelled,
    movie_ids: upQueue.value.map(t => t.movieId).filter(Boolean),
    episode_ids: upQueue.value.map(t => t.episodeId).filter(Boolean),
  })
}
function retryFailed() {
  upQueue.value.filter(t => t.state === 'error').forEach(t => { t.state = 'queued'; t.note = ''; t.transferred = 0 })
  startUpload()
}
function cancelUpload() {
  upCancelled = true
  if (upAbort) upAbort()
}
// 归档整理（第 2 步）：仅本次上传影片，规范命名并收敛到视频库根，先预览再执行
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
    || s === 'skipped_tv_unknown' || s.startsWith('stored_scan_warn')
}))
const upOrganizableIds = computed(() => [...new Set(
  upQueue.value.filter(t => t.movieId).map(t => t.movieId))])
const upOrganizable = computed(() => upOrganizableIds.value.length > 0)
function upOrgBody(dry_run) {
  return JSON.stringify({ mode: 'relocate', library_id: upLibId.value,
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
    upDoneSummary.value = `搬迁完成：${ok}/${upOrgPlans.value.length}，已归档到视频库根`
    upStep.value = 'done'
    emit('done')
  } catch (e) {
    upOrgMsg.value = '执行失败：' + e.message
  } finally {
    upOrgBusy.value = false
  }
}

function openTvTools() { router.push({ path: '/settings', query: { sec: 'sec-tvorganize', library: upLibId.value } }) }
watch(() => uploading.value || tvBusy.value || upOrgBusy.value, value => emit('busy', value), { flush: 'sync' })
function beforeUnload(event) {
  if (uploading.value || tvBusy.value || upOrgBusy.value) { event.preventDefault(); event.returnValue = '' }
}
let unsubscribe
onBeforeRouteLeave(() => {
  if (uploading.value || tvBusy.value || upOrgBusy.value) { upMsg.value = '请先取消当前任务，再离开上传窗口。'; return false }
})
onMounted(() => {
  unsubscribe = onLibChange(() => { libRevision.value++; if (!uploading.value && !tvBusy.value) upLibId.value = _storedUploadLib() })
  window.addEventListener('beforeunload', beforeUnload)
})
onUnmounted(() => {
  upCancelled = true
  upAbort?.()
  stopScanTicker()
  upQueue.value.forEach(t => clearTimeout(t._hintTimer))
  unsubscribe?.()
  window.removeEventListener('beforeunload', beforeUnload)
})
</script>

<style scoped>
.fhint { color: var(--jz-text-dim); font-size: 0.75rem; }
.collist { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 30vh; overflow: auto; }
.collist li { overflow-wrap: anywhere; display: flex; justify-content: space-between; gap: 8px; align-items: center; background: var(--jz-surface-2); border-radius: var(--jz-radius-s); padding: 6px 10px; }
.up-scan { color: var(--jz-warn); font-size: 0.8125rem; }
.up-progress { height: 8px; border-radius: 999px; background: var(--jz-surface-3); overflow: hidden; margin: 0 12px; }
.up-progress-fill { height: 100%; background: var(--jz-accent); border-radius: 999px;  }
</style>

<style scoped>
.bar { padding: 10px 0; }
.bar, .tv-target, .tv-result { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
label { display: flex; gap: 8px; align-items: center; max-width: 100%; }
select, input { min-width: 0; max-width: 100%; box-sizing: border-box; }
.upload-target label { flex: 1; flex-direction: column; align-items: stretch; font-weight: 600; }
.upload-target select { width: 100%; font-weight: 400; }
.upload-mode { border-bottom: 1px solid var(--jz-border); margin-bottom: 8px; }
.upload-mode label { min-height: 44px; padding-right: 16px; cursor: pointer; }
.upload-picker input { width: 100%; padding: 20px 16px; border: 1px dashed var(--jz-border-strong); background: var(--jz-bg); }
.upload-picker input::file-selector-button { border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-s); background: var(--jz-surface-3); color: var(--jz-text); padding: 10px 12px; margin-right: 12px; font: inherit; cursor: pointer; }
.upload-feedback { flex-basis: 100%; color: var(--jz-text-dim); font-size: var(--jz-font-m); overflow-wrap: anywhere; }
@media (max-width: 600px) { .tv-target label { flex-wrap: wrap; } .collist li { flex-wrap: wrap; } .collist li > span:first-child { min-width: 0; } }
.tv-target { padding: 12px; }
.tv-target input[type="number"] { width: 72px; }
.tv-target .fhint, .tv-result > span { flex-basis: 100%; }
.tv-results { overflow-wrap: anywhere; padding: 12px; border-top: 1px solid var(--jz-border); }
a { color: var(--jz-link); }
</style>
