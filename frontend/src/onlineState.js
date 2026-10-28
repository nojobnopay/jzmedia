import { api } from './api.js'
import { onlineIdentityKey, validateOnlineIdentity } from './onlinePlayback.js'

const ITEM_ID = /^itm_[0-9a-f]{32}$/
const EPISODE_ID = /^ep_[0-9a-f]{32}$/
const LINE_ID = /^ln_[0-9a-f]{32}$/
const OPAQUE = /^[A-Za-z0-9_-]{32,128}$/
const SCOPE = /^[A-Za-z0-9_-]{16,256}$/
const exact = (pattern, value) => typeof value === 'string' && pattern.exec(value)?.[0] === value

function invalid(code = 'invalid_online_state') {
  const error = new Error('在线观看记录无效，请重新打开页面')
  error.code = code
  return error
}
function itemId(value) { if (!exact(ITEM_ID, value)) throw invalid(); return value }
function scopeHeaders(value) {
  if (!exact(SCOPE, value)) throw invalid('online_state_scope_required')
  return { 'X-Online-State-Scope': value }
}
function sameScope(response, scope) {
  if (response?.scope_token !== scope) throw invalid('online_state_scope_changed')
  return response
}
export function onlineStateError(error) {
  return ({
    online_progress_writer_conflict: '另一播放页已更新此进度，本次播放不再覆盖它。重新开始播放可再次保存。',
    online_state_scope_changed: '观看身份已变化，本次记录未继续保存。请重新打开页面。',
    online_state_scope_required: '观看身份尚未确认，请重新打开页面。',
    online_access_revoked: '观看权限已变化，请重新连接服务。',
    online_children_unavailable: '儿童模式暂不支持在线记录。',
    online_source_disabled: '视频源已停用；记录会保留，恢复来源后可继续。',
    online_state_limit: '此设备的在线记录已达到上限，可清除旧记录后重试。',
    online_favorite_limit: '此设备当前观看模式的收藏已达上限，请先取消部分收藏。',
    online_progress_limit: '此设备当前观看模式的进度已达上限，请先清除旧观看记录。',
    online_state_scope_limit: '此设备的记录状态暂时无法更新，请重新连接服务后重试。',
  }[error?.code] || '暂时无法保存或读取在线记录；视频播放仍可继续。')
}

// Reads have separate ownership so a detail state read cannot cancel the shelf.
// Mutations freeze the scope obtained by the initiating read; they never look
// up a newer token after a mode change or retry an optimistic-write conflict.
export function createOnlineStateClient({ request = api } = {}) {
  const reads = new Map()
  let closed = false
  async function read(channel, path) {
    if (closed) return null
    reads.get(channel)?.abort()
    const control = new AbortController()
    reads.set(channel, control)
    try {
      const response = await request(path, { signal: control.signal, timeout: 10000 })
      return !closed && reads.get(channel) === control && !control.signal.aborted ? response : null
    } catch (error) {
      if (closed || reads.get(channel) !== control || control.signal.aborted) return null
      throw error
    } finally { if (reads.get(channel) === control) reads.delete(channel) }
  }
  function cancel() { for (const control of reads.values()) control.abort(); reads.clear() }
  return {
    library(section = 'continue', offset = 0) {
      if (!['favorites', 'continue', 'history'].includes(section) || !Number.isInteger(offset) || offset < 0) throw invalid()
      return read('library', '/api/online/state/library?' + new URLSearchParams({ section, limit: '20', offset: String(offset) }))
    },
    item(value, selection = {}) {
      const query = new URLSearchParams()
      for (const [key, pattern] of [['episode_id', EPISODE_ID], ['line_id', LINE_ID]]) {
        const selected = selection[key]
        if (selected == null) continue
        if (!exact(pattern, selected)) throw invalid()
        query.set(key, selected)
      }
      return read('item', `/api/online/state/items/${itemId(value)}${query.size ? '?' + query : ''}`)
    },
    async favorite(value, favorite, scope) {
      if (typeof favorite !== 'boolean') throw invalid()
      return sameScope(await request(`/api/online/state/items/${itemId(value)}/favorite`, {
        method: 'PUT', body: JSON.stringify({ favorite }), headers: scopeHeaders(scope), timeout: 10000,
      }), scope)
    },
    async deleteHistory(value, scope) {
      const response = await request(`/api/online/state/items/${itemId(value)}/history`, {
        method: 'DELETE', headers: scopeHeaders(scope), timeout: 10000,
      })
      // Clearing history advances the server's scope epoch, invalidating old
      // pending writers. Only a new explicit intent may use this new token.
      if (!exact(SCOPE, response?.scope_token)) throw invalid()
      return response
    },
    cancel,
    close() { closed = true; cancel() },
  }
}

export function loadOnlineAutoplay(mode = 'normal', storage) {
  try { return (storage ?? globalThis.localStorage)?.getItem(`jzmedia.online.autoplay.${mode === 'private' ? 'private' : 'normal'}`) === 'true' }
  catch { return false }
}
export function saveOnlineAutoplay(enabled, mode = 'normal', storage) {
  try { (storage ?? globalThis.localStorage)?.setItem(`jzmedia.online.autoplay.${mode === 'private' ? 'private' : 'normal'}`, enabled ? 'true' : 'false') }
  catch { /* This preference never stores content or private viewing records. */ }
}

export function nextOnlineEpisode(detail, identity) {
  if (!detail || identity?.kind !== 'episode' || detail.item?.kind !== 'tv'
    || detail.item.id !== identity.item_id || detail.source?.id !== identity.source_id) return null
  const line = detail.lines?.find(value => value.id === identity.line_id)
  if (!line) return null
  const current = line.entries.findIndex(value => value.identity
    && onlineIdentityKey(value.identity) === onlineIdentityKey(identity))
  if (current < 0) return null
  const next = line.entries[current + 1]
  return next?.supported && next.identity?.kind === 'episode'
    && next.identity.source_id === identity.source_id && next.identity.item_id === identity.item_id
    && next.identity.line_id === identity.line_id ? next : null
}

// One playback run owns one writer. A session renewal can re-claim using only
// this writer's known revision. A conflict never causes an automatic GET/claim
// loop that would take ownership from a newer tab.
export function createOnlineProgressWriter({ identity, scopeToken, revision = 0, request = api,
  intervalMs = 10000, now = Date.now, onNotice = () => {}, onSaved = () => {} } = {}) {
  const selected = validateOnlineIdentity(identity)
  const key = onlineIdentityKey(selected)
  const scope = scopeToken
  let expectedRevision = Number.isSafeInteger(revision) && revision >= 0 ? revision : 0
  let session = null, grantedSession = null, grant = null, sequence = 0
  let pending = null, flight = null, timer = null, lastSent = -Infinity
  let closing = false, closed = false, disabled = !exact(SCOPE, scope)
  const headers = disabled ? {} : scopeHeaders(scope)
  function notify(error) { onNotice(onlineStateError(error), error?.code || 'online_state_unavailable') }
  function clear() { if (timer !== null) clearTimeout(timer); timer = null }
  function currentIdentity(value) { try { return onlineIdentityKey(value) === key } catch { return false } }
  function schedule() {
    if (closed || closing || disabled || !pending || !session || timer !== null || flight) return
    const wait = Math.max(0, intervalMs - (now() - lastSent))
    timer = setTimeout(() => { timer = null; void send().catch(() => {}) }, wait)
  }
  async function send({ claimOnly = false } = {}) {
    if (flight) return flight
    if (closed || disabled || !pending && !claimOnly || !session) return false
    const work = (async () => {
      try {
        if (grantedSession !== session.session_id) {
          const claiming = session.session_id
          const response = sameScope(await request('/api/online/state/progress/claim', {
            method: 'POST', body: JSON.stringify({ session_id: claiming, expected_revision: expectedRevision }),
            headers, timeout: 4000, credentials: 'same-origin', redirect: 'error', cache: 'no-store', keepalive: true,
          }), scope)
          if (!currentIdentity(response.identity) || !exact(OPAQUE, response.writer_id)
            || !Number.isSafeInteger(response.writer_revision) || response.writer_revision <= expectedRevision) throw invalid()
          expectedRevision = response.writer_revision
          grant = { writer_id: response.writer_id, writer_revision: response.writer_revision }
          sequence = 0; grantedSession = claiming
          if (closed || disabled || grantedSession !== session?.session_id) return false
        }
        if (!pending) return true
        const value = pending
        pending = null
        const response = sameScope(await request('/api/online/state/progress', {
          method: 'POST', body: JSON.stringify({ ...grant, seq: ++sequence, ...value }), headers,
          timeout: 4000, credentials: 'same-origin', redirect: 'error', cache: 'no-store', keepalive: true,
        }), scope)
        if (!currentIdentity(response.progress?.identity) || response.progress.writer_revision !== grant.writer_revision) throw invalid()
        lastSent = now()
        if (!closed) onSaved(response.progress)
        return true
      } catch (error) {
        // A failed mutation may already have committed. Stop this writer; a
        // later explicit playback start reads a revision and can claim anew.
        disabled = true; pending = null; clear()
        if (!closed) notify(error)
        return false
      }
    })()
    flight = work
    try { return await work }
    finally { if (flight === work) flight = null; schedule() }
  }
  async function flush() {
    clear()
    if (flight) await flight
    if (pending && !disabled && !closed) return send()
    return !disabled
  }
  return {
    async session(value) {
      if (closing || closed || disabled || !currentIdentity(value?.identity) || !exact(OPAQUE, value?.session_id)) return false
      session = { session_id: value.session_id }
      const selectedSession = value.session_id
      if (flight) await flight
      if (closed || closing || disabled || session?.session_id !== selectedSession) return false
      return send({ claimOnly: true })
    },
    update(value) {
      if (closing || closed || disabled || value?.first_frame !== true || !currentIdentity(value.identity)
        || !Number.isFinite(value.position) || value.position < 0) return false
      const duration = Number.isFinite(value.duration) && value.duration > 0 ? value.duration : null
      pending = { position: duration ? Math.min(value.position, duration) : value.position,
        duration, paused: !!value.paused, completed: value.completed === true }
      if (['pause', 'seek', 'close', 'ended', 'pagehide', 'error'].includes(value.reason) || value.completed === true) {
        void flush().catch(() => {})
      } else schedule()
      return true
    },
    flush,
    async close({ timeoutMs = 4500 } = {}) {
      if (closed) return
      closing = true; clear()
      let timeout
      try { await Promise.race([flush(), new Promise(resolve => { timeout = setTimeout(resolve, timeoutMs) })]) }
      finally { clearTimeout(timeout); closed = true; pending = null; clear() }
    },
    snapshot: () => ({ revision: expectedRevision, disabled, pending: !!pending, inFlight: !!flight, closed }),
  }
}
