// Bounded, session-only history. Keys include the effective filters, sort and media library.
import { clearOnlineBrowse } from './onlineBrowse.js'

import { clearAiNavigation } from './aiDiscoveryNav.js'
import { clearAiDiscovery } from './useAiDiscovery.js'

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
  const focused = globalThis.document?.activeElement?.closest('[data-browse-focus], [data-browse-id]')
  const focus = focused?.dataset.browseFocus
    ? { entry: focused.dataset.browseFocus }
    : focused ? { poster: focused.dataset.browseId } : null
  snapshots.set(key, clone({ ...snapshot, focus }))
  returns.set(`${snapshot.path}:${snapshot.mediaId ?? ''}`, { path: snapshot.path, query: clone(snapshot.query) })
  while (snapshots.size > MAX) snapshots.delete(snapshots.keys().next().value)
}
export function readBrowse(key) { return snapshots.has(key) ? clone(snapshots.get(key)) : null }
export function browseReturn(path, mediaId) {
  return returns.get(`${path}:${mediaId ?? ''}`) || { path, query: mediaId == null ? {} : { media: mediaId } }
}
export function clearBrowse() { snapshots.clear(); returns.clear(); clearOnlineBrowse(); clearAiNavigation(); clearAiDiscovery() }
export function captureAnchor(root = document) {
  const el = [...root.querySelectorAll('[data-browse-id]')].find(el => el.getBoundingClientRect().bottom > 0)
  return el ? { id: el.dataset.browseId, offset: el.getBoundingClientRect().top } : null
}
export function restoreBrowsePosition(snapshot) {
  const posters = [...document.querySelectorAll('[data-browse-id]')]
  const el = snapshot?.anchor && posters.find(el => el.dataset.browseId === snapshot.anchor.id)
  const top = el ? window.scrollY + el.getBoundingClientRect().top - snapshot.anchor.offset : snapshot?.scrollTop || 0
  window.scrollTo({ top: Math.max(0, top), behavior: 'instant' })
  // The list can finish restoring after the user has already focused search or a dialog.
  if (!snapshot?.focus || (document.activeElement !== document.body && document.activeElement?.id !== 'main-content')) return
  const target = snapshot.focus.entry
    ? [...document.querySelectorAll('[data-browse-focus]')].find(item => item.dataset.browseFocus === snapshot.focus.entry)
    : posters.find(item => item.dataset.browseId === snapshot.focus.poster)
  if (!target) return
  target.focus({ preventScroll: true })
  const row = target.closest('.cw-row')
  if (row) {
    const card = target.getBoundingClientRect()
    const bounds = row.getBoundingClientRect()
    if (card.left < bounds.left) row.scrollLeft += card.left - bounds.left
    else if (card.right > bounds.right) row.scrollLeft += card.right - bounds.right
  }
}
