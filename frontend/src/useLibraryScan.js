import { computed, onMounted, onUnmounted, reactive, watch } from 'vue'
import { api } from './api.js'

const shared = reactive({ job: null, error: '', starting: false })
let subscribers = 0
let timer = null
let pending = null
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
  return '扫描完成：' + parts.join('，')
}
async function refresh() {
  if (pending) return pending
  pending = (async () => {
    try {
      shared.job = await api('/api/jobs/scan')
      shared.error = ''
    } catch (e) { shared.error = '扫描状态暂不可用：' + e.message }
    finally { pending = null }
  })()
  return pending
}
function schedule() {
  clearTimeout(timer)
  if (subscribers) timer = setTimeout(async () => { await refresh(); schedule() }, 1500)
}
export function useLibraryScan(library, onDone = () => {}) {
  const running = computed(() => scanApplies(shared.job, library()) && shared.job.state === 'running')
  const blocked = computed(() => shared.starting || shared.job?.state === 'running')
  const message = computed(() => shared.error || scanSummary(shared.job, library()) ||
    (shared.job?.state === 'running' ? '另一个范围的扫描正在进行，请等待完成。' : ''))
  const stateText = computed(() => running.value ? '扫描中…' : scanApplies(shared.job, library())
    ? ({ done: '完成', failed: '失败', cancelled: '已取消' }[shared.job.state] || '随时可用') : '随时可用')
  let observed = ''
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
    if (!lib || lib.enabled === false || lib.enabled === 0 || blocked.value) return
    shared.starting = true
    shared.error = ''
    try {
      if (pending) await pending
      const result = await api('/api/jobs/scan', { method: 'POST', body: JSON.stringify({ library_id: lib.id }) })
      shared.job = { ...result, state: 'running', done: 0, total: 0 }
      await refresh()
    } catch (e) { shared.error = '扫描启动失败：' + e.message }
    finally { shared.starting = false }
  }
  async function cancel() {
    if (!running.value) return
    try { await api(`/api/jobs/scan/${shared.job.job_id}/cancel`, { method: 'POST' }); await refresh() }
    catch (e) { shared.error = '取消失败：' + e.message }
  }
  onMounted(() => { subscribers++; refresh().then(schedule) })
  onUnmounted(() => { subscribers--; if (!subscribers) clearTimeout(timer) })
  return { running, blocked, message, stateText, start, cancel }
}
