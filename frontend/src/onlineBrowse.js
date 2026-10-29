// Session-only catalogue snapshots. A successful sources read must supply both
// the device scope and the exact source/network configuration before reuse.
const MAX_BYTES = 2 * 1024 * 1024
const MAX_PAGES = 12
const DATA_TTL = 60 * 1000
const NAVIGATION_TTL = 30 * 60 * 1000
const SOURCE = /^src_[0-9a-f]{32}$/
const TOKEN = /^[0-9a-f]{64}$/
const SCOPE = /^[0-9a-f]{64}:(?:normal|private):\d+:\d+$/
const clone = value => JSON.parse(JSON.stringify(value))

export function onlineBrowseScope(token, viewing = {}) {
  if (!TOKEN.test(token || '') || !['normal', 'private'].includes(viewing.mode)) return ''
  return `${token}:${viewing.mode}:${Number(viewing.generation) || 0}:${Number(viewing.revision) || 0}`
}

export function createOnlineBrowseMemory({ clock = Date.now, maxBytes = MAX_BYTES, maxPages = MAX_PAGES,
  dataTtl = DATA_TTL, navigationTtl = NAVIGATION_TTL } = {}) {
  if (!Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > MAX_BYTES
    || !Number.isInteger(maxPages) || maxPages < 1 || maxPages > MAX_PAGES
    || !(dataTtl > 0 && dataTtl <= DATA_TTL) || !(navigationTtl >= dataTtl && navigationTtl <= NAVIGATION_TTL)) throw new Error('在线浏览缓存上限无效')
  const entries = new Map()
  let totalBytes = 0
  function remove(key) { totalBytes -= entries.get(key)?.bytes || 0; entries.delete(key) }
  function prune() {
    const now = clock()
    for (const [key, entry] of entries) if (now - entry.savedAt >= navigationTtl) remove(key)
  }
  function valid(scope, source) { return SCOPE.test(scope || '') && SOURCE.test(source?.id || '') && TOKEN.test(source?.configuration_token || '') }
  function key(scope, source, categoryId, query, page) { return JSON.stringify([scope, source.id, source.configuration_token, categoryId, query, page]) }
  function read(entry) {
    if (!entry) return null
    const result = clone(entry.value)
    if (clock() - result.loadedAt >= dataTtl) result.response = null
    return result
  }
  return {
    save({ scope, source, categoryId = '', query = '', inputQuery = query, response, loadedAt = clock(), position = null }) {
      if (!valid(scope, source) || typeof categoryId !== 'string' || !/^[A-Za-z0-9_-]{0,128}$/.test(categoryId)
        || typeof query !== 'string' || query.length > 100 || typeof inputQuery !== 'string' || inputQuery.length > 100
        || !response || !Number.isInteger(response.page) || response.page < 1 || response.page > 1000
        || !Array.isArray(response.items) || response.items.length > 100 || !Number.isFinite(loadedAt)) return false
      let value, bytes
      try {
        value = clone({ sourceId: source.id, configuration: source.configuration_token, categoryId, query,
          inputQuery, page: response.page, response, loadedAt, position })
        bytes = new TextEncoder().encode(JSON.stringify(value)).byteLength
      } catch { return false }
      if (bytes > maxBytes) return false
      prune()
      const selected = key(scope, source, categoryId, query, response.page)
      if (entries.has(selected)) remove(selected)
      while (entries.size && (entries.size >= maxPages || totalBytes + bytes > maxBytes)) remove(entries.keys().next().value)
      entries.set(selected, { value, bytes, savedAt: clock(), scope })
      totalBytes += bytes
      return true
    },
    read(scope, source, { categoryId = '', query = '', page = 1 } = {}) {
      if (!valid(scope, source)) return null
      prune()
      const selected = key(scope, source, categoryId, query, page)
      const entry = entries.get(selected)
      if (!entry) return null
      entries.delete(selected); entries.set(selected, entry)
      return read(entry)
    },
    last(scope, sources, sourceId = '') {
      if (!SCOPE.test(scope || '') || !Array.isArray(sources)) return null
      prune()
      const available = new Map(sources.filter(source => valid(scope, source)).map(source => [source.id, source.configuration_token]))
      for (const entry of [...entries.values()].reverse()) {
        const value = entry.value
        if (entry.scope === scope && (!sourceId || value.sourceId === sourceId)
          && available.get(value.sourceId) === value.configuration) return read(entry)
      }
      return null
    },
    clear() { entries.clear(); totalBytes = 0 },
    stats: () => ({ pages: entries.size, bytes: totalBytes }),
  }
}

export const onlineBrowseMemory = createOnlineBrowseMemory()
export function clearOnlineBrowse() { onlineBrowseMemory.clear() }

export function captureOnlinePosition(root, viewport = globalThis.window) {
  if (!root?.querySelectorAll || !viewport) return null
  const item = [...root.querySelectorAll('[data-online-browse-id]')].find(element => element.getBoundingClientRect().bottom > 0)
  return { scrollTop: viewport.scrollY || 0, anchor: item
    ? { id: item.dataset.onlineBrowseId, offset: item.getBoundingClientRect().top } : null }
}

export function restoreOnlinePosition(position, root, viewport = globalThis.window) {
  if (!position || !viewport?.scrollTo || !root?.querySelectorAll) return
  const item = position.anchor && [...root.querySelectorAll('[data-online-browse-id]')]
    .find(element => element.dataset.onlineBrowseId === position.anchor.id)
  const top = item ? (viewport.scrollY || 0) + item.getBoundingClientRect().top - position.anchor.offset : position.scrollTop || 0
  viewport.scrollTo({ top: Math.max(0, top), behavior: 'instant' })
}
