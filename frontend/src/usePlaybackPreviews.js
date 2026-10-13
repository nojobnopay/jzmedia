import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from './api.js'

export function usePlaybackPreviews(identity) {
  const manifest = ref(null)
  const state = ref('missing')
  const error = ref('')
  const jobId = ref('')
  const busy = computed(() => state.value === 'running')
  const ready = computed(() => state.value === 'ready')
  const status = computed(() => {
    if (error.value) return error.value
    if (busy.value) {
      if (manifest.value?.queued) return '正在等待生成此影片的预览'
      if (manifest.value?.waiting) return '正在等待播放转码结束'
      const total = Number(manifest.value?.frame_total || manifest.value?.count || 0)
      const cells = Number(manifest.value?.columns || 0) * Number(manifest.value?.rows || 0)
      const completed = Math.min(total, Math.max(Number(manifest.value?.frames || 0),
        (manifest.value?.pages?.length || 0) * cells))
      return total > 0
        ? `预览生成中：${completed}/${total} 张（${Math.floor(completed / total * 100)}%），已完成的图片可直接使用`
        : '预览生成中，正在准备抽帧'
    }
    return ({
      missing: '尚未生成进度预览', ready: '预览已生成完毕',
      cancelled: '已取消，可继续生成', failed: '生成失败，可重试',
    }[state.value] || '尚未生成进度预览')
  })
  let controller = null
  let timer = null
  let generation = 0
  let request = 0
  let disposed = false
  const base = () => {
    const { id, kind } = identity()
    return `/api/stream/${id}/previews?kind=${encodeURIComponent(kind)}`
  }
  async function load() {
    clearTimeout(timer)
    const seq = ++request
    const gen = generation
    const signal = controller.signal
    try {
      const data = await api(base(), { signal })
      if (disposed || gen !== generation || seq !== request) return
      manifest.value = data
      state.value = data.state
      jobId.value = data.job_id || ''
      error.value = data.error || ''
    } catch (e) {
      if (!disposed && gen === generation && seq === request && !signal.aborted) error.value = '预览暂不可用：' + e.message
    }
    if (!disposed && gen === generation && seq === request && busy.value) timer = setTimeout(load, 3000)
  }
  async function start() {
    if (busy.value || ready.value || disposed) return
    const seq = ++request
    const gen = generation
    error.value = ''
    state.value = 'running'
    try {
      const data = await api(base(), { method: 'POST', signal: controller.signal })
      if (disposed || gen !== generation || seq !== request) return
      jobId.value = data.job_id || ''
      clearTimeout(timer)
      await load()
    } catch (e) {
      if (disposed || gen !== generation || seq !== request) return
      error.value = e.message
      state.value = 'failed'
    }
  }
  async function cancel() {
    if (!jobId.value || disposed) return
    const seq = ++request
    const gen = generation
    try {
      await api(`/api/stream/previews/jobs/${jobId.value}/cancel`, { method: 'POST', signal: controller.signal })
      if (disposed || gen !== generation || seq !== request) return
      clearTimeout(timer)
      await load()
    } catch (e) {
      if (!disposed && gen === generation && seq === request) error.value = e.message
    }
  }
  watch(() => `${identity().kind}:${identity().id}`, () => {
    generation++
    controller?.abort()
    clearTimeout(timer)
    controller = new AbortController()
    manifest.value = null
    state.value = 'missing'
    jobId.value = ''
    error.value = ''
    load()
  }, { immediate: true })
  onBeforeUnmount(() => {
    disposed = true
    generation++
    controller?.abort()
    clearTimeout(timer)
  })
  return { manifest, status, busy, ready, start, cancel }
}
