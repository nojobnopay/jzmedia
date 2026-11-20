import { computed, nextTick, onScopeDispose, reactive, watch } from 'vue'
import { api } from './api.js'
import { mediaSelectionRevision } from './libraries.js'

// Keep the current list only in this tab. The viewing lifecycle clears it on lock.
let savedList = null
export function clearAiDiscovery() { savedList = null }

export function discoveryFilters(selection) {
  const out = {}
  for (const [key, field] of Object.entries({ genres: 'genre', regions: 'region', countries: 'country',
    years: 'year', decades: 'decade', tags: 'tag', status: 'status' })) {
    if (!selection[key]?.length) continue
    out[field] = key === 'years' || key === 'decades' ? selection[key].map(Number) : [...selection[key]]
  }
  if (selection.watched != null) out.watched = selection.watched
  if (selection.rating != null) {
    out.min_rating = selection.rating
    out.rating_source = selection.ratingSource
  }
  return out
}

export function useAiDiscovery({ kind, mediaId, ordinaryQuery, selection, route, viewing, request = api }) {
  const inputId = (kind === 'tv' ? 'tv' : 'movie') + '-browse-input'
  const fresh = () => ({ open: false, draft: '', question: '', initialQuestion: '', result: null, error: '', errorCode: '',
    busy: false, refreshing: false, source: 'library', kinds: 'auto', inherit: false, focusKey: '',
    wallTop: 0, scrollTop: 0, lastAttempt: '', lastSource: 'library', lastReuse: false, refreshSession: '', expired: false })
  const state = reactive(fresh())
  let generation = 0, controller = null, disposed = false
  function scopeKey() {
    return JSON.stringify([kind, mediaId.value, mediaSelectionRevision(), route.fullPath, viewing?.mode, viewing?.generation, viewing?.revision])
  }
  function allowed() {
    return viewing?.mode !== 'children' && !viewing?.locallyLocked && !viewing?.suspended
  }
  function cancel() {
    generation++
    controller?.abort()
    controller = null
    state.busy = false
    state.refreshing = false
  }
  function focusInput(reveal = false) {
    void nextTick(() => {
      const input = globalThis.document?.getElementById?.(inputId)
      if (reveal) input?.scrollIntoView?.({ block: 'nearest' })
      input?.focus({ preventScroll: true })
    })
  }
  function open({ reveal = false } = {}) {
    if (state.open || !allowed()) return
    clearAiDiscovery()
    Object.assign(state, fresh(), { open: true, draft: ordinaryQuery.value, wallTop: globalThis.window?.scrollY || 0 })
    focusInput(reveal)
  }
  function close(restore = true) {
    const top = state.wallTop
    cancel()
    clearAiDiscovery()
    Object.assign(state, fresh())
    if (restore) {
      focusInput()
      void nextTick(() => globalThis.window?.scrollTo?.({ top, behavior: 'instant' }))
    }
  }
  function restart() {
    cancel()
    clearAiDiscovery()
    Object.assign(state, fresh(), { open: true, wallTop: state.wallTop })
    focusInput(true)
  }
  function changeDraft(value) {
    if (state.busy && !state.refreshing) cancel()
    state.draft = value
  }
  const input = computed({
    get: () => state.open ? state.draft : ordinaryQuery.value,
    set: value => { if (state.open) changeDraft(value); else ordinaryQuery.value = value },
  })
  function fill(value) {
    changeDraft(value)
    focusInput(true)
  }
  function basePayload() {
    const payload = { kind }
    if (mediaId.value != null) payload.media_library_id = mediaId.value
    if (state.kinds !== 'auto') payload.kinds = state.kinds === 'both' ? ['movie', 'tv'] : [state.kinds]
    return payload
  }
  async function run(payload, refresh = false) {
    if (!allowed() || !state.open || disposed || state.busy) return false
    const turn = ++generation, scope = scopeKey()
    controller = new AbortController()
    state.error = state.errorCode = ''
    state.busy = true
    state.refreshing = refresh
    const current = () => !disposed && generation === turn && scope === scopeKey() && allowed()
    try {
      const result = await request('/api/ai/search/results' + (refresh ? '/refresh' : ''), {
        method: 'POST', signal: controller.signal, timeout: 130000, body: JSON.stringify(payload),
      })
      if (!current()) return false
      if (!result?.ok) throw Object.assign(new Error(result?.message || '智能搜索暂时不可用。'), { code: result?.code })
      state.result = result
      if (result.source) state.source = result.source
      state.refreshSession = ''
      if (!refresh) {
        if (!state.initialQuestion) state.initialQuestion = payload.q
        state.question = payload.q
        state.draft = ''
      }
      return true
    } catch (error) {
      if (!current()) return false
      state.error = error.message || '智能搜索失败，请重试。'
      state.errorCode = error.code || ''
      state.expired = error.code === 'session_expired'
      return false
    } finally {
      if (turn === generation) { controller = null; state.busy = false; state.refreshing = false }
    }
  }
  async function submit(text = state.draft, source = state.source, reusePlan = false) {
    const q = text.trim()
    if (!q || state.busy || state.expired) return
    state.lastAttempt = q
    state.lastSource = source
    state.lastReuse = reusePlan
    const payload = { ...basePayload(), q, source }
    if (state.result?.session_id) payload.session_id = state.result.session_id
    if (reusePlan) payload.reuse_plan = true
    if (state.inherit) payload.filters = discoveryFilters(selection.value)
    await run(payload)
  }
  function remember(key) {
    state.focusKey = key
    state.scrollTop = globalThis.window?.scrollY || 0
    // Keep a plain snapshot: unmounting aborts any in-flight request.
    savedList = { scope: scopeKey(), ordinaryQuery: ordinaryQuery.value, value: JSON.parse(JSON.stringify({ ...state,
      busy: false, refreshing: false, error: '', errorCode: '' })) }
  }
  function ask(text) {
    focusInput(true)
    return submit(text)
  }
  function searchPublic() {
    if (state.result?.source !== 'library') return
    focusInput(true)
    return submit('按刚才的要求查公开片目', 'public', true)
  }
  function retry() {
    if (state.refreshSession) return run({ ...basePayload(), session_id: state.refreshSession }, true)
    return submit(state.lastAttempt, state.lastSource, state.lastReuse)
  }
  async function restore() {
    if (!allowed() || !savedList || savedList.scope !== scopeKey()) return false
    const previous = savedList.value
    ordinaryQuery.value = savedList.ordinaryQuery
    clearAiDiscovery()
    Object.assign(state, previous)
    if (state.result?.session_id) {
      state.refreshSession = state.result.session_id
      // Confirm availability before rendering links or showing a stale private title.
      state.result = null
      const ok = await run({ ...basePayload(), session_id: state.refreshSession }, true)
      if (!ok && !state.expired && state.open && !state.error) return false
    }
    if (disposed || !state.open || !allowed()) return false
    await nextTick()
    const links = globalThis.document?.querySelectorAll('[data-ai-result-link]') || []
    const card = [...links].find(el => el.dataset.aiResultLink === state.focusKey)
    card?.focus({ preventScroll: true })
    globalThis.window?.scrollTo?.({ top: state.scrollTop, behavior: 'instant' })
    return true
  }
  const scope = () => [mediaId.value, viewing?.mode, viewing?.generation, viewing?.revision, viewing?.locallyLocked]
  watch(scope, () => {
    if (state.open || savedList && savedList.scope !== scopeKey()) close(false)
  }, { flush: 'sync' })
  watch(() => route.fullPath, () => {
    if (state.open && route.path === (kind === 'tv' ? '/tv' : '/')) close(false)
  }, { flush: 'sync' })
  watch(() => viewing?.suspended, paused => { if (paused) cancel() }, { flush: 'sync' })
  onScopeDispose(() => { disposed = true; cancel() })
  return { state, input, open, close, restart, cancel, fill, changeDraft, submit, ask, searchPublic, retry, remember, restore }
}
