// Each player owns its leases. Releasing one player must never close another's stream.
export function createSessionReleaser(request) {
  const released = new Map()
  return sid => {
    if (!sid) return Promise.resolve()
    if (!released.has(sid)) {
      let result
      try {
        result = request(`/api/stream/sessions/${sid}`, {
          method: 'DELETE', keepalive: true, timeout: 15000,
        })
      } catch (error) { result = Promise.reject(error) }
      // Failed delivery is covered by the server's lease expiry.
      released.set(sid, Promise.resolve(result).catch(() => {}))
    }
    return released.get(sid)
  }
}

export function playbackHeartbeatPayload({ position, paused, playbackRate }) {
  const pos = Number(position), rate = Number(playbackRate)
  return { position: Number.isFinite(pos) && pos >= 0 ? pos : 0,
    paused: !!paused, playback_rate: Number.isFinite(rate) && rate > 0 ? rate : 1 }
}

// A slow request cannot stack heartbeats or renew an old lease after replacement.
export function createPlaybackHeartbeat({ request, session, state, enabled, onUpdate, onExpired,
  schedule = setInterval, unschedule = clearInterval }) {
  let timer = null, epoch = 0, pending = null
  async function send() {
    const sid = session()
    if (!sid || !enabled() || pending) return
    const current = epoch, ctrl = new AbortController()
    pending = ctrl
    try {
      const result = await request(`/api/stream/sessions/${sid}/ping`, {
        method: 'POST', timeout: 10000, signal: ctrl.signal,
        body: JSON.stringify(playbackHeartbeatPayload(state())),
      })
      if (current === epoch && session() === sid && enabled()) onUpdate?.(result)
    } catch (error) {
      if (error?.status === 404 && current === epoch && session() === sid && enabled()) onExpired(sid)
    } finally { if (pending === ctrl) pending = null }
  }
  function stop() {
    epoch++
    if (timer !== null) unschedule(timer)
    timer = null
    pending?.abort()
    pending = null
  }
  function start() {
    stop()
    if (!session() || !enabled()) return
    void send()
    timer = schedule(send, 10000)
  }
  return { start, stop, send }
}

// pagehide covers closing, refresh and bfcache; hidden alone may still be playing.
export function createPlaybackPageLifecycle({ capture, save, stop, restore }) {
  let away = false, epoch = 0, snapshot, leaving = null, target = null
  function leave() {
    if (away) return leaving
    away = true
    epoch++
    snapshot = capture()
    let saving, stopping
    try { saving = save(snapshot) } catch (error) { saving = Promise.reject(error) }
    // Never wait for a progress request before aborting creation and releasing leases.
    try { stopping = stop(snapshot) } catch (error) { stopping = Promise.reject(error) }
    leaving = Promise.allSettled([saving, stopping])
    return leaving
  }
  function pageShow(event) {
    if (!event.persisted || !away) return
    away = false
    leaving = null
    const current = ++epoch
    void Promise.resolve().then(() => { if (!away && current === epoch) return restore(snapshot) }).catch(() => {})
  }
  function unbind() {
    target?.removeEventListener('pagehide', leave)
    target?.removeEventListener('pageshow', pageShow)
    target = null
  }
  function bind(nextTarget) {
    unbind()
    target = nextTarget
    target.addEventListener('pagehide', leave)
    target.addEventListener('pageshow', pageShow)
  }
  return { leave, bind, unbind, isAway: () => away }
}

// A missing lease may be rebuilt once; repeated immediate expiry needs user retry.
export function createExpiredSessionRecovery({ current, recover, failed, now = Date.now }) {
  let pending = null, lastAttempt = -Infinity, attempts = 0
  function reset() { attempts = 0; lastAttempt = -Infinity }
  function expired(sid) {
    if (current() !== sid) return Promise.resolve()
    if (pending) return pending
    if (attempts >= 2 || now() - lastAttempt < 30000) { failed(); return Promise.resolve() }
    attempts++
    lastAttempt = now()
    pending = Promise.resolve().then(() => { if (current() === sid) return recover(sid) })
      .catch(() => { failed() }).finally(() => { pending = null })
    return pending
  }
  return { expired, reset }
}
