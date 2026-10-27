// Online sessions use their own API, identity and original media timeline.
// No local playback, progress, preview or compatibility-transcode request lives here.
import { api, getToken } from './api.js'

const SOURCE_ID = /^src_[0-9a-f]{32}$/
const ITEM_ID = /^itm_[0-9a-f]{32}$/
const EPISODE_ID = /^ep_[0-9a-f]{32}$/
const LINE_ID = /^ln_[0-9a-f]{32}$/
const SESSION_ID = /^[A-Za-z0-9_-]{32,128}$/
const IDENTITY_KEYS = new Set(['source_id', 'item_id', 'kind', 'episode_id', 'line_id'])
const DESCRIPTOR_KEYS = new Set(['identity', 'session_id', 'media_url', 'transport', 'processing',
  'seekable', 'duration_seconds', 'time_offset_seconds', 'expires_at'])

export class OnlinePlaybackError extends Error {
  constructor(code) {
    const messages = {
      invalid_online_identity: '在线内容身份无效',
      invalid_online_descriptor: '在线播放描述无效，请重试',
      online_session_expired: '在线播放会话已过期，请重试',
      online_cancelled: '已取消',
      online_player_closed: '播放器已关闭',
      invalid_online_progress: '在线播放进度无效',
    }
    super(messages[code] || '在线播放请求未完成')
    this.name = 'OnlinePlaybackError'
    this.code = code
  }
}

function record(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function exactMatch(pattern, value) {
  // JavaScript's $ can match before a final newline; identifiers cannot.
  return typeof value === 'string' && pattern.exec(value)?.[0] === value
}

export function validateOnlineIdentity(value) {
  if (!record(value) || Object.keys(value).some(key => !IDENTITY_KEYS.has(key))
    || !exactMatch(SOURCE_ID, value.source_id) || !exactMatch(ITEM_ID, value.item_id)
    || !['movie', 'episode'].includes(value.kind)
    || value.line_id != null && !exactMatch(LINE_ID, value.line_id)
    || (value.kind === 'episode' ? !exactMatch(EPISODE_ID, value.episode_id)
      : value.episode_id !== undefined && value.episode_id !== null)) {
    throw new OnlinePlaybackError('invalid_online_identity')
  }
  return Object.freeze({ source_id: value.source_id, item_id: value.item_id,
    kind: value.kind, episode_id: value.kind === 'episode' ? value.episode_id : null,
    ...(value.line_id != null ? { line_id: value.line_id } : {}) })
}

export function onlineIdentityKey(value) {
  const identity = validateOnlineIdentity(value)
  return `${identity.source_id}:${identity.item_id}:${identity.kind}:${identity.episode_id || ''}${identity.line_id ? ':' + identity.line_id : ''}`
}

function sessionId(value) {
  return exactMatch(SESSION_ID, value) ? value : null
}

export function validateOnlineDescriptor(value, expectedIdentity, { now = Date.now } = {}) {
  const expected = validateOnlineIdentity(expectedIdentity)
  let identity
  try { identity = validateOnlineIdentity(value?.identity) }
  catch { throw new OnlinePlaybackError('invalid_online_descriptor') }
  const sid = sessionId(value?.session_id)
  const duration = value?.duration_seconds
  const expiry = value?.expires_at
  if (!record(value) || Object.keys(value).some(key => !DESCRIPTOR_KEYS.has(key))
    || onlineIdentityKey(identity) !== onlineIdentityKey(expected) || !sid
    || value.media_url !== `/api/online/sessions/${sid}/media`
    || !['mp4', 'hls'].includes(value.transport) || value.processing !== 'passthrough'
    || typeof value.seekable !== 'boolean' || value.time_offset_seconds !== 0
    || duration !== null && (!Number.isFinite(duration) || duration <= 0)
    || !Number.isSafeInteger(expiry) || expiry <= 0 || !Number.isSafeInteger(expiry * 1000)) {
    throw new OnlinePlaybackError('invalid_online_descriptor')
  }
  const current = now()
  if (!Number.isFinite(current) || expiry * 1000 <= current) {
    throw new OnlinePlaybackError('online_session_expired')
  }
  return Object.freeze({ identity, session_id: sid, media_url: value.media_url,
    transport: value.transport, processing: 'passthrough', seekable: value.seekable,
    duration_seconds: duration, time_offset_seconds: 0, expires_at: expiry })
}

async function deleteRequest(path, options) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options.timeout)
  const token = getToken()
  try {
    // api() expects JSON, whereas successful session deletion returns 204.
    // Cookie ownership and the existing write token must survive page exit.
    return await fetch(path, { method: 'DELETE', keepalive: true, credentials: 'same-origin',
      headers: token ? { 'X-Api-Token': token } : {}, signal: controller.signal })
  } finally { clearTimeout(timer) }
}

export function createOnlinePlaybackClient({ request = api, releaseRequest = deleteRequest,
  now = Date.now } = {}) {
  const owned = new Set()
  const releasing = new Map()
  let disposed = false

  function release(sid) {
    if (!sessionId(sid) || !owned.has(sid)) return Promise.resolve(true)
    if (releasing.has(sid)) return releasing.get(sid)
    let delivery
    try {
      delivery = releaseRequest(`/api/online/sessions/${sid}`, {
        method: 'DELETE', keepalive: true, timeout: 15000,
      })
    } catch (error) { delivery = Promise.reject(error) }
    const result = Promise.resolve(delivery).then(response => {
      const status = response?.status
      const delivered = response !== false && (status === undefined
        || status >= 200 && status < 300 || status === 404 || status === 410)
      if (delivered) owned.delete(sid)
      return delivered
    }, error => {
      if (error?.status === 404 || error?.status === 410) {
        owned.delete(sid)
        return true
      }
      return false
    }).finally(() => {
      if (releasing.get(sid) === result) releasing.delete(sid)
    })
    releasing.set(sid, result)
    return result
  }

  async function create(identity, { signal } = {}) {
    if (disposed) throw new OnlinePlaybackError('online_player_closed')
    const expected = validateOnlineIdentity(identity)
    if (signal?.aborted) throw new OnlinePlaybackError('online_cancelled')
    let value
    try {
      value = await request('/api/online/sessions', { method: 'POST', signal, timeout: 45000,
        body: JSON.stringify(expected) })
    } catch (error) {
      if (signal?.aborted || disposed) throw new OnlinePlaybackError('online_cancelled')
      throw error
    }
    const sid = sessionId(value?.session_id)
    if (sid) owned.add(sid)
    if (signal?.aborted || disposed) {
      if (sid) await release(sid)
      throw new OnlinePlaybackError('online_cancelled')
    }
    try { return validateOnlineDescriptor(value, expected, { now }) }
    catch (error) {
      if (sid) await release(sid)
      throw error
    }
  }

  function releaseAll() { return Promise.all([...owned].map(release)) }
  function close() {
    disposed = true
    // A failed request remains owned and can be retried on a later cleanup.
    // An unknown result after an aborted POST is bounded by server idle expiry.
    return releaseAll()
  }
  return { create, release, releaseAll, close }
}

export function createOnlinePlaybackController({ client = createOnlinePlaybackClient() } = {}) {
  let generation = 0
  let pending = null
  let current = null
  let disposed = false

  async function begin(identity, { signal } = {}) {
    if (disposed) throw new OnlinePlaybackError('online_player_closed')
    const turn = ++generation
    pending?.abort()
    const controller = new AbortController()
    pending = controller
    const cancel = () => controller.abort()
    if (signal?.aborted) cancel()
    else signal?.addEventListener('abort', cancel, { once: true })
    const previous = current
    current = null
    const active = () => !disposed && turn === generation && !controller.signal.aborted
    try {
      if (previous) await client.release(previous.session_id)
      if (!active()) throw new OnlinePlaybackError('online_cancelled')
      const descriptor = await client.create(identity, { signal: controller.signal })
      if (!active()) {
        await client.release(descriptor.session_id)
        throw new OnlinePlaybackError('online_cancelled')
      }
      current = descriptor
      return descriptor
    } catch (error) {
      if (!active()) throw new OnlinePlaybackError('online_cancelled')
      throw error
    } finally {
      signal?.removeEventListener('abort', cancel)
      if (pending === controller) pending = null
    }
  }

  function stop() {
    generation++
    pending?.abort()
    pending = null
    current = null
    return client.releaseAll()
  }
  function close() {
    disposed = true
    stop()
    return client.close()
  }
  return { begin, current: () => current, stop, close }
}

export function createOnlineProgressMemory({ maxEntries = 32 } = {}) {
  if (!Number.isInteger(maxEntries) || maxEntries < 1 || maxEntries > 256) {
    throw new OnlinePlaybackError('invalid_online_progress')
  }
  const values = new Map()
  function save(identity, value) {
    const key = onlineIdentityKey(identity)
    if (!Number.isFinite(value?.position) || value.position < 0) return null
    const duration = Number.isFinite(value.duration) && value.duration > 0 ? value.duration : null
    const snapshot = Object.freeze({ position: duration === null ? value.position : Math.min(value.position, duration),
      duration, paused: !!value.paused })
    values.delete(key)
    values.set(key, snapshot)
    while (values.size > maxEntries) values.delete(values.keys().next().value)
    return snapshot
  }
  function get(identity) { return values.get(onlineIdentityKey(identity)) || null }
  return { save, get, clear: identity => values.delete(onlineIdentityKey(identity)), clearAll: () => values.clear() }
}

// A bounded one-time initial gap is a client alignment event, not a reason to
// start decoding or transcoding on the NAS. Keep the P0 acceptance boundaries.
export function canRecoverOnlineStartupGap(event) {
  return !!event && event.type === 'mediaError' && event.detail === 'bufferSeekOverHole'
    && event.fatal === false && event.beforeFirstFrame === true
    && event.priorRecoveries === 0 && Number.isFinite(event.mediaTime)
    && Number.isFinite(event.seekFrom) && Number.isFinite(event.seekTo)
    && event.seekFrom >= 0 && event.seekTo > event.seekFrom && event.seekTo <= 0.5
    && event.seekTo - event.seekFrom <= 0.5
    && event.mediaTime >= 0 && Math.abs(event.mediaTime - event.seekTo) <= 0.01
}
