// 普通 MP4 短片有完整 moov/有限时长；网页保持原片时间轴，下一片只预取一份。
export function mp4ClipEnd(start, duration, total) {
  if (!(duration > 0) || !Number.isFinite(duration)) return null
  return Math.min(total || Infinity, Math.floor(start + duration + 0.02))
}

export function detachedMediaTimeline(video) {
  return !!video && !video.paused && !video.ended && video.readyState >= 2
    && !(video.buffered?.length > 0)
}

export function mp4AtEnd(video, duration) {
  return !!video && (video.ended || (!video.seeking && duration > 0
    && Number(video.currentTime) >= duration - 0.35))
}

export async function loadMp4Blob(url, signal, {
  fetcher = fetch, makeUrl = blob => URL.createObjectURL(blob),
  maxBytes = 64 * 1024 * 1024, timeout = 45000,
} = {}) {
  const ctrl = new AbortController()
  const abort = () => ctrl.abort()
  if (signal?.aborted) ctrl.abort()
  else signal?.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(abort, timeout)
  try {
    const response = await fetcher(url, { signal: ctrl.signal })
    if (!response.ok) throw new Error('兼容片段下载失败（HTTP ' + response.status + '）')
    const length = Number(response.headers.get('Content-Length'))
    const tooLarge = () => { throw new Error('兼容片段超过内存限额，请切换 720p 后重试') }
    if (length > maxBytes) tooLarge()
    let blob
    if (response.body?.getReader) {
      const reader = response.body.getReader(), parts = []
      let size = 0
      while (true) {
        const result = await reader.read()
        if (ctrl.signal.aborted) throw new Error('aborted')
        if (result.done) break
        size += result.value.byteLength
        if (size > maxBytes) tooLarge()
        parts.push(result.value)
      }
      blob = new Blob(parts, { type: 'video/mp4' })
    } else {
      if (!(length > 0)) throw new Error('浏览器无法读取兼容片段，请用 Chrome/Edge')
      blob = await response.blob()
      if (blob.size > maxBytes) tooLarge()
    }
    if (ctrl.signal.aborted) throw new Error('aborted')
    if (!blob.size) throw new Error('兼容片段为空，请重新加载')
    return { url: makeUrl(blob), bytes: blob.size }
  } catch (error) {
    if (signal?.aborted) throw new Error('已取消')
    if (ctrl.signal.aborted) throw new Error('兼容片段下载超时，请检查网络或切换 720p')
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
    ctrl.abort()
  }
}

export function createMp4Queue({ create, close, isCurrent }) {
  let pending = null
  const drop = session => { if (session?.session_id) Promise.resolve(close(session.session_id, session)).catch(() => {}) }
  function clear() {
    const old = pending
    pending = null
    if (old) { old.ctrl.abort(); drop(old.session) }
  }
  function prepare(start, body, gen) {
    if (pending?.start === start && pending.gen === gen) return
    clear()
    const item = { start, gen, ctrl: new AbortController(), session: null }
    pending = item
    item.promise = create({ ...body, start }, item.ctrl.signal).then(session => {
      if (pending !== item || !isCurrent(gen)) { drop(session); return null }
      item.session = session
      return session
    }).catch(() => null)
  }
  async function take(start, gen) {
    const item = pending
    if (!item || item.start !== start || item.gen !== gen) return null
    const session = await item.promise
    if (pending !== item || !isCurrent(gen)) return null
    pending = null
    return session
  }
  return { prepare, take, clear, sessionId: () => pending?.session?.session_id,
    debug: () => pending && { start: pending.start, ready: !!pending.session } }
}
