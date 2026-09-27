import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from './api.js'

export function useTvUpload({ kind, libraryId, queue, done }) {
  const targetShow = ref(0)
  const targetTitle = ref('')
  const targetSeason = ref(1)
  const availableShows = ref([])
  const showsError = ref('')
  const tvBusy = ref(false)
  const tvMessage = ref('')
  const tvPlan = ref(null)
  const tvResults = ref({})
  const uploadedShows = computed(() => [...new Map(queue.value.filter(t => t.showId).map(t =>
    [t.showId, { id: t.showId, title: t.showTitle, status: tvResults.value[t.showId] }])).values()])
  let jobId = ''
  let timer
  let disposed = false
  let generation = 0
  async function loadShows() {
    const current = ++generation
    availableShows.value = []
    targetShow.value = 0
    showsError.value = ''
    if (kind !== 'tv' || !libraryId.value) return
    try {
      for (let offset = 0; ; offset += 200) {
        const d = await api(`/api/tv/shows?library=${libraryId.value}&limit=200&offset=${offset}`)
        if (disposed || current !== generation) return
        availableShows.value.push(...(d.items || []))
        if (!d.has_more || !d.items?.length) break
      }
    } catch (e) { if (current === generation) showsError.value = '剧集列表加载失败：' + e.message }
  }
  watch(libraryId, loadShows)
  const statuses = { ok: '资料已处理，请核对匹配', ok_needs_review: '匹配待确认', no_match: '未匹配，请手动核对',
    ok_offline: '已使用离线资料', ok_external: '已使用外部资料', skipped_cached: '资料已存在' }
  async function poll() {
    if (disposed || !jobId) return
    try {
      const job = await api('/api/jobs/tv-scrape/' + jobId)
      if (disposed) return
      if (job.state === 'running') tvMessage.value = `正在补全资料 ${job.done || 0}/${job.total || '…'}`
      else {
        tvBusy.value = false
        tvMessage.value = job.state === 'done' ? '资料处理结束，请核对以下匹配结果。' : job.state === 'cancelled' ? '资料补全已取消，上传文件已保留。' : '资料补全失败：' + (job.error || '未知错误')
        for (const result of job.summary?.results || []) {
          if (result.status === 'ok_backfilled' || result.status === 'no_cache_credits') continue
          tvResults.value[result.show_id] = result.needs_review ? '匹配待确认，请手动核对'
            : String(result.status).startsWith('error') ? '资料补全失败，请检查资料来源后重试'
              : statuses[result.status] || result.status
        }
        done()
        return
      }
    } catch (e) { tvMessage.value = '资料进度暂不可用，正在重试：' + e.message }
    if (!disposed) timer = setTimeout(poll, 1500)
  }
  async function scrapeUploadedShows() {
    if (tvBusy.value || disposed || !uploadedShows.value.length) return
    tvBusy.value = true
    tvMessage.value = '正在启动资料补全…'
    try {
      const d = await api('/api/jobs/tv-scrape', { method: 'POST', body: JSON.stringify({ library_id: libraryId.value, ids: uploadedShows.value.map(s => s.id), force: true }) })
      jobId = d.job_id
      await poll()
    } catch (e) { tvBusy.value = false; tvMessage.value = '资料补全未完成，可重试：' + e.message }
  }
  async function cancelTvMetadata() {
    if (!jobId) return
    try { await api(`/api/jobs/tv-scrape/${jobId}/cancel`, { method: 'POST' }) }
    catch (e) { tvMessage.value = '取消失败：' + e.message }
  }
  async function previewTv(id) {
    try {
      const plan = await api(`/api/tv/shows/${id}/organize-hint`)
      if (!disposed) tvPlan.value = plan
    } catch (e) { tvMessage.value = '预览失败：' + e.message }
  }
  onMounted(loadShows)
  onUnmounted(() => { disposed = true; generation++; clearTimeout(timer) })
  return { targetShow, targetTitle, targetSeason, availableShows, showsError, tvBusy, tvMessage,
    tvPlan, uploadedShows, scrapeUploadedShows, cancelTvMetadata, previewTv }
}
