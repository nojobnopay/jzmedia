import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from './api.js'

export function useTvBindings(libraryId, showId = null, options = {}) {
  const request = options.request || api
  const pollDelay = options.pollDelay ?? 700
  const rows = ref([]), shows = ref([]), history = ref([])
  const query = ref(''), results = ref([]), candidates = ref([]), target = ref(null)
  const plan = ref(null), undoPlan = ref(null), completed = ref(null)
  const error = ref(''), notes = ref([])
  const loading = ref(false), refreshing = ref(false), searching = ref(false), suggesting = ref(false)
  const previewing = ref(false), applying = ref(false)
  const replaceManual = ref(false), allowDuplicates = ref(false)
  const selected = computed(() => rows.value.filter(r => r.checked))
  const body = computed(() => ({ library_id: Number(libraryId), tmdb_id: target.value?.tmdb_id,
    target_show_id: target.value?.show_id || null,
    directories: selected.value.map(r => ({ path: r.path, season: r.season === '' ? null : Number(r.season),
      override_season: !!r.override })),
    replace_manual: replaceManual.value, allow_duplicates: allowDuplicates.value }))
  const canPreview = computed(() => !!target.value?.tmdb_id && selected.value.length > 0 &&
    !loading.value && !applying.value && !suggesting.value)
  const canApply = computed(() => !!plan.value?.can_apply && !!plan.value?.token &&
    !previewing.value && !applying.value)
  let disposed = false, loadGen = 0, searchGen = 0, suggestGen = 0, previewGen = 0
  let syncingInventory = false
  const controllers = new Set()
  const pauseTimers = new Map()
  async function read(url, opts = {}) {
    const controller = new AbortController()
    controllers.add(controller)
    try { return await request(url, { ...opts, signal: controller.signal }) }
    finally { controllers.delete(controller) }
  }
  const post = (url, data, cancellable = true) => (cancellable ? read : request)(url,
    { method: 'POST', body: JSON.stringify(data) })
  function pause(ms) {
    return new Promise(resolve => {
      const timer = setTimeout(() => { pauseTimers.delete(timer); resolve() }, ms)
      pauseTimers.set(timer, resolve)
    })
  }
  async function runJob(url, data, valid = () => true) {
    const started = await post(url, data)
    if (started.state === 'done') return started.result
    if (!started.job_id) throw new Error('后台任务未能启动，请重试')
    while (!disposed && valid()) {
      const job = await read(`/api/tv/bindings/jobs/${encodeURIComponent(started.job_id)}`, { timeout: 15000 })
      if (job.state === 'done') return job.result
      if (job.state === 'failed' || job.state === 'cancelled') {
        throw new Error(job.error || (job.state === 'cancelled' ? '任务已取消' : '后台处理失败'))
      }
      await pause(pollDelay)
    }
    throw new Error('已取消')
  }
  function setInventory(data, preserve = false) {
    const old = new Map(rows.value.map(row => [row.path, row]))
    const oldBody = preserve ? JSON.stringify(body.value) : ''
    const oldPaths = preserve ? selected.value.map(row => row.path).join('\n') : ''
    syncingInventory = true
    try {
      rows.value = (data.items || []).map(row => {
        const previous = preserve ? old.get(row.path) : null
        return { ...row,
          checked: previous ? previous.checked : !!row.selected,
          season: previous ? previous.season : (row.binding?.season ?? ''),
          override: previous ? previous.override : !!row.binding?.override_season }
      })
      shows.value = data.shows || shows.value
    } finally {
      syncingInventory = false
    }
    if (preserve && oldBody !== JSON.stringify(body.value)) invalidate()
    if (preserve && oldPaths !== selected.value.map(row => row.path).join('\n')) {
      ++suggestGen
      suggesting.value = false
      candidates.value = []
    }
  }
  function invalidate() {
    ++previewGen
    plan.value = null
    undoPlan.value = null
    previewing.value = false
  }
  watch(body, () => { if (!syncingInventory) invalidate() }, { deep: true, flush: 'sync' })
  watch(() => selected.value.map(r => r.path).join('\n'), () => {
    if (syncingInventory) return
    ++suggestGen
    suggesting.value = false
    candidates.value = []
  }, { flush: 'sync' })

  async function load() {
    const gen = ++loadGen
    loading.value = true
    error.value = ''
    try {
      const [data, journal] = await Promise.all([
        read(`/api/tv/bindings/directories?library_id=${libraryId}${showId ? `&show_id=${showId}` : ''}`),
        read(`/api/tv/bindings/history?library_id=${libraryId}`),
      ])
      if (disposed || gen !== loadGen) return
      setInventory(data)
      history.value = journal.items || []
      const current = shows.value.find(s => s.id === Number(showId))
      if (current?.tmdb_id) target.value = { ...current, show_id: current.id }
      query.value = selected.value[0]?.query || ''
      void refreshInventory(gen)
    } catch (e) { if (!disposed && gen === loadGen) error.value = e.message }
    finally { if (!disposed && gen === loadGen) loading.value = false }
  }
  async function refreshInventory(loadGeneration = loadGen) {
    if (disposed) return
    refreshing.value = true
    try {
      const data = await runJob('/api/tv/bindings/directories/refresh', {
        library_id: Number(libraryId), show_id: showId ? Number(showId) : null,
      }, () => loadGeneration === loadGen)
      if (disposed || loadGeneration !== loadGen) return
      setInventory(data, true)
      const current = shows.value.find(s => s.id === Number(showId))
      if (!target.value && current?.tmdb_id) target.value = { ...current, show_id: current.id }
      if (!query.value) query.value = selected.value[0]?.query || ''
    } catch (e) {
      if (!disposed && loadGeneration === loadGen && e.message !== '已取消') {
        notes.value = [`目录刷新：${e.message}；当前仍可使用已扫描目录`]
      }
    } finally { if (!disposed && loadGeneration === loadGen) refreshing.value = false }
  }
  async function search() {
    const term = query.value.trim()
    if (!term) return
    const gen = ++searchGen
    searching.value = true
    error.value = ''
    results.value = []
    try {
      const data = await read('/api/tv/search?q=' + encodeURIComponent(term))
      if (!disposed && gen === searchGen) results.value = (data.items || []).filter(r => r.tmdb_id)
    } catch (e) { if (!disposed && gen === searchGen) error.value = e.message }
    finally { if (!disposed && gen === searchGen) searching.value = false }
  }
  async function suggest(tmdbId = null) {
    if (!selected.value.length || selected.value.length > 12 || applying.value) return
    const gen = ++suggestGen
    suggesting.value = true
    error.value = ''
    try {
      const data = await runJob('/api/tv/bindings/suggest/start', { library_id: Number(libraryId),
        paths: selected.value.map(r => r.path), tmdb_id: tmdbId }, () => gen === suggestGen)
      if (disposed || gen !== suggestGen) return
      candidates.value = data.items || []
      notes.value = data.warnings || []
      // Fetching evidence does not alter the selected target or season choices.
    } catch (e) { if (!disposed && gen === suggestGen) error.value = e.message }
    finally { if (!disposed && gen === suggestGen) suggesting.value = false }
  }
  function choose(candidate, useSuggestions = false) {
    if (applying.value) return
    const local = shows.value.filter(s => s.tmdb_id === candidate.tmdb_id)
    target.value = { ...candidate, show_id: candidate.show_id || (local.length === 1 ? local[0].id : null) }
    if (useSuggestions) {
      for (const row of selected.value) {
        const choices = candidate.directories?.find(d => d.path === row.path)?.suggestions || []
        if (choices[0]?.score >= 3 && (!choices[1] || choices[0].score > choices[1].score)) {
          row.season = choices[0].season
        } else if (candidate.seasons?.length === 1) {
          row.season = candidate.seasons[0].season_number
        }
      }
    }
  }
  async function preview() {
    if (!canPreview.value || previewing.value) return
    const gen = ++previewGen
    plan.value = null
    completed.value = null
    error.value = ''
    previewing.value = true
    const payload = JSON.parse(JSON.stringify(body.value))
    try {
      const data = await runJob('/api/tv/bindings/preview/start', payload, () => gen === previewGen)
      if (!disposed && gen === previewGen) plan.value = data
    } catch (e) { if (!disposed && gen === previewGen) error.value = e.message }
    finally { if (!disposed && gen === previewGen) previewing.value = false }
  }
  async function apply() {
    if (!canApply.value) return
    const token = plan.value.token
    applying.value = true
    error.value = ''
    try {
      const result = await runJob('/api/tv/bindings/apply/start', { token })
      if (disposed) return
      completed.value = result
      target.value = { ...target.value, show_id: result.show_id }
      invalidate()
      options.onChanged?.(result)
      if (!disposed) await load()
    } catch (e) { if (!disposed) error.value = e.message }
    finally { if (!disposed) applying.value = false }
  }
  async function previewUndo(entry) {
    if (applying.value) return
    invalidate()
    const gen = ++previewGen
    error.value = ''
    try {
      const data = await post('/api/tv/bindings/undo', { token: entry.id, dry_run: true })
      if (!disposed && gen === previewGen) undoPlan.value = { ...data, token: entry.id }
    } catch (e) { if (!disposed && gen === previewGen) error.value = e.message }
  }
  async function undo() {
    if (!undoPlan.value || applying.value) return
    applying.value = true
    error.value = ''
    try {
      const result = await post('/api/tv/bindings/undo', { token: undoPlan.value.token, dry_run: false }, false)
      if (disposed) return
      invalidate()
      completed.value = { ...result, undone: true }
      options.onChanged?.({ ...result, undone: true })
      if (!disposed) await load()
    } catch (e) { if (!disposed) error.value = e.message }
    finally { if (!disposed) applying.value = false }
  }
  onScopeDispose(() => {
    disposed = true
    ++loadGen; ++searchGen; ++suggestGen; ++previewGen
    for (const controller of controllers) controller.abort()
    for (const [timer, resolve] of pauseTimers) {
      clearTimeout(timer)
      resolve()
    }
    pauseTimers.clear()
  })
  return { rows, shows, history, query, results, candidates, target, plan, undoPlan,
    completed, error, notes, loading, refreshing, searching, suggesting, previewing, applying,
    replaceManual, allowDuplicates, selected, canPreview, canApply,
    load, refreshInventory, search, suggest, choose, preview, apply, previewUndo, undo, invalidate }
}
