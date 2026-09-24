const TOKEN_KEY = 'jzmedia.token'

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
  // 错误体截断（评审 B8/R05-Q6/R14-Q4）：HTTP body 直出 UI 会撑破布局
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  return t.length > 300 ? t.slice(0, 300) + '…' : t
}

// opts.signal：调用方取消（关窗/切档）与内部超时合并；外部取消抛「已取消」而非超时文案。
export async function api(path, opts = {}) {
  const { timeout = 120000, signal: outer, ...fetchOpts } = opts
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
      headers: { ...(fetchOpts.body != null ? { 'Content-Type': 'application/json' } : {}), ..._authHeaders(), ...(fetchOpts.headers || {}) },
      ...fetchOpts,
      signal: ctrl.signal
    })
    if (r.status === 401) {
      try { window.dispatchEvent(new CustomEvent('jzmedia:unauthorized')) } catch (e) { /* 忽略 */ }
      throw new Error(`${r.status} ${_brief(await r.text())}`)
    }
    if (!r.ok) {
      throw new Error(`${r.status} ${_brief(await r.text())}`)
    }
    return r.json()
  } catch (e) {
    if (e && e.name === 'AbortError') {
      if (outer && outer.aborted) throw new Error('已取消')
      throw new Error('请求超时，后台可能仍在处理，稍后刷新查看')
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
  return v ? `${u}?v=${encodeURIComponent(v)}` : u
}

// TV 演职员头像：走后端代理 `/api/tv/cast-avatar`（浏览器无需可达 TMDB 图片
// 域名，w185 缓存到 data/posters/tvcast/）；下载失败后端抛 502，调用方需
// `@error` 回退首字母占位（季页剧照同模式）。
export const castAvatarUrl = (p) => {
  if (!p) return ''
  return `/api/tv/cast-avatar?path=${encodeURIComponent(p)}`
}

// 大文件上传专用：FormData + XHR（支持进度与中断取消；>2GB 局域网场景）。
// onProgress(0~100)；onUploaded 在字节发完时触发（服务端可能还在刮削，用于切换等待提示）；
// fields 透传为 query 参数（如 {relpath, target_dir}）；
// 返回 { promise, abort }，abort 后 promise 以 '已取消' 拒绝。
export function apiUpload(path, file, { onProgress, onUploaded, subdir = '', fields = {} } = {}) {
  let xhr = null
  let uploadedFired = false
  const fireUploaded = () => {
    if (uploadedFired) return
    uploadedFired = true
    try { if (onUploaded) onUploaded() } catch (e) { /* 忽略 */ }
  }
  const promise = new Promise((resolve, reject) => {
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
            onProgress(Math.round((e.loaded / e.total) * 100))
            if (e.loaded >= e.total) fireUploaded()
          }
        }
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)) }
        catch (e) { reject(new Error('上传响应解析失败（服务端可能异常，请刷新查看）')) }
      } else {
        reject(new Error(`${xhr.status} ${_brief(xhr.responseText)}`))
      }
    }
    xhr.onerror = () => reject(new Error('上传失败：网络错误'))
    xhr.onabort = () => reject(new Error('已取消'))
    xhr.ontimeout = () => reject(new Error('上传超时'))
    const fd = new FormData()
    fd.append('file', file, file.name)
    xhr.send(fd)
  })
  return { promise, abort: () => { try { xhr && xhr.abort() } catch (e) { /* 忽略 */ } } }
}
