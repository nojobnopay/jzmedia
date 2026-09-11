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
