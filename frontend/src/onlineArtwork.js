// Only this bounded queue reads online posters. The browser never receives a
// provider URL; every byte still passes the authenticated artwork gateway.
export const ONLINE_ARTWORK_LIMIT = 512 * 1024
export const ONLINE_ARTWORK_BYTES = 16 * 1024 * 1024
export const ONLINE_ARTWORK_CONCURRENCY = 2
const MAX_OBJECTS = 128
const MAX_TASKS = 256
const READ_SECONDS = 12
const MIME_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])
const VIEWING_CODES = new Set(['online_access_revoked', 'viewing_session_required', 'viewing_session_invalid', 'online_children_unavailable', 'viewing_locked'])
const PATH = /^\/api\/online\/catalog\/artwork\/src_[0-9a-f]{32}\/[0-9a-f]{32}$/

export function onlineArtworkPath(value) {
  return typeof value === 'string' && PATH.exec(value)?.[0] === value ? value : ''
}

function problem(code, retryable = false, status = 0) {
  return Object.assign(new Error('海报暂时无法加载'), { code, retryable, status })
}

function notify(type, detail) {
  try { window.dispatchEvent(new CustomEvent(type, { detail })) } catch { /* No browser in unit tests. */ }
}

async function boundedBody(response, signal, limit) {
  if (!response.body?.getReader) throw problem('artwork_body_unavailable')
  const reader = response.body.getReader()
  const chunks = []
  let size = 0
  try {
    for (;;) {
      if (signal.aborted) throw problem('artwork_cancelled')
      const { value, done } = await reader.read()
      if (signal.aborted) throw problem('artwork_cancelled')
      if (done) break
      if (!(value instanceof Uint8Array) || size + value.byteLength > limit) throw problem('artwork_byte_limit')
      size += value.byteLength
      chunks.push(value)
    }
    return { chunks, size }
  } finally {
    try { await reader.cancel() } catch { /* An aborted transport may already be closed. */ }
    reader.releaseLock()
  }
}

export function createOnlineArtworkQueue({
  request = (...args) => fetch(...args),
  createObjectURL = blob => URL.createObjectURL(blob),
  revokeObjectURL = url => URL.revokeObjectURL(url),
  dispatch = notify,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  byteBudget = ONLINE_ARTWORK_BYTES,
  objectLimit = MAX_OBJECTS,
} = {}) {
  if (!Number.isInteger(byteBudget) || byteBudget < ONLINE_ARTWORK_LIMIT || byteBudget > ONLINE_ARTWORK_BYTES
      || !Number.isInteger(objectLimit) || objectLimit < 1 || objectLimit > MAX_OBJECTS) throw new Error('海报缓存限制无效')
  const pending = new Set(), queue = [], objects = new Map()
  let active = 0, retainedBytes = 0, sequence = 0, epoch = 0, scheduled = false

  function release(entry, evicted = false) {
    const asset = objects.get(entry)
    if (!asset) return
    objects.delete(entry)
    retainedBytes -= asset.size
    revokeObjectURL(asset.url)
    if (evicted) entry.onEvict?.()
  }
  function settle(entry, value, error) {
    if (entry.settled) return
    entry.settled = true
    if (error) entry.reject(error)
    else entry.resolve(value)
  }
  function cancel(entry) {
    entry.cancelled = true
    entry.control?.abort()
    const index = queue.indexOf(entry)
    if (index >= 0) { queue.splice(index, 1); pending.delete(entry) }
    release(entry)
    settle(entry, null)
    schedule()
  }
  function clear() {
    epoch++
    for (const entry of [...pending]) cancel(entry)
    for (const entry of [...objects.keys()]) { release(entry, true); entry.cancelled = true }
  }
  function schedule() {
    if (scheduled) return
    scheduled = true
    Promise.resolve().then(() => {
      scheduled = false
      queue.sort((a, b) => a.priority - b.priority || a.order - b.order)
      while (active < ONLINE_ARTWORK_CONCURRENCY && queue.length) {
        const entry = queue.shift()
        active++
        void read(entry)
      }
    })
  }
  function retain(entry, blob, size, mime) {
    // Retained URLs are bounded separately from the at-most-two 512 KiB reads.
    while (objects.size >= objectLimit || retainedBytes + size > byteBudget) {
      const leastVisible = [...objects.keys()].sort((a, b) => b.priority - a.priority || a.order - b.order)[0]
      release(leastVisible, true)
    }
    const asset = { url: createObjectURL(blob), size, mime }
    objects.set(entry, asset)
    retainedBytes += size
    return asset
  }
  async function read(entry) {
    const control = entry.control = new AbortController()
    let response
    const timer = setTimer(() => {
      entry.timedOut = true
      control.abort()
      settle(entry, null, problem('artwork_timeout', true))
    }, READ_SECONDS * 1000)
    try {
      response = await request(entry.path, {
        signal: control.signal, credentials: 'same-origin', mode: 'same-origin', redirect: 'error',
        cache: 'no-store', referrerPolicy: 'no-referrer', headers: { Accept: 'image/jpeg,image/png,image/webp' },
      })
      if (control.signal.aborted || entry.cancelled || entry.epoch !== epoch) return
      const generation = response.headers?.get('X-Viewing-Generation')
      if (generation != null && /^\d{1,15}$/.test(generation)) dispatch('jzmedia:viewing-generation', { generation: Number(generation) })
      if (!response.ok) {
        let code = ''
        if ([401, 403].includes(response.status)) {
          try {
            const { chunks, size } = await boundedBody(response, control.signal, 2048)
            const body = new Uint8Array(size)
            let offset = 0
            for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength }
            const value = JSON.parse(new TextDecoder().decode(body))?.code
            if (VIEWING_CODES.has(value)) code = value
          } catch { /* Unknown or oversized errors never become page content. */ }
        } else {
          try { await response.body?.cancel() } catch { /* Already closed. */ }
        }
        if (control.signal.aborted || entry.cancelled || entry.epoch !== epoch) return
        const error = problem(code || 'artwork_http_error', [500, 502, 503, 504].includes(response.status), response.status)
        settle(entry, null, error)
        if ([401, 403].includes(response.status)) {
          clear()
          if (code) dispatch('jzmedia:viewing-error', { code })
          else if (response.status === 401) dispatch('jzmedia:unauthorized')
        }
        return
      }
      const mime = (response.headers?.get('Content-Type') || '').split(';')[0].trim().toLowerCase()
      if (!MIME_TYPES.has(mime)) throw problem('artwork_mime_unsupported')
      const length = response.headers?.get('Content-Length')
      if (length != null && (!/^\d{1,10}$/.test(length) || Number(length) > ONLINE_ARTWORK_LIMIT)) throw problem('artwork_byte_limit')
      const { chunks, size } = await boundedBody(response, control.signal, ONLINE_ARTWORK_LIMIT)
      if (control.signal.aborted || entry.cancelled || entry.epoch !== epoch) return
      if (!size || length != null && Number(length) !== size) throw problem('artwork_length_invalid')
      const asset = retain(entry, new Blob(chunks, { type: mime }), size, mime)
      settle(entry, asset)
    } catch (error) {
      if (!entry.cancelled && entry.epoch === epoch) {
        settle(entry, null, entry.timedOut ? problem('artwork_timeout', true)
          : error?.code ? error : problem('artwork_network_error', true))
      }
    } finally {
      clearTimer(timer)
      control.abort()
      try { await response?.body?.cancel() } catch { /* Abort may have closed the body already. */ }
      pending.delete(entry)
      // Cancelling a request does not free its slot until the actual read exits.
      active--
      schedule()
    }
  }
  function enqueue(path, { priority = 0, onEvict } = {}) {
    if (!onlineArtworkPath(path)) throw problem('artwork_path_invalid')
    if (pending.size >= MAX_TASKS) throw problem('artwork_queue_full')
    const entry = { path, priority, onEvict, order: ++sequence, epoch, cancelled: false, settled: false }
    entry.promise = new Promise((resolve, reject) => { entry.resolve = resolve; entry.reject = reject })
    pending.add(entry)
    queue.push(entry)
    schedule()
    return {
      promise: entry.promise,
      cancel: () => cancel(entry),
      setPriority(value) { entry.priority = Number.isFinite(value) ? value : Infinity; schedule() },
    }
  }
  return { enqueue, clear, stats: () => ({ active, queued: queue.length, retainedBytes, objects: objects.size }) }
}

export const onlineArtworkQueue = createOnlineArtworkQueue()
