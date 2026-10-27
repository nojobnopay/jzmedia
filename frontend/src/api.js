const TOKEN_KEY = 'jzmedia.token'
let artworkScope = ''

// Protection changes must not reuse artwork cached before protection was enabled.
export function setArtworkScope(scope) { artworkScope = scope || '' }
export function scopedArtworkUrl(path) {
  if (!path || !artworkScope || !/^\/(?:posters\/|api\/)/.test(path)) return path || ''
  return `${path}${path.includes('?') ? '&' : '?'}viewing=${encodeURIComponent(artworkScope)}`
}

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch (e) { return '' }
}
export function setToken(t) {
  try {
    const v = String(t || '')
    if (v) localStorage.setItem(TOKEN_KEY, v)
    else localStorage.removeItem(TOKEN_KEY)
  } catch (e) { /* 忽略 */ }
}

function _authHeaders() {
  const t = getToken()
  return t ? { 'X-Api-Token': t } : {}
}

function _brief(text) {
  // FastAPI 的字符串 detail 是给用户看的原因，不把 JSON 包装显示在反馈中。
  try {
    const data = JSON.parse(text)
    if (typeof data?.detail === 'string') text = data.detail
    else if (typeof data?.message === 'string') text = data.message
  } catch { /* 网关和连接错误也可能返回纯文本，沿用原文。 */ }
  // 错误体截断（评审 B8/R05-Q6/R14-Q4）：HTTP body 直出 UI 会撑破布局
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  return t.length > 300 ? t.slice(0, 300) + '…' : t
}

function _viewingError(code) {
  try { window.dispatchEvent(new CustomEvent('jzmedia:viewing-error', { detail: { code } })) } catch { /* 无浏览器环境 */ }
}

function _httpError(status, body, silentViewing = false) {
  let code = ''
  try { code = JSON.parse(body)?.code || '' } catch { /* 非 JSON 错误 */ }
  if (code && !silentViewing) {
    _viewingError(code)
  } else if (status === 401 && !code) {
    try { window.dispatchEvent(new CustomEvent('jzmedia:unauthorized')) } catch { /* 无浏览器环境 */ }
  }
  const error = new Error(`${status} ${_brief(body)}`)
  error.code = code
  error.status = status
  return error
}

// opts.signal：调用方取消（关窗/切档）与内部超时合并；外部取消抛「已取消」而非超时文案。
export async function api(path, opts = {}) {
  const { timeout: configuredTimeout, signal: outer, headers: extraHeaders,
    mayContinue = false, silentViewing = false, ...fetchOpts } = opts
  const method = String(fetchOpts.method || 'GET').toUpperCase()
  const timeout = configuredTimeout ?? (method === 'GET' ? 30000 : 120000)
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  const onOuterAbort = outer ? () => ctrl.abort() : null
  if (onOuterAbort) {
    if (outer.aborted) ctrl.abort()
    else outer.addEventListener('abort', onOuterAbort)
  }
  try {
    const r = await fetch(path, {
      // 仅带 body 时发 JSON Content-Type（评审 B8/R04-B2）
      headers: { ...(fetchOpts.body != null ? { 'Content-Type': 'application/json' } : {}), ..._authHeaders(), ...(extraHeaders || {}) },
      ...fetchOpts,
      signal: ctrl.signal
    })
    const generation = r.headers?.get('X-Viewing-Generation')
    if (generation != null && !/^\/api\/viewing\/(session|mode|unlock|lock|activity|password)$/.test(path)) {
      try { window.dispatchEvent(new CustomEvent('jzmedia:viewing-generation', { detail: { generation: Number(generation) } })) } catch { /* 无浏览器环境 */ }
    }
    if (!r.ok) {
      throw _httpError(r.status, await r.text(), silentViewing)
    }
    return r.json()
  } catch (e) {
    if (e && e.name === 'AbortError') {
      if (outer && outer.aborted) throw new Error('已取消')
      if (mayContinue) throw new Error('请求超时，后台任务可能仍在处理，可稍后刷新查看')
      if (method === 'GET') throw new Error('请求超时，请检查连接后重试')
      throw new Error('请求超时，操作结果未知，请刷新后核对')
    }
    throw e
  } finally {
    clearTimeout(timer)
    if (onOuterAbort) outer.removeEventListener('abort', onOuterAbort)
  }
}
// v 为可选版本（一般传 updated_at）：海报原地覆盖时 URL 不变，浏览器会吃旧缓存；
// 带版本参数即可强制取新图（后端对可变海报也发 Cache-Control: no-cache 双保险）。
// DB 存 DATA_DIR 相对路径（如 posters/movies/123.jpg）：去掉头部 posters/ 保留子目录。
export const posterUrl = (p, v) => {
  if (!p) return ''
  let rel = String(p).replace(/^\/+/, '')
  if (rel.startsWith('posters/')) rel = rel.slice('posters/'.length)
  const u = `/posters/${rel}`
  return scopedArtworkUrl(v ? `${u}?v=${encodeURIComponent(v)}` : u)
}

// TV 演职员头像：走后端代理 `/api/tv/cast-avatar`（浏览器无需可达 TMDB 图片
// 域名，w185 缓存到 data/posters/tvcast/）；下载失败后端抛 502，调用方需
// `@error` 回退首字母占位（季页剧照同模式）。
export const castAvatarUrl = (p) => {
  if (!p) return ''
  return scopedArtworkUrl(`/api/tv/cast-avatar?path=${encodeURIComponent(p)}`)
}

// 大文件上传专用：FormData + XHR（支持进度与中断取消；>2GB 局域网场景）。
// onProgress(0~100)；onUploaded 在字节发完时触发（服务端可能还在刮削，用于切换等待提示）；
// fields 透传为 query 参数（如 {relpath, target_dir}）；
// 发送文件前预检权限；authorize 可验证保护密码，验证后重新检查再开始传输。
// canStart 在预检结束后确认调用方仍有效，避免离开详情页后开始传输。
// 返回 { promise, abort }，abort 后 promise 以 '已取消' 拒绝。
export function apiUpload(path, file, { onProgress, onUploaded, authorize, canStart, subdir = '', fields = {} } = {}) {
  let xhr = null
  const ctrl = new AbortController()
  let uploadedFired = false
  const fireUploaded = () => {
    if (uploadedFired || ctrl.signal.aborted) return
    uploadedFired = true
    try { if (onUploaded) onUploaded() } catch (e) { /* 忽略 */ }
  }
  const promise = new Promise((resolve, reject) => {
    ctrl.signal.addEventListener('abort', () => {
      try { xhr?.abort() } finally { reject(new Error('已取消')) }
    }, { once: true })
    const start = async () => {
      try {
        const check = (silentViewing = !!authorize) => api('/api/uploads/check-access', {
          method: 'POST', signal: ctrl.signal, silentViewing,
        })
        try { await check() }
        catch (error) {
          if (ctrl.signal.aborted) return
          if (error.code !== 'management_required' || !authorize) {
            if (authorize && error.code) _viewingError(error.code)
            throw error
          }
          if (!await authorize()) throw new Error('已取消')
          if (ctrl.signal.aborted) return
          await check(false)
        }
        if (ctrl.signal.aborted) return
        if (canStart && !canStart()) throw new Error('已取消')
        send()
      } catch (error) { reject(error) }
    }
    const send = () => {
      xhr = new XMLHttpRequest()
      const qs = new URLSearchParams()
      if (subdir) qs.set('subdir', subdir)
      for (const [k, v] of Object.entries(fields || {})) {
        if (v !== undefined && v !== null && v !== '') qs.set(k, v)
      }
      const url = qs.toString() ? `${path}?${qs}` : path
      xhr.open('POST', url)
      const tk = getToken()
      if (tk) { try { xhr.setRequestHeader('X-Api-Token', tk) } catch (e) { /* 忽略 */ } }
      xhr.timeout = 0 // 大文件不限时，由用户手动取消
      if (xhr.upload) {
        xhr.upload.onload = fireUploaded
        if (onProgress) {
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              onProgress(Math.round((e.loaded / e.total) * 100), Math.min(file.size, Math.round(file.size * e.loaded / e.total)))
              if (e.loaded >= e.total) fireUploaded()
            }
          }
        }
      }
      xhr.onload = () => {
        const generation = xhr.getResponseHeader?.('X-Viewing-Generation')
        if (generation != null) {
          try { window.dispatchEvent(new CustomEvent('jzmedia:viewing-generation', { detail: { generation: Number(generation) } })) } catch { /* 无浏览器环境 */ }
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)) }
          catch (e) { reject(new Error('上传响应解析失败（服务端可能异常，请刷新查看）')) }
        } else {
          reject(_httpError(xhr.status, xhr.responseText))
        }
      }
      xhr.onerror = () => reject(new Error('上传失败：网络错误'))
      xhr.onabort = () => reject(new Error('已取消'))
      xhr.ontimeout = () => reject(new Error('上传超时'))
      const fd = new FormData()
      fd.append('file', file, file.name)
      xhr.send(fd)
    }
    start()
  })
  return { promise, abort: () => ctrl.abort() }
}
