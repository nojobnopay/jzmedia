import { computed, ref } from 'vue'
import { api } from './api.js'

export function castStateLabel(session) {
  if (!session) return ''
  return ({ PLAYING: '电视正在播放', PAUSED_PLAYBACK: '电视已暂停', STOPPED: '投屏已停止',
    NO_MEDIA_PRESENT: '电视未加载视频', REPLACED: '电视已切换到其他视频',
    DISCONNECTED: '与电视的连接已中断', MEDIA_REMOVED: '影片已从媒体库移除',
    TRANSITIONING: '已发送播放请求，等待电视状态' })[session.state] || '等待电视上报状态'
}

// Independent requests/generations: closing the picker cancels discovery, never stops the TV.
export function useCasting({ media, position, beforePlay = async () => {}, onSession = () => {}, request = api }) {
  const open = ref(false), searching = ref(false), busy = ref(false)
  const devices = ref([]), selected = ref(''), serverUrl = ref('')
  const session = ref(null), error = ref(''), searched = ref(false)
  let disposed = false, searchGeneration = 0, searchController = null, pollTimer = 0
  let pollController = null, restoreController = null
  let sessionEpoch = 0
  const active = computed(() => !!session.value?.active)
  const stateLabel = computed(() => castStateLabel(session.value))

  function accept(value) {
    if (disposed) return
    session.value = value
    if (value?.active) onSession(value)
    clearTimeout(pollTimer)
    if (value?.active) pollTimer = setTimeout(poll, 3000)
  }
  async function poll() {
    if (disposed || !active.value) return
    const sid = session.value.id
    const ctrl = new AbortController()
    pollController = ctrl
    try {
      const value = await request(`/api/cast/sessions/${encodeURIComponent(sid)}`, { signal: ctrl.signal })
      if (!disposed && !ctrl.signal.aborted && session.value?.id === sid) accept(value)
    } catch (e) {
      if (!disposed && !ctrl.signal.aborted && session.value?.id === sid) {
        error.value = e.message
        if (String(e.message).startsWith('404')) {
          session.value = { ...session.value, active: false, state: 'DISCONNECTED', position_known: false }
        } else pollTimer = setTimeout(poll, 5000)
      }
    } finally { if (pollController === ctrl) pollController = null }
  }
  async function restore() {
    const epoch = sessionEpoch
    const item = media()
    const ctrl = new AbortController()
    restoreController = ctrl
    try {
      const value = await request(`/api/cast/current?version_id=${item.version_id}&kind=${item.kind}`, { signal: ctrl.signal, timeout: 5000 })
      if (!disposed && epoch === sessionEpoch && value.session) accept(value.session)
    } catch { /* A failed read must not prevent ordinary local playback. */ }
    finally { if (restoreController === ctrl) restoreController = null }
  }
  async function discover() {
    searchController?.abort()
    const generation = ++searchGeneration
    const ctrl = new AbortController()
    searchController = ctrl
    searching.value = true
    searched.value = false
    error.value = ''
    try {
      const result = await request('/api/cast/discover', { method: 'POST', signal: ctrl.signal, timeout: 30000 })
      if (disposed || generation !== searchGeneration) return
      devices.value = result.items || []
      if (!devices.value.some(device => device.id === selected.value)) selected.value = ''
      if (!serverUrl.value && result.base_url) serverUrl.value = result.base_url
      searched.value = true
    } catch (e) {
      if (!disposed && generation === searchGeneration) error.value = e.message
    } finally {
      if (!disposed && generation === searchGeneration) searching.value = false
    }
  }
  function show() {
    if (disposed) return
    open.value = true
    if (!active.value) discover()
  }
  function close() {
    if (busy.value) return
    open.value = false
    searchGeneration++
    searchController?.abort()
    searching.value = false
  }
  async function play() {
    if (disposed || busy.value || searching.value || !selected.value) return
    busy.value = true
    sessionEpoch++
    error.value = ''
    try {
      await beforePlay()
      if (disposed) return
      const value = await request('/api/cast/sessions', { method: 'POST', body: JSON.stringify({
        ...media(), device_id: selected.value, base_url: serverUrl.value, position: Math.max(0, Number(position()) || 0),
      }), timeout: 60000 })
      // Playback was explicitly requested and continues if the page is unloaded during the request.
      if (!disposed) accept(value)
    } catch (e) { if (!disposed) error.value = e.message }
    finally { if (!disposed) busy.value = false }
  }
  async function control(command, value) {
    if (disposed || busy.value || !active.value) return false
    const sid = session.value.id
    busy.value = true
    error.value = ''
    pollController?.abort()
    clearTimeout(pollTimer)
    try {
      const result = await request(`/api/cast/sessions/${encodeURIComponent(sid)}/control`, {
        method: 'POST', body: JSON.stringify({ command, ...(value == null ? {} : { value }) }), timeout: 30000,
      })
      if (!disposed && session.value?.id === sid) accept(result)
      return true
    } catch (e) {
      if (!disposed) {
        error.value = e.message
        pollTimer = setTimeout(poll, 3000)
      }
      return false
    } finally { if (!disposed) busy.value = false }
  }
  function dispose() {
    disposed = true
    searchGeneration++
    searchController?.abort()
    restoreController?.abort()
    pollController?.abort()
    clearTimeout(pollTimer)
  }
  function forget() {
    sessionEpoch++
    clearTimeout(pollTimer)
    pollController?.abort()
    session.value = null
  }
  return { open, searching, busy, devices, selected, serverUrl, session, error, searched,
    active, stateLabel, restore, discover, show, close, play, control, forget, dispose }
}
