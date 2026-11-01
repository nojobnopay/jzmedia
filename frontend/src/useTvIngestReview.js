import { computed, onScopeDispose, ref, watch } from 'vue'
import { TV_INGEST_ACTIONS, tvIngestFlow } from './useTvIngestFlow.js'

export function useTvIngestReview(initialSummary, { flow = tvIngestFlow, pollDelay = 1200 } = {}) {
  const summary = ref(initialSummary), plans = computed(() => summary.value.plans || [])
  // The host has already applied library and automatic-prompt filters. Displayed
  // plans may disappear when options change, but that must not shrink this scope.
  const reviewIds = Object.freeze([...new Set(plans.value.map(plan => Number(plan.show_id)))])
  const selected = ref(plans.value.filter(plan => plan.groups?.length && !plan.blocked && plan.reason !== 'read_only').map(plan => Number(plan.show_id)))
  const actions = ref([...(initialSummary.params?.actions || TV_INGEST_ACTIONS)])
  const allowTorrent = ref(Boolean(initialSummary.params?.allow_torrent))
  const allowAbsolute = ref([...(initialSummary.params?.allow_absolute_shows || [])])
  const phase = ref('preview'), loading = ref(false), ready = ref(true), error = ref(''), pollError = ref('')
  const result = ref(null), done = ref(0), total = ref(0), deciding = ref(false), decisionFailed = ref(false)
  let disposed = false, generation = 0, timer, previewTimer, suppress = false
  const running = computed(() => phase.value === 'running')
  const busy = computed(() => running.value || loading.value || deciding.value)
  const selectedPlans = computed(() => plans.value.filter(plan => selected.value.includes(Number(plan.show_id))))
  const noWork = computed(() => phase.value === 'preview' && ready.value && !plans.value.length)
  const canConfirm = computed(() => phase.value === 'preview' && ready.value && !busy.value && actions.value.length
    && selectedPlans.value.some(plan => plan.groups?.length && !plan.blocked && plan.reason !== 'read_only'))
  async function refresh(retryMetadata = false) {
    if (disposed || running.value || deciding.value) return
    clearTimeout(previewTimer)
    const seq = ++generation
    ready.value = false
    loading.value = true
    error.value = ''
    phase.value = 'preview'
    try {
      const next = await flow.prepare({ ids: [...reviewIds], scope_preview_id: summary.value.preview_id, source: 'manual',
        retry_metadata: retryMetadata, actions: [...actions.value], allow_torrent: allowTorrent.value,
        allow_absolute_shows: [...allowAbsolute.value] }, { valid: () => !disposed && seq === generation })
      if (disposed || seq !== generation || !next) return
      summary.value = next
      // Keep the shared host on the current preview without replacing this dialog instance.
      if (flow.review.value) flow.replaceReview(next)
      ready.value = true
    } catch (failure) { if (!disposed && seq === generation) error.value = '预览更新失败：' + failure.message }
    finally { if (!disposed && seq === generation) loading.value = false }
  }
  watch([actions, allowTorrent, allowAbsolute], () => {
    if (suppress || disposed || running.value) return
    generation++
    ready.value = false
    error.value = ''
    clearTimeout(previewTimer)
    if (!actions.value.length) { loading.value = false; return }
    loading.value = true
    previewTimer = setTimeout(() => refresh(), 180)
  }, { deep: true, flush: 'sync' })
  async function confirm() {
    if (!canConfirm.value) return
    phase.value = 'running'
    error.value = ''
    pollError.value = ''
    const body = { preview_id: summary.value.preview_id, ids: selectedPlans.value.filter(plan => !plan.blocked && plan.groups?.length).map(plan => Number(plan.show_id)),
      actions: [...actions.value], allow_torrent: allowTorrent.value, allow_absolute_shows: [...allowAbsolute.value], dry_run: false }
    let job
    try {
      job = await flow.request('/api/jobs/tv-organize', { method: 'POST', body: JSON.stringify(body) })
      if (!job.job_id) throw new Error('服务器未返回任务编号')
    } catch (failure) {
      if (!disposed) {
        ready.value = false
        phase.value = 'preview'
        error.value = failure.status === 409 ? '文件或资料已变化，请重新预览后确认。' : '整理未能启动，请重新预览：' + failure.message
      }
      return
    }
    async function poll() {
      if (disposed) return
      try {
        const status = await flow.request('/api/jobs/tv-organize/' + job.job_id)
        if (disposed) return
        done.value = Number(status.done) || 0
        total.value = Number(status.total) || 0
        pollError.value = ''
        if (status.state === 'done') {
          result.value = status.summary || {}
          phase.value = 'done'
          ready.value = false
          flow.changed()
          return
        }
        if (['failed', 'cancelled'].includes(status.state)) {
          error.value = status.error || (status.state === 'cancelled' ? '整理已取消' : '整理未完成')
          phase.value = 'failed'
          ready.value = false
          return
        }
      } catch (failure) { if (!disposed) pollError.value = '进度暂不可用，正在重试：' + failure.message }
      if (!disposed) timer = setTimeout(poll, pollDelay)
    }
    poll()
  }
  async function decide(decision) {
    if (busy.value) return
    if (noWork.value) { flow.close(); return }
    deciding.value = true
    decisionFailed.value = false
    error.value = ''
    try { await flow.decide(decision) }
    catch (failure) { if (!disposed) { decisionFailed.value = true; error.value = '处理选择未保存，可重试或关闭并保留待办：' + failure.message } }
    finally { if (!disposed) deciding.value = false }
  }
  function close() {
    if (busy.value) return
    if (phase.value === 'done' || decisionFailed.value || noWork.value) flow.close()
    else decide('deferred')
  }
  function closePending() { if (!busy.value && decisionFailed.value) flow.close() }
  function setSelected(ids) { selected.value = ids.map(Number) }
  function resetOptions(params) {
    suppress = true
    actions.value = params.actions || TV_INGEST_ACTIONS
    allowTorrent.value = Boolean(params.allow_torrent)
    allowAbsolute.value = params.allow_absolute_shows || []
    suppress = false
  }
  onScopeDispose(() => { disposed = true; generation++; clearTimeout(timer); clearTimeout(previewTimer) })
  return { summary, plans, reviewIds, selected, actions, allowTorrent, allowAbsolute, phase, loading, ready, busy, running, noWork,
    error, pollError, done, total, result, decisionFailed, selectedPlans, canConfirm, refresh, confirm, decide, close, closePending, setSelected, resetOptions }
}
