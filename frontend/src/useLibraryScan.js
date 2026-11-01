import { computed, onMounted, onUnmounted, reactive, watch } from 'vue'
import { api } from './api.js'
import { tvIngestFlow } from './useTvIngestFlow.js'
import { fileReconciliationText, hasMetadataIssues, ingestionMetadataResults } from './tvIngestFeedback.js'
import { scanCleanupFollowups } from './scanCleanupFollowups.js'

const shared = reactive({ job: null, error: '', starting: false })
let subscribers = 0
let timer = null
let pending = null
let reviewedScan = ''
const scanReviewScopes = new Map()
export function scanApplies(job, library) {
  if (!job?.job_id || !library) return false
  if (job.library_id != null) return Number(job.library_id) === Number(library.id)
  return job.media_library_id == null || Number(job.media_library_id) === Number(library.media_library_id || library.media_id)
}
export function scanSummary(job, library) {
  if (!scanApplies(job, library)) return ''
  if (job.state === 'running') return `扫描中 ${job.done || 0}/${job.total || '…'}`
  if (job.state === 'failed') return '扫描失败：' + (job.error || '未知错误')
  if (job.state === 'cancelled') return `已取消（已处理 ${job.done || 0} 项）`
  if (job.state !== 'done') return ''
  const sum = job.summary || {}
  const c = sum.by_library?.[library.id] || sum.counts || {}
  if (c.library_offline) return '库离线，已跳过扫描'
  const parts = [`新增/更新 ${(c.ok || 0) + (c.ok_needs_review || 0) + (c.tv_ok || 0)}`,
    `未匹配 ${(c.no_match || 0) + (c.skipped_tv_unknown || 0)}`]
  const failed = (c.scan_failed || 0) + (sum.errors || []).length
  if (failed) parts.push(`失败 ${failed}`)
  if (sum.tv_scrape?.error) parts.push('剧集资料补全失败：' + sum.tv_scrape.error)
  if (sum.tv_scrape?.skipped) parts.push('资料补全已有任务在运行')
  if (sum.tv_ingest?.error) parts.push('剧集入库检查失败：' + sum.tv_ingest.error)
  const metadata = ingestionMetadataResults(sum.tv_ingest, library.id)
  const files = fileReconciliationText(metadata, c)
  if (files) parts.push(files)
  const filled = metadata.reduce((count, result) => count + (Number(result.filled) || 0), 0)
  if (filled) parts.push(`已补全 ${filled} 集资料`)
  const incomplete = metadata.filter(hasMetadataIssues).length
  if (incomplete) parts.push(`资料待补全 ${incomplete} 部，可在整理预览中重试`)
  return '扫描完成：' + parts.join('，')
}
async function refresh() {
  if (pending) return pending
  pending = (async () => {
    try {
      const previous = shared.job
      shared.job = await api('/api/jobs/scan')
      shared.error = ''
      scanCleanupFollowups.record(shared.job)
      if (previous?.state === 'running' && previous.job_id === shared.job.job_id
        && shared.job.state === 'done' && shared.job.summary?.tv_ingest?.preview_id && reviewedScan !== shared.job.job_id) {
        reviewedScan = shared.job.job_id
        const valid = scanReviewScopes.get(shared.job.job_id)
        if (valid) tvIngestFlow.enqueue(shared.job.summary.tv_ingest, { automatic: true, valid })
        scanReviewScopes.delete(shared.job.job_id)
      }
      if (['done', 'failed', 'cancelled'].includes(shared.job.state)) scanReviewScopes.delete(shared.job.job_id)
    } catch (e) { shared.error = '扫描状态暂不可用：' + e.message }
    finally { pending = null }
  })()
  return pending
}
function schedule() {
  clearTimeout(timer)
  if (subscribers) timer = setTimeout(async () => { await refresh(); schedule() }, 1500)
}
export function useLibraryScan(library, onDone = () => {}, { isCurrent = () => true } = {}) {
  const running = computed(() => scanApplies(shared.job, library()) && shared.job.state === 'running')
  const blocked = computed(() => shared.starting || shared.job?.state === 'running')
  const message = computed(() => shared.error || scanSummary(shared.job, library()) ||
    (shared.job?.state === 'running' ? '另一个范围的扫描正在进行，请等待完成。' : ''))
  const stateText = computed(() => running.value ? '扫描中…' : scanApplies(shared.job, library())
    ? ({ done: '完成', failed: '失败', cancelled: '已取消' }[shared.job.state] || '随时可用') : '随时可用')
  let observed = ''
  let disposed = false
  watch(() => shared.job, job => {
    if (!scanApplies(job, library())) return
    if (job.state === 'running') observed = job.job_id
    else if (observed === job.job_id) {
      observed = ''
      window.dispatchEvent(new CustomEvent('jzmedia:content-changed'))
      Promise.resolve(onDone()).catch(e => { shared.error = '刷新扫描结果失败：' + e.message })
    }
  }, { immediate: true })
  async function start() {
    const lib = library()
    if (!lib || lib.enabled === false || lib.enabled === 0 || blocked.value) return false
    shared.starting = true
    shared.error = ''
    const validContext = tvIngestFlow.captureContext()
    try {
      if (pending) await pending
      const result = await api('/api/jobs/scan', { method: 'POST', body: JSON.stringify({ library_id: lib.id }) })
      scanReviewScopes.set(result.job_id, () => !disposed && isCurrent() && validContext()
        && Number(library()?.id) === Number(lib.id))
      shared.job = { ...result, state: 'running', done: 0, total: 0 }
      await refresh()
      return true
    } catch (e) { shared.error = '扫描启动失败：' + e.message; return false }
    finally { shared.starting = false }
  }
  async function cancel() {
    if (!running.value) return
    try { await api(`/api/jobs/scan/${shared.job.job_id}/cancel`, { method: 'POST' }); await refresh() }
    catch (e) { shared.error = '取消失败：' + e.message }
  }
  onMounted(() => { subscribers++; scanCleanupFollowups.reload(); refresh().then(schedule) })
  onUnmounted(() => { disposed = true; subscribers--; if (!subscribers) clearTimeout(timer) })
  return { running, blocked, message, stateText, start, cancel }
}
