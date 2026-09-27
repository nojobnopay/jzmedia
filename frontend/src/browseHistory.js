// Bounded, session-only history. Keys include the effective filters, sort and media library.
const snapshots = new Map()
const returns = new Map()
const MAX = 12
const clone = value => JSON.parse(JSON.stringify(value))
export function browseKey(path, mediaId, params) {
  const p = new URLSearchParams(params)
  p.delete('offset')
  p.delete('limit')
  p.sort()
  return `${path}:${mediaId ?? ''}:${p}`
}
export function saveBrowse(key, snapshot) {
  snapshots.delete(key)
  snapshots.set(key, clone(snapshot))
  returns.set(`${snapshot.path}:${snapshot.mediaId ?? ''}`, { path: snapshot.path, query: clone(snapshot.query) })
  while (snapshots.size > MAX) snapshots.delete(snapshots.keys().next().value)
}
export function readBrowse(key) { return snapshots.has(key) ? clone(snapshots.get(key)) : null }
export function browseReturn(path, mediaId) {
  return returns.get(`${path}:${mediaId ?? ''}`) || { path, query: mediaId == null ? {} : { media: mediaId } }
}
export function clearBrowse() { snapshots.clear(); returns.clear() }
export function captureAnchor(root = document) {
  const el = [...root.querySelectorAll('[data-browse-id]')].find(el => el.getBoundingClientRect().bottom > 0)
  return el ? { id: el.dataset.browseId, offset: el.getBoundingClientRect().top } : null
}
export function restoreBrowsePosition(snapshot) {
  const el = snapshot?.anchor && [...document.querySelectorAll('[data-browse-id]')].find(el => el.dataset.browseId === snapshot.anchor.id)
  const top = el ? window.scrollY + el.getBoundingClientRect().top - snapshot.anchor.offset : snapshot?.scrollTop || 0
  window.scrollTo({ top: Math.max(0, top), behavior: 'instant' })
}
