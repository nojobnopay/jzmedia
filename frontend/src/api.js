export async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  })
  if (!r.ok) {
    const t = await r.text()
    throw new Error(`${r.status} ${t}`)
  }
  return r.json()
}
export const posterUrl = (p) => (p ? `/posters/${p.split('/').pop()}` : '')
