import { computed, onScopeDispose, reactive, ref, shallowRef, watch } from 'vue'
import { api } from './api.js'
import { viewing } from './viewing.js'
import { ACTION_HELP } from './tvOrganizePlans.js'
import { fileReconciliationText, ingestionMetadataResults } from './tvIngestFeedback.js'

export const TV_INGEST_ACTIONS = Object.keys(ACTION_HELP)
const phaseText = { queued: '等待检查', discovery: '正在发现新增分集', discover: '正在发现新增分集',
  metadata: '正在补全剧集资料', preview: '正在检查目录与文件名', organize: '正在整理文件' }
const changed = () => {
  if (typeof window !== 'undefined' && window.dispatchEvent) window.dispatchEvent(new CustomEvent('jzmedia:content-changed'))
}
export function canAutomaticallyIngest() {
  return viewing.ready && viewing.mode !== 'children' && !viewing.suspended && !viewing.locallyLocked
    && (!viewing.enabled || viewing.management_unlocked)
}
export function ingestHasNotice(plan) {
  return Boolean(plan && ((plan.groups || []).length || plan.blocked || plan.absolute_risk
    || (plan.manual || []).length || plan.manual_more || (plan.conflicts || []).length
    || (plan.warnings || []).length || (plan.metadata?.issues || []).length))
}
export function normalizeIngestSummary(summary = {}, automatic = false) {
  const autoIds = summary.auto_prompt_ids?.map(Number)
  const plans = (summary.plans || []).filter(plan => ingestHasNotice(plan)
    && (!automatic || (autoIds ? autoIds.includes(Number(plan.show_id))
      : summary.auto_prompt !== false && plan.auto_prompt !== false && !['deferred', 'ignored'].includes(plan.state))))
  return { ...summary, plans, params: { actions: TV_INGEST_ACTIONS, allow_torrent: false,
    allow_absolute_shows: [], ...summary.params } }
}

// This queue owns TV ingestion reviews only. Existing focus traps arbitrate other dialogs.
export function createTvIngestFlow({ request = api, pollDelay = 1200, onChanged = changed,
  pause = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
  const review = shallowRef(null), queue = shallowRef([]), context = ref(''), allowed = ref(false), libraryIds = ref(null)
  const blockers = reactive(new Set())
  const blocked = computed(() => [...blockers].some(getter => getter()))
  let revision = 0
  const sameVersion = (left, right) => Number(left.show_id) === Number(right.show_id)
    && Boolean(left.fingerprint) && left.fingerprint === right.fingerprint
  function removeQueued(matches) {
    queue.value = queue.value.map(item => ({ ...item, plans: item.plans.filter(plan => !matches.some(match => sameVersion(plan, match))) }))
      .filter(item => item.plans.length)
  }
  function captureContext() { const captured = revision; return () => captured === revision && allowed.value }
  function setContext(key, enabled, libraries = null) {
    if (context.value !== key || allowed.value !== enabled) {
      revision++
      review.value = null
      queue.value = []
    }
    context.value = key
    allowed.value = enabled
    libraryIds.value = libraries?.map(Number) || null
  }
  function enqueue(summary, { automatic = false, valid = () => true } = {}) {
    const next = normalizeIngestSummary(summary, automatic)
    if (libraryIds.value) next.plans = next.plans.filter(plan => libraryIds.value.includes(Number(plan.library_id)))
    if (!valid() || !allowed.value || !next.preview_id || !next.plans.length) return false
    queue.value = queue.value.filter(item => item.contextRevision === revision && item.current())
    if (review.value?.preview_id === next.preview_id) return true
    next.plans = next.plans.filter(plan => !review.value?.plans.some(active => sameVersion(plan, active)))
    if (!next.plans.length) return true
    // Keep the latest unseen snapshot for each show; an already displayed draft stays intact.
    queue.value = queue.value.map(item => ({ ...item, plans: item.plans.filter(plan => !next.plans.some(incoming => Number(incoming.show_id) === Number(plan.show_id))) }))
      .filter(item => item.plans.length)
    queue.value = [...queue.value, { ...next, current: valid, contextRevision: revision }]
    return true
  }
  function takeNext() {
    if (review.value || !allowed.value || !queue.value.length) return
    queue.value = queue.value.filter(item => item.contextRevision === revision && item.current())
    if (!queue.value.length) return
    review.value = queue.value[0]
    queue.value = queue.value.slice(1)
  }
  async function prepare(body, { automatic = false, valid = () => true, progress = () => {} } = {}) {
    const started = revision
    const current = () => valid() && started === revision
    const opts = { silentViewing: automatic, silentAuth: automatic }
    const job = await request('/api/jobs/tv-ingest', { ...opts, method: 'POST', body: JSON.stringify(body) })
    if (!job.job_id) throw new Error('服务器未返回任务编号')
    while (current()) {
      const status = await request('/api/jobs/tv-ingest/' + job.job_id, opts)
      if (!current()) return null
      progress(phaseText[status.phase] || (status.state === 'queued' ? '等待检查' : '正在检查剧集'))
      if (status.state === 'done') { onChanged(); return normalizeIngestSummary(status.summary) }
      if (['failed', 'cancelled'].includes(status.state)) throw new Error(status.error || (status.state === 'cancelled' ? '检查已取消，已入库文件保留' : '检查未完成'))
      await pause(pollDelay)
    }
    return null
  }
  async function reviews(scope, includeIgnored = false, automatic = false) {
    const query = new URLSearchParams(Object.entries(scope).filter(([, value]) => value != null).map(([key, value]) => [key, String(value)]))
    if (includeIgnored) query.set('include_ignored', 'true')
    const data = await request('/api/tv/ingestion-reviews?' + query, { silentViewing: automatic, silentAuth: automatic })
    return data.items || []
  }
  async function decide(decision, ids) {
    const original = review.value
    if (!original) return
    const visibleIds = original.plans.map(plan => Number(plan.show_id))
    const chosenIds = ids?.length ? ids.map(Number).filter(id => visibleIds.includes(id)) : visibleIds
    if (!chosenIds.length) return
    await request('/api/tv/ingestion-reviews/decision', { method: 'POST', body: JSON.stringify({
      preview_id: original.preview_id, ids: chosenIds, decision,
    }) })
    removeQueued(original.plans.filter(plan => chosenIds.includes(Number(plan.show_id))))
    if (review.value === original) review.value = null
    onChanged()
  }
  function close() { review.value = null }
  function replaceReview(next) { removeQueued(next.plans || []); review.value = next }
  function addBlocker(getter) { blockers.add(getter); return () => blockers.delete(getter) }
  return { review, queue, allowed, context, setContext, enqueue, takeNext, prepare, reviews, decide, close, replaceReview, captureContext,
    blocked, addBlocker, request, changed: onChanged }
}
export const tvIngestFlow = createTvIngestFlow()

export function useTvIngestFlow({ isCurrent = () => true, scopeKey = () => '', blocked = () => false,
  onCompleted = () => {}, flow = tvIngestFlow } = {}) {
  const busy = ref(false), message = ref(''), waiting = ref(false)
  let disposed = false, generation = 0
  const removeBlocker = flow.addBlocker(blocked)
  onScopeDispose(() => { disposed = true; generation++; removeBlocker() })
  watch(scopeKey, () => { generation++; busy.value = false; message.value = '' }, { flush: 'sync' })
  const current = seq => !disposed && seq === generation && isCurrent()
  async function start(scope, { automatic = false, handoff = () => {}, ...options } = {}) {
    if (busy.value || !isCurrent() || automatic && !canAutomaticallyIngest()) return null
    const seq = ++generation
    busy.value = true
    message.value = '正在检查新增分集与资料…'
    try {
      const summary = await flow.prepare({ ...scope, source: automatic ? 'detail' : 'manual', retry_metadata: false, ...options }, {
        automatic, valid: () => current(seq), progress: value => { if (current(seq)) message.value = value + '…' },
      })
      if (!summary || !current(seq)) return null
      const results = ingestionMetadataResults(summary)
      const filled = results.reduce((sum, result) => sum + (Number(result.filled) || 0), 0)
      const metadataNote = filled ? `已补全 ${filled} 集资料；` : '资料已检查；'
      const files = fileReconciliationText(results)
      message.value = metadataNote + (files ? files + '；' : '')
        + (summary.plans.some(ingestHasNotice) ? '有待核对的资料或整理项目。' : '目录与文件名无需整理。')
      await onCompleted(summary)
      if (!current(seq)) return null
      handoff()
      waiting.value = blocked()
      flow.enqueue(summary, { automatic, valid: () => current(seq) })
      return summary
    } catch (error) {
      if (current(seq)) message.value = automatic && [401, 403].includes(error.status)
        ? '需要管理权限后才能检查资料与整理。' : '检查未完成，可重试：' + error.message
      return null
    } finally { if (current(seq)) busy.value = false }
  }
  async function pending(scope, { includeIgnored = false, handoff } = {}) {
    if (busy.value) return
    const seq = ++generation
    busy.value = true
    message.value = '正在读取整理待办…'
    let items
    try {
      items = await flow.reviews(scope, includeIgnored)
      if (!current(seq)) return
      if (!items.length) { message.value = '没有待处理的剧集。'; return }
    } catch (error) { if (current(seq)) message.value = '待办读取失败，可重试：' + error.message; return }
    finally { if (current(seq)) busy.value = false }
    if (current(seq)) return start({ ids: [...new Set(items.map(item => Number(item.show_id)))] }, { handoff })
  }
  return { busy, message, waiting, start, pending, flow, blocked: computed(blocked) }
}
