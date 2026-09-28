import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from './api.js'

export function useTvBindings(libraryId, showId = null, options = {}) {
  const request = options.request || api
  const rows = ref([]), shows = ref([]), history = ref([])
  const query = ref(''), results = ref([]), candidates = ref([]), target = ref(null)
  const plan = ref(null), undoPlan = ref(null), completed = ref(null)
  const error = ref(''), notes = ref([])
  const loading = ref(false), searching = ref(false), suggesting = ref(false)
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
  const controllers = new Set()
  async function read(url, opts = {}) {
    const controller = new AbortController()
    controllers.add(controller)
    try { return await request(url, { ...opts, signal: controller.signal }) }
    finally { controllers.delete(controller) }
  }
  const post = (url, data, cancellable = true) => (cancellable ? read : request)(url,
    { method: 'POST', body: JSON.stringify(data) })
  function invalidate() {
    ++previewGen
    plan.value = null
    undoPlan.value = null
    previewing.value = false
  }
  watch(body, invalidate, { deep: true, flush: 'sync' })
  watch(() => selected.value.map(r => r.path).join('\n'), () => {
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
      rows.value = (data.items || []).map(r => ({ ...r, checked: !!r.selected,
        season: r.binding?.season ?? '', override: !!r.binding?.override_season }))
      shows.value = data.shows || []
      history.value = journal.items || []
      const current = shows.value.find(s => s.id === Number(showId))
      if (current?.tmdb_id) target.value = { ...current, show_id: current.id }
      query.value = selected.value[0]?.query || ''
    } catch (e) { if (!disposed && gen === loadGen) error.value = e.message }
    finally { if (!disposed && gen === loadGen) loading.value = false }
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
      const data = await post('/api/tv/bindings/suggest', { library_id: Number(libraryId),
        paths: selected.value.map(r => r.path), tmdb_id: tmdbId })
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
      const data = await post('/api/tv/bindings/preview', payload)
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
      const result = await post('/api/tv/bindings/apply', { token }, false)
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
  })
  return { rows, shows, history, query, results, candidates, target, plan, undoPlan,
    completed, error, notes, loading, searching, suggesting, previewing, applying,
    replaceManual, allowDuplicates, selected, canPreview, canApply,
    load, search, suggest, choose, preview, apply, previewUndo, undo, invalidate }
}
