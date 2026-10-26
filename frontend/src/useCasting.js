import { computed, ref } from 'vue'
import { api } from './api.js'

const SEEK_CONFIRM_TIMEOUT = 15000

export function castStateLabel(session) {
  if (!session) return ''
  return ({ PLAYING: '电视正在播放', PAUSED_PLAYBACK: '电视已暂停', STOPPED: '投屏已停止',
    WAITING_FOR_APP: '等待 jzmedia-tv 确认播放', LOADING: 'jzmedia-tv 正在加载视频',
    PREPARING: '正在准备所选音轨', CANCELLED: '投屏已取消', EXPIRED: '等待电视 App 已过期',
    NO_MEDIA_PRESENT: '电视未加载视频', REPLACED: '电视已切换到其他视频',
    DISCONNECTED: '与电视的连接已中断', MEDIA_REMOVED: '影片已从媒体库移除',
    TRANSITIONING: '已发送播放请求，等待电视状态' })[session.state] || '等待电视上报状态'
}

// Independent requests/generations: closing the picker cancels discovery, never stops the TV.
export function useCasting({ media, position, beforePlay = async () => {}, onSession = () => {},
  onPlayFailure = () => {}, onPlayUncertain = () => {}, request = api }) {
  const open = ref(false), searching = ref(false), busy = ref(false)
  const devices = ref([]), selected = ref(''), serverUrl = ref('')
  const receiverType = ref('dlna'), appDevices = ref([]), searchingApps = ref(false), appsSearched = ref(false), appsError = ref('')
  const manualLocation = ref('')
  const session = ref(null), error = ref(''), searched = ref(false)
  const uncertain = ref(false)
  const pendingSeek = ref(null)
  let disposed = false, searchGeneration = 0, searchController = null, pollTimer = 0
  let pollController = null, restoreController = null, reconcileController = null
  let pendingCreation = null
  let appTimer = 0, appController = null, appGeneration = 0, nextPosition = null
  let sessionEpoch = 0
  let seekTimer = 0
  const active = computed(() => !!session.value?.active)
  const seekPending = computed(() => active.value && !!pendingSeek.value && pendingSeek.value.sessionId === session.value?.id)
  const displayPosition = computed(() => {
    const remote = session.value
    const position = seekPending.value ? pendingSeek.value.target : remote?.position_known ? remote.position : remote?.last_position
    return Math.max(0, Math.min(Number(remote?.duration) || Infinity, Number(position) || 0))
  })
  const stateLabel = computed(() => uncertain.value ? '投屏状态待确认'
    : seekPending.value ? '正在跳转，等待电视确认' : castStateLabel(session.value))

  function clearSeek() {
    clearTimeout(seekTimer)
    pendingSeek.value = null
  }
  function waitForSeek(pending) {
    if (pendingSeek.value !== pending) return
    pending.phase = 'waiting'
    pending.waitingAt = Date.now()
    clearTimeout(seekTimer)
    seekTimer = setTimeout(() => {
      if (disposed || pendingSeek.value !== pending) return
      clearSeek()
      error.value = '电视尚未确认跳转，请核对电视画面后重试。'
    }, SEEK_CONFIRM_TIMEOUT)
  }
  function rememberStalePosition(pending, position) {
    // A near-target report is already within the receiver's seek precision.
    if (!Number.isFinite(position) || Math.abs(position - pending.target) <= 3) return
    if (!pending.stalePositions.some(previous => Math.abs(previous - position) <= 0.5)) {
      pending.stalePositions.push(position)
    }
  }
  function updateSeek(value) {
    const pending = pendingSeek.value
    if (!pending) return
    if (value?.id !== pending.sessionId || !value?.active || value.error) { clearSeek(); return }
    // An accepted command may still carry the previous position. Only a real
    // playback report near the requested target confirms it; leave session's
    // position/last_position untouched for progress saving and phone handoff.
    if (pending.phase !== 'waiting' || !value.position_known || !['PLAYING', 'PAUSED_PLAYBACK'].includes(value.state)) return
    const reported = Number(value.position)
    if (!Number.isFinite(reported)) return
    // Include time spent awaiting the command's HTTP response: the TV may
    // already have sought and continued playing before its acknowledgement.
    const elapsed = Math.max(0, Date.now() - pending.startedAt) / 1000
    const delta = reported - pending.target
    // A frozen old position must never become confirmation merely because the
    // allowance for playback since submission grows to include that number.
    if (pending.stalePositions.some(previous => Math.abs(previous - reported) <= 0.5)) return
    if (delta >= -2 && delta <= elapsed + 3) {
      if (pending.failureMessage && error.value === pending.failureMessage) error.value = ''
      clearSeek()
    } else rememberStalePosition(pending, reported)
  }

  function stopApps() {
    clearTimeout(appTimer)
    appGeneration++
    appController?.abort()
    searchingApps.value = false
  }
  async function refreshApps() {
    if (disposed) return
    clearTimeout(appTimer)
    appController?.abort()
    const generation = ++appGeneration
    const ctrl = new AbortController()
    appController = ctrl
    searchingApps.value = true
    appsError.value = ''
    try {
      const result = await request('/api/cast/apps', { signal: ctrl.signal, timeout: 5000 })
      if (disposed || ctrl.signal.aborted || generation !== appGeneration) return
      appDevices.value = result.items || []
      if (receiverType.value === 'jzmedia_tv' && !appDevices.value.some(device => device.id === selected.value)) selected.value = ''
      appsSearched.value = true
    } catch (e) {
      if (!disposed && !ctrl.signal.aborted && generation === appGeneration) {
        appsError.value = e.status === 404 ? '当前服务器尚不支持电视 App 投屏，请升级服务器和 jzmedia-tv。' : e.message
      }
    } finally {
      if (appController === ctrl) appController = null
      if (!disposed && generation === appGeneration) {
        searchingApps.value = false
        if (open.value && receiverType.value === 'jzmedia_tv' && !active.value && !uncertain.value) appTimer = setTimeout(refreshApps, 3000)
      }
    }
  }
  async function chooseReceiver(type) {
    if (disposed || busy.value || uncertain.value || !['dlna', 'jzmedia_tv'].includes(type)) return false
    if (active.value) {
      if (type === (session.value.receiver_type || 'dlna')) return true
      const target = Number(position())
      if (!await control('stop')) return false
      if (disposed) return false
      const stopped = session.value
      const finalReported = stopped?.last_position ?? (stopped?.position_known ? stopped.position : null)
      const latest = Number(finalReported ?? target)
      nextPosition = Number.isFinite(latest) ? Math.max(0, latest) : 0
    }
    receiverType.value = type
    selected.value = ''
    searchGeneration++
    searchController?.abort()
    searching.value = false
    error.value = ''
    stopApps()
    if (type === 'jzmedia_tv') await refreshApps()
    else await discover()
    return true
  }

  function accept(value) {
    if (disposed) return
    uncertain.value = false
    pendingCreation = null
    updateSeek(value)
    session.value = value
    if (value?.receiver_type) receiverType.value = value.receiver_type
    if (value?.active) stopApps()
    if (value?.active) onSession(value)
    clearTimeout(pollTimer)
    if (value?.active) pollTimer = setTimeout(poll, 3000)
    else if (open.value && receiverType.value === 'jzmedia_tv') {
      clearTimeout(appTimer)
      appTimer = setTimeout(refreshApps, 3000)
    }
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
          clearSeek()
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
  async function addManual() {
    if (disposed || busy.value || !manualLocation.value.trim()) return
    searchController?.abort()
    searchGeneration++
    searching.value = false
    busy.value = true
    error.value = ''
    try {
      const result = await request('/api/cast/devices', { method: 'POST',
        body: JSON.stringify({ location: manualLocation.value.trim() }), timeout: 15000 })
      if (disposed) return
      devices.value = [...devices.value.filter(device => device.id !== result.item.id), result.item]
      selected.value = result.item.id
      searched.value = true
      if (!serverUrl.value && result.base_url) serverUrl.value = result.base_url
    } catch (e) { if (!disposed) error.value = e.message }
    finally { if (!disposed) busy.value = false }
  }
  function show() {
    if (disposed) return
    open.value = true
    if (!active.value && !uncertain.value) {
      if (receiverType.value === 'jzmedia_tv') refreshApps()
      else discover()
    }
  }
  function close() {
    if (busy.value) return
    open.value = false
    searchGeneration++
    searchController?.abort()
    searching.value = false
    stopApps()
  }
  async function play() {
    if (disposed || busy.value || searching.value || uncertain.value || !selected.value) return
    busy.value = true
    stopApps()
    const epoch = ++sessionEpoch
    let submitted = false
    // Capture this handoff before saving or contacting the receiver can suspend.
    error.value = ''
    try {
      const candidate = nextPosition ?? Number(position())
      const target = Number.isFinite(candidate) ? Math.max(0, candidate) : 0
      const body = { ...media(), device_id: selected.value, base_url: serverUrl.value, position: target,
        receiver_type: receiverType.value }
      await beforePlay(target)
      if (disposed || epoch !== sessionEpoch) return
      pendingCreation = { body, epoch, previousId: session.value?.id }
      submitted = true
      const value = await request('/api/cast/sessions', { method: 'POST', body: JSON.stringify(body), timeout: 60000 })
      // Playback was explicitly requested and continues if the page is unloaded during the request.
      if (!disposed && epoch === sessionEpoch) {
        accept(value)
        if (value?.active === false) onPlayFailure()
      }
      nextPosition = null
    } catch (e) {
      if (!disposed && epoch === sessionEpoch) {
        error.value = e.message
        const status = Number(e.status)
        const rejected = status >= 400 && status < 500 && status !== 408
        if (submitted && !rejected) {
          pendingCreation.failureMessage = e.message
          await reconcileCreation()
        }
        else { pendingCreation = null; onPlayFailure() }
      }
    }
    finally {
      if (!disposed) {
        busy.value = false
        if (open.value && receiverType.value === 'jzmedia_tv' && !active.value && !uncertain.value) {
          clearTimeout(appTimer)
          appTimer = setTimeout(refreshApps, 3000)
        }
      }
    }
  }
  async function reconcileCreation() {
    const pending = pendingCreation
    if (!pending || disposed || pending.epoch !== sessionEpoch) return
    const ctrl = new AbortController()
    reconcileController = ctrl
    try {
      const { body } = pending
      const value = await request(`/api/cast/current?version_id=${body.version_id}&kind=${encodeURIComponent(body.kind)}`,
        { signal: ctrl.signal, timeout: 5000 })
      if (disposed || ctrl.signal.aborted || pending.epoch !== sessionEpoch) return
      const remote = value.session
      if (!Object.hasOwn(value, 'session')) throw new Error('无法读取投屏状态')
      if (!remote || !remote.active) {
        uncertain.value = false
        pendingCreation = null
        error.value = `${pending.failureMessage || '投屏请求未完成'}；已确认没有活动投屏，已恢复手机播放器。`
        onPlayFailure()
        return
      }
      if (remote.id && remote.id !== pending.previousId && remote.version_id === body.version_id
          && remote.kind === body.kind && remote.device?.id === body.device_id
          && (remote.receiver_type || 'dlna') === body.receiver_type) {
        error.value = ''
        accept(remote)
        return
      }
      throw new Error('已有其他投屏，无法确认本次请求')
    } catch {
      if (!disposed && !ctrl.signal.aborted && pending.epoch === sessionEpoch) {
        uncertain.value = true
        error.value = '无法确认电视是否已开始播放，手机保持暂停。请核对电视后重新检查投屏状态。'
        onPlayUncertain()
      }
    } finally { if (reconcileController === ctrl) reconcileController = null }
  }
  async function reconcile() {
    if (disposed || busy.value || !uncertain.value) return
    busy.value = true
    try { await reconcileCreation() }
    finally { if (!disposed) busy.value = false }
  }
  async function control(command, value) {
    if (disposed || busy.value || !active.value) return false
    const sid = session.value.id
    const epoch = ++sessionEpoch
    if (command === 'seek') {
      clearSeek()
      const target = Number(value)
      if (Number.isFinite(target) && target >= 0) {
        pendingSeek.value = { sessionId: sid, target, phase: 'sending', startedAt: Date.now(), stalePositions: [] }
        const remote = session.value
        rememberStalePosition(pendingSeek.value, remote.position_known ? remote.position : remote.last_position)
      }
    }
    const pending = command === 'seek' ? pendingSeek.value : null
    busy.value = true
    error.value = ''
    pollController?.abort()
    stopApps()
    clearTimeout(pollTimer)
    try {
      const result = await request(`/api/cast/sessions/${encodeURIComponent(sid)}/control`, {
        method: 'POST', body: JSON.stringify({ command, ...(value == null ? {} : { value }) }), timeout: 30000,
      })
      if (!disposed && epoch === sessionEpoch && session.value?.id === sid) {
        if (pending) {
          waitForSeek(pending)
          if (result?.state === 'TRANSITIONING') {
            rememberStalePosition(pending, result.last_position)
            if (result.position_known) rememberStalePosition(pending, result.position)
          }
        }
        accept(result)
      }
      return true
    } catch (e) {
      if (!disposed && epoch === sessionEpoch && session.value?.id === sid) {
        const status = Number(e.status)
        if (pending) {
          if (status >= 400 && status < 500 && status !== 408) clearSeek()
          else { pending.failureMessage = e.message; waitForSeek(pending) }
        }
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
    reconcileController?.abort()
    pollController?.abort()
    stopApps()
    clearTimeout(pollTimer)
    clearSeek()
  }
  function forget() {
    sessionEpoch++
    clearTimeout(pollTimer)
    pollController?.abort()
    session.value = null
    clearSeek()
    uncertain.value = false
    pendingCreation = null
    nextPosition = null
    reconcileController?.abort()
    stopApps()
  }
  return { open, searching, busy, devices, selected, serverUrl, manualLocation, session, error, searched,
    receiverType, appDevices, searchingApps, appsSearched, appsError, refreshApps, chooseReceiver,
    active, uncertain, pendingSeek, seekPending, displayPosition, stateLabel,
    restore, reconcile, discover, addManual, show, close, play, control, forget, dispose }
}
