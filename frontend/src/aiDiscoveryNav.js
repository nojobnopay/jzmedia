// Only an opaque reference crosses the router. Titles and the return location
// live in this tab and disappear when the viewing scope changes.
import { mediaSelectionRevision } from './libraries.js'

const origins = new Map()
let libraryRevision = mediaSelectionRevision()
function syncLibrary() {
  if (libraryRevision !== mediaSelectionRevision()) {
    clearAiNavigation()
    libraryRevision = mediaSelectionRevision()
  }
}
const tabId = Math.random().toString(36).slice(2)
let sequence = 0
let currentVisit = null

function destination(hit) {
  return /^\/(?:m\/\d+|tv\/\d+(?:\/s\/\d+(?:\/e\/\d+)?)?)$/.test(hit.detail_path || '') ? hit.detail_path : null
}

export function rememberAiNavigation(hit, route) {
  syncLibrary()
  if (!['/', '/tv'].includes(route.path) || !hit?.key) return
  sequence++
  currentVisit = { ref: tabId + sequence, key: hit.key, from: route.fullPath }
  origins.set(currentVisit.ref, { origin: route.fullPath, path: destination(hit), title: hit.title,
    version: Number(hit.version_id) || null })
  if (origins.size > 32) origins.delete(origins.keys().next().value)
}

function target(path, hit, route) {
  // A normal router-link resolves again after its click listener recorded the
  // origin. Use a getter so Vue Router reads the reference at navigation time.
  return { path, get state() {
    syncLibrary()
    return currentVisit?.key === hit.key && currentVisit?.from === route.fullPath
      ? { aiVisit: currentVisit.ref } : {}
  } }
}

export function aiLocalDetailTarget(hit, route) { return target(destination(hit) || route.path, hit, route) }
export function aiOnlineTarget(hit, route) { return target('/online', hit, route) }
export function aiNavigationContext(path, state = globalThis.history?.state) {
  syncLibrary()
  const visit = origins.get(state?.aiVisit)
  return visit && (path === '/online' || path === visit.path) ? visit : null
}
export function clearAiNavigation() { origins.clear(); currentVisit = null }
