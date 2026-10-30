// Session-only catalogue snapshots. A successful sources read must supply both
// the device scope and the exact source/network configuration before reuse.
const MAX_BYTES = 2 * 1024 * 1024
const MAX_PAGES = 12
export const ONLINE_BROWSE_FRESH_MS = 15 * 60 * 1000
export const ONLINE_BROWSE_NAVIGATION_MS = 30 * 60 * 1000
const DATA_TTL = ONLINE_BROWSE_FRESH_MS
const NAVIGATION_TTL = ONLINE_BROWSE_NAVIGATION_MS
const SOURCE = /^src_[0-9a-f]{32}$/
const TOKEN = /^[0-9a-f]{64}$/
const SCOPE = /^[0-9a-f]{64}:(?:normal|private):\d+:\d+$/
const clone = value => JSON.parse(JSON.stringify(value))
const identity = value => typeof value === 'string' && /^[A-Za-z0-9_-]{0,128}$/.exec(value)?.[0] === value
const validYear = value => typeof value === 'string' && (!value || /^[1-9][0-9]{3}$/.exec(value)?.[0] === value)

function validSelections(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  if (![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false
  const entries = Object.entries(value)
  return entries.length <= 256 && entries.every(([channel, category]) => channel && category && identity(channel) && identity(category))
}

export function onlineBrowseScope(token, viewing = {}) {
  if (!TOKEN.test(token || '') || !['normal', 'private'].includes(viewing.mode)) return ''
  return `${token}:${viewing.mode}:${Number(viewing.generation) || 0}:${Number(viewing.revision) || 0}`
}

function displayed(response) {
  const fields = ['external_id', 'title', 'kind', 'year', 'description', 'poster_path', 'category', 'category_id',
    'parent_category_id', 'remarks', 'genres', 'actors', 'directors', 'area', 'language', 'subtitle']
  return JSON.stringify({ items: response.items.map(item => Object.fromEntries(fields
    .filter(field => item[field] !== undefined).map(field => [field, item[field]]))),
    page: response.page, page_count: response.page_count, total: response.total,
    categories: Array.isArray(response.categories) ? response.categories.map(row => ({ id: row.id, name: row.name, parent_id: row.parent_id || '' })) : [] })
}

export function onlineBrowseResponseEqual(current, next) {
  try {
    const categories = Array.isArray(next.categories) && next.categories.length ? next.categories : current.categories
    return displayed(current) === displayed({ ...next, categories })
  } catch { return false }
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
  function key(scope, source, categoryId, query, page, year, channelId) {
    return JSON.stringify([scope, source.id, source.configuration_token, categoryId, query, page, year, channelId])
  }
  const sizeOf = value => new TextEncoder().encode(JSON.stringify(value)).byteLength
  const hasCategories = rows => Array.isArray(rows) && rows.length > 0
  const matches = (entry, scope, source) => entry.scope === scope && entry.value.sourceId === source.id
    && entry.value.configuration === source.configuration_token
  function selectionKey(scope, source, { categoryId = '', query = '', page = 1, year = '', channelId = '' } = {}) {
    if (!valid(scope, source) || !identity(categoryId) || !identity(channelId) || !validYear(year)
      || year && source.capabilities?.year_filter !== true || typeof query !== 'string' || query.length > 100
      || !Number.isInteger(page) || page < 1 || page > 1000) return null
    return key(scope, source, categoryId, query, page, year, channelId)
  }
  function read(entry) {
    if (!entry) return null
    const result = clone(entry.value)
    result.categories = Array.isArray(result.response.categories) ? result.response.categories : []
    result.stale = clock() - result.loadedAt >= dataTtl
    // Navigation may be remembered again without reviving a 30-minute-old row.
    if (clock() - result.loadedAt >= navigationTtl) result.response = null
    if (result.pending && clock() - result.pendingLoadedAt >= navigationTtl) result.pending = null
    return result
  }
  function replaceValue(selected, value) {
    const entry = entries.get(selected)
    if (!entry) return false
    const bytes = sizeOf(value)
    if (bytes > maxBytes) return false
    while (totalBytes - entry.bytes + bytes > maxBytes) {
      const victim = [...entries.keys()].find(candidate => candidate !== selected)
      if (!victim) return false
      remove(victim)
    }
    totalBytes += bytes - entry.bytes
    entry.value = value; entry.bytes = bytes
    return true
  }
  function responseValue(response, previous) {
    const result = clone(response)
    // Background-control fields do not belong to the cached catalogue body.
    delete result.status
    if (!hasCategories(result.categories) && hasCategories(previous?.categories)) result.categories = clone(previous.categories)
    return result
  }
  function propagateTaxonomy(scope, source, taxonomy) {
    const serialized = JSON.stringify(taxonomy)
    for (const entry of entries.values()) {
      if (!matches(entry, scope, source)) continue
      if (JSON.stringify(entry.value.response.categories) === serialized
        && (!entry.value.pending || JSON.stringify(entry.value.pending.categories) === serialized)) continue
      entry.value.response.categories = clone(taxonomy)
      // The latest applied source tree also supersedes older staged trees.
      if (entry.value.pending) entry.value.pending.categories = clone(taxonomy)
      const updated = sizeOf(entry.value)
      totalBytes += updated - entry.bytes; entry.bytes = updated
    }
  }
  return {
    save({ scope, source, categoryId = '', query = '', inputQuery = query, year = '', channelId = '', channelSelections = {},
      response, loadedAt = clock(), position = null }) {
      if (!valid(scope, source) || !identity(categoryId) || !identity(channelId) || !validYear(year) || year && source.capabilities?.year_filter !== true || !validSelections(channelSelections)
        || typeof query !== 'string' || query.length > 100 || typeof inputQuery !== 'string' || inputQuery.length > 100
        || !response || !Number.isInteger(response.page) || response.page < 1 || response.page > 1000
        || !Array.isArray(response.items) || response.items.length > 100 || !Number.isFinite(loadedAt)) return false
      prune()
      const selected = key(scope, source, categoryId, query, response.page, year, channelId)
      const previous = entries.get(selected)
      let value, bytes
      try {
        value = clone({ sourceId: source.id, configuration: source.configuration_token, categoryId, query, year, channelId, channelSelections,
          inputQuery, page: response.page, response: responseValue(response), loadedAt, position,
          lastAttemptAt: loadedAt, hasUpdate: false, pending: null, pendingLoadedAt: null })
        // Remembering position must not discard an in-flight check's attempt or
        // a staged result, nor downgrade an unchanged validated page's age.
        if (previous && loadedAt <= previous.value.loadedAt
          && displayed(responseValue(value.response, previous.value.response)) === displayed(previous.value.response)) {
          value.loadedAt = previous.value.loadedAt
          for (const field of ['lastAttemptAt', 'hasUpdate', 'pending', 'pendingLoadedAt']) value[field] = clone(previous.value[field])
        }
      } catch { return false }
      const previousTaxonomy = [...entries.values()]
        .find(entry => matches(entry, scope, source) && hasCategories(entry.value.response.categories))?.value.response.categories
      const taxonomy = hasCategories(value.response.categories) ? value.response.categories : previousTaxonomy
      if (taxonomy) value.response.categories = clone(taxonomy)
      bytes = sizeOf(value)
      if (bytes > maxBytes) {
        if (taxonomy && JSON.stringify(taxonomy) !== JSON.stringify(previousTaxonomy)) {
          for (const [candidate, entry] of entries) if (matches(entry, scope, source)) remove(candidate)
        }
        return false
      }
      if (taxonomy) propagateTaxonomy(scope, source, taxonomy)
      if (entries.has(selected)) remove(selected)
      while (entries.size && (entries.size >= maxPages || totalBytes + bytes > maxBytes)) remove(entries.keys().next().value)
      entries.set(selected, { value, bytes, savedAt: clock(), scope })
      totalBytes += bytes
      return true
    },
    recordAttempt(scope, source, selection = {}, attemptedAt = clock()) {
      if (!Number.isFinite(attemptedAt)) return false
      prune()
      const selected = selectionKey(scope, source, selection)
      const entry = selected && entries.get(selected)
      if (!entry) return false
      const value = clone(entry.value)
      value.lastAttemptAt = Math.max(value.lastAttemptAt, attemptedAt)
      return replaceValue(selected, value)
    },
    stage(scope, source, selection = {}, response, checkedAt = clock()) {
      prune()
      const selected = selectionKey(scope, source, selection)
      const entry = selected && entries.get(selected)
      if (!entry || !Number.isFinite(checkedAt) || !response || !Array.isArray(response.items)
        || response.items.length > 100 || response.page !== entry.value.page) return null
      let next, changed
      try {
        next = responseValue(response, entry.value.response)
        changed = displayed(next) !== displayed(entry.value.response)
      } catch { return null }
      const value = clone(entry.value)
      value.lastAttemptAt = Math.max(value.lastAttemptAt, checkedAt)
      value.hasUpdate = changed
      value.pending = changed ? next : null
      value.pendingLoadedAt = changed ? checkedAt : null
      if (!changed) value.loadedAt = checkedAt
      let cached = changed && replaceValue(selected, value)
      if (!changed || !cached) {
        value.pending = null; value.pendingLoadedAt = null
        if (!replaceValue(selected, value)) return null
        cached = false
      }
      if (!changed) entry.savedAt = checkedAt
      return { changed, cached }
    },
    applyPending(scope, source, selection = {}) {
      prune()
      const selected = selectionKey(scope, source, selection)
      const entry = selected && entries.get(selected)
      if (!entry?.value.pending) return null
      if (clock() - entry.value.pendingLoadedAt >= navigationTtl) {
        const expired = clone(entry.value); expired.pending = null; expired.pendingLoadedAt = null
        replaceValue(selected, expired)
        return null
      }
      const value = clone(entry.value)
      value.response = value.pending; value.loadedAt = value.pendingLoadedAt
      value.pending = null; value.pendingLoadedAt = null; value.hasUpdate = false
      if (!replaceValue(selected, value)) return null
      if (hasCategories(value.response.categories)) propagateTaxonomy(scope, source, value.response.categories)
      // Propagation is metadata-only and must stay within the existing cap.
      while (totalBytes > maxBytes) {
        const victim = [...entries.keys()].find(candidate => candidate !== selected)
        if (!victim) break
        remove(victim)
      }
      return read(entries.get(selected))
    },
    clearPending(scope, source, selection = {}) {
      prune()
      const selected = selectionKey(scope, source, selection)
      const entry = selected && entries.get(selected)
      if (!entry) return false
      const value = clone(entry.value)
      value.hasUpdate = false; value.pending = null; value.pendingLoadedAt = null
      return replaceValue(selected, value)
    },
    read(scope, source, { categoryId = '', query = '', page = 1, year = '', channelId = '' } = {}) {
      if (!valid(scope, source) || !identity(categoryId) || !identity(channelId) || !validYear(year)
        || year && source.capabilities?.year_filter !== true) return null
      prune()
      const selected = key(scope, source, categoryId, query, page, year, channelId)
      const entry = entries.get(selected)
      if (!entry) return null
      entries.delete(selected); entries.set(selected, entry)
      return read(entry)
    },
    last(scope, sources, sourceId = '') {
      if (!SCOPE.test(scope || '') || !Array.isArray(sources)) return null
      prune()
      const available = new Map(sources.filter(source => valid(scope, source)).map(source => [source.id, source]))
      // This list has just passed the server's permission/configuration check.
      // Retire invalid bindings rather than reviving them if a setting reverts.
      for (const [selected, entry] of entries) {
        const source = available.get(entry.value.sourceId)
        if (!source || !matches(entry, scope, source)
          || entry.value.year && source.capabilities?.year_filter !== true) remove(selected)
      }
      for (const entry of [...entries.values()].reverse()) {
        const value = entry.value
        if (entry.scope === scope && (!sourceId || value.sourceId === sourceId)
          && available.get(value.sourceId)?.configuration_token === value.configuration
          && (!value.year || available.get(value.sourceId)?.capabilities?.year_filter === true)) return read(entry)
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
