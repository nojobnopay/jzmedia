export async function api(path, opts = {}) {
  const { timeout = 120000, ...fetchOpts } = opts
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  try {
    const r = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...fetchOpts,
      signal: ctrl.signal
    })
    if (!r.ok) {
      const t = await r.text()
      throw new Error(`${r.status} ${t}`)
    }
    return r.json()
  } catch (e) {
    if (e && e.name === 'AbortError') {
      throw new Error('请求超时，后台可能仍在处理，稍后刷新查看')
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}
export const posterUrl = (p) => (p ? `/posters/${p.split('/').pop()}` : '')

// 大文件上传专用：FormData + XHR（支持进度与中断取消；>2GB 局域网场景）。
// onProgress(0~100)；fields 透传为 query 参数（如 {relpath, target_dir}）；
// 返回 { promise, abort }，abort 后 promise 以 '已取消' 拒绝。
export function apiUpload(path, file, { onProgress, subdir = '', fields = {} } = {}) {
  let xhr = null
  const promise = new Promise((resolve, reject) => {
    xhr = new XMLHttpRequest()
    const qs = new URLSearchParams()
    if (subdir) qs.set('subdir', subdir)
    for (const [k, v] of Object.entries(fields || {})) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, v)
    }
    const url = qs.toString() ? `${path}?${qs}` : path
    xhr.open('POST', url)
    xhr.timeout = 0 // 大文件不限时，由用户手动取消
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)) }
        catch (e) { resolve({}) }
      } else {
        reject(new Error(`${xhr.status} ${xhr.responseText}`.slice(0, 300)))
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
