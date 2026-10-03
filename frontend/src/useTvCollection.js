import { onUnmounted, ref, watch } from 'vue'

export function useTvCollection(getShow, request) {
  const data = ref(null), error = ref(''), notice = ref(''), loading = ref(false), checking = ref(false)
  let generation = 0, controller = null, timer = null, wake = null, disposed = false
  const key = () => {
    const show = getShow()
    return show?.id ? `${show.id}:${show.tmdb_id || ''}:${show.media_library_id || ''}:${Number(show.needs_review) || 0}` : ''
  }
  function cancel() {
    generation++
    controller?.abort()
    controller = null
    clearTimeout(timer)
    wake?.()
    wake = null
    loading.value = false
    checking.value = false
    notice.value = ''
  }
  async function read(seq, identity, signal) {
    const show = getShow()
    const result = await request(`/api/tv/shows/${show.id}/collection`, { signal })
    if (disposed || seq !== generation || key() !== identity) return null
    if (result?.show_id != null && Number(result.show_id) !== Number(show.id)) return null
    if (result?.tmdb_id && show.tmdb_id && Number(result.tmdb_id) !== Number(show.tmdb_id)) return null
    if (!Array.isArray(result?.seasons)) return null
    data.value = result
    error.value = ''
    return result
  }
  async function reload() {
    cancel()
    const identity = key()
    if (!identity || disposed) { loading.value = false; return null }
    const seq = generation
    controller = new AbortController()
    loading.value = true
    error.value = ''
    try { return await read(seq, identity, controller.signal) }
    catch (e) { if (!disposed && seq === generation) error.value = e.message }
    finally { if (!disposed && seq === generation) loading.value = false }
    return null
  }
  async function check() {
    if (checking.value || !key() || disposed) return
    cancel()
    const seq = generation, identity = key(), before = Number(data.value?.checked_at) || 0
    const beforeError = data.value?.error || ''
    const ctrl = new AbortController()
    controller = ctrl
    checking.value = true
    error.value = ''
    try {
      const result = await request('/api/tv/airing/check', {
        method: 'POST', body: JSON.stringify({ show_id: Number(getShow().id) }), signal: ctrl.signal,
      })
      if (disposed || seq !== generation || key() !== identity) return
      if (result?.error) error.value = result.error
      const pending = result?.running || Number(result?.pending) > 0 || ['queued', 'running', 'pending'].includes(result?.status)
      let finished = false
      for (let i = 0; i < 6; i++) {
        const snapshot = await read(seq, identity, ctrl.signal)
        if (disposed || seq !== generation || key() !== identity) return
        if (result?.error) error.value = result.error
        const newError = snapshot?.error && snapshot.error !== beforeError
        if ((Number(snapshot?.checked_at) || 0) !== before || newError || result?.error || !pending) { finished = true; break }
        if (i < 5) await new Promise(resolve => { wake = resolve; timer = setTimeout(() => { wake = null; resolve() }, 2000) })
        if (disposed || seq !== generation || key() !== identity) return
      }
      if (!finished && !disposed && seq === generation) notice.value = '检查已排队，将在后台继续；稍后重新读取。'
    } catch (e) { if (!disposed && seq === generation) error.value = e.message }
    finally { if (!disposed && seq === generation) checking.value = false }
  }
  watch(key, () => { data.value = null; error.value = ''; reload() }, { immediate: true })
  onUnmounted(() => { disposed = true; cancel() })
  return { data, error, notice, loading, checking, reload, check }
}
