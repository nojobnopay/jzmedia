import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from './api.js'
import { ACTION_HELP, hintExecBody } from './tvOrganizePlans.js'

// A preview is executable only for the exact options that produced it.
export function useTvOrganizeDialog(initialPlan, {
  request = api, previewDelay = 180, pollDelay = 1200, onFinished = () => {},
} = {}) {
  const showId = Number(initialPlan.show_id)
  const keys = Object.keys(ACTION_HELP)
  const selected = ref((initialPlan.params?.actions || keys).filter(k => keys.includes(k)))
  const allowAbsolute = ref(false)
  const plan = ref(initialPlan)
  const phase = ref('preview')
  const loading = ref(false)
  const ready = ref(true)
  const error = ref('')
  const pollError = ref('')
  const done = ref(0)
  const total = ref(0)
  const result = ref(null)
  const execution = ref(hintExecBody(initialPlan, selected.value, false))
  let disposed = false, generation = 0, previewTimer = null, pollTimer = null, controller = null
  let jobId = ''
  const running = computed(() => phase.value === 'running')
  const canProceed = computed(() => ready.value && !loading.value
    && ['preview', 'confirm'].includes(phase.value) && selected.value.length > 0
    && plan.value?.needs && !plan.value.blocked && plan.value.reason !== 'read_only'
    && execution.value.ids.length === 1 && execution.value.ids[0] === showId
    && execution.value.actions.length > 0)

  function invalidate() {
    clearTimeout(previewTimer)
    controller?.abort()
    generation++
    ready.value = false
    phase.value = 'preview'
    result.value = null
    error.value = ''
    loading.value = selected.value.length > 0
  }
  async function refresh() {
    if (disposed || running.value) return
    invalidate()
    if (!selected.value.length) return
    const current = generation
    const actions = [...selected.value]
    const allow = allowAbsolute.value
    controller = new AbortController()
    const query = new URLSearchParams({ actions: actions.join(',') })
    if (allow) query.set('allow_absolute', 'true')
    try {
      const next = await request(`/api/tv/shows/${showId}/organize-hint?${query}`, { signal: controller.signal })
      if (disposed || current !== generation) return
      plan.value = next
      execution.value = hintExecBody(next, actions, allow)
      ready.value = true
    } catch (e) {
      if (!disposed && current === generation) error.value = '预览更新失败：' + e.message
    } finally {
      if (!disposed && current === generation) loading.value = false
    }
  }
  watch([selected, allowAbsolute], () => {
    if (disposed || running.value) return
    invalidate()
    if (selected.value.length) previewTimer = setTimeout(refresh, previewDelay)
  }, { deep: true, flush: 'sync' })

  async function poll() {
    if (disposed || !running.value) return
    try {
      const job = await request('/api/jobs/tv-organize/' + jobId)
      if (disposed) return
      pollError.value = ''
      done.value = Number(job.done) || 0
      total.value = Number(job.total) || 0
      if (job.state === 'done') {
        result.value = job.summary || {}
        phase.value = 'done'
        ready.value = false
        onFinished(result.value)
      } else if (job.state === 'failed' || job.state === 'cancelled') {
        error.value = job.error || (job.state === 'cancelled' ? '整理任务已取消' : '整理任务失败')
        phase.value = 'failed'
        ready.value = false
      }
    } catch (e) {
      if (!disposed) pollError.value = '暂时无法获取进度，正在重试：' + e.message
    }
    if (!disposed && running.value) pollTimer = setTimeout(poll, pollDelay)
  }
  async function proceed() {
    if (disposed || !canProceed.value) return
    if (phase.value === 'preview') { phase.value = 'confirm'; return }
    phase.value = 'running'
    done.value = 0
    total.value = 0
    error.value = ''
    pollError.value = ''
    try {
      const job = await request('/api/jobs/tv-organize', {
        method: 'POST', body: JSON.stringify(execution.value),
      })
      if (disposed) return
      if (!job.job_id) throw new Error('服务器未返回任务编号')
      jobId = job.job_id
      poll()
    } catch (e) {
      if (!disposed) {
        error.value = '未能启动整理：' + e.message
        phase.value = 'failed'
        ready.value = false
      }
    }
  }
  onScopeDispose(() => {
    disposed = true
    generation++
    clearTimeout(previewTimer)
    clearTimeout(pollTimer)
    controller?.abort()
  })
  return { selected, allowAbsolute, plan, phase, loading, ready, error, pollError,
    done, total, result, running, canProceed, refresh, proceed }
}
