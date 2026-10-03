// Browser-only presentation history. Airing checks and collection state belong to the server.
export const TV_UPDATES_WEEK = 7 * 86400000
export const TV_UPDATES_RETENTION = 30 * 86400000
const PREFIX = 'jzmedia.tvUpdates.v1.'

export function tvUpdateEventKey(item, event) {
  const id = event?.event_id || event?.tmdb_episode_id || `${event?.season}:${event?.episode}`
  return `${item.tmdb_id}:${id}`
}

export function normalizeTvUpdatesHistory(value, now = Date.now()) {
  const seen = Object.fromEntries(Object.entries(value?.seen || {}).filter(([key, timestamp]) =>
    key && Number.isFinite(timestamp) && timestamp > now - TV_UPDATES_RETENTION && timestamp <= now))
  const lastShown = Number(value?.lastShown) || 0
  return { seen, lastShown: lastShown > 0 && lastShown <= now ? lastShown : 0 }
}

export function loadTvUpdatesHistory(storage, mediaId, now = Date.now()) {
  try { return normalizeTvUpdatesHistory(JSON.parse(storage.getItem(PREFIX + mediaId) || '{}'), now) }
  catch { return normalizeTvUpdatesHistory(null, now) }
}

export function saveTvUpdatesHistory(storage, mediaId, value, now = Date.now()) {
  // Another tab may have acknowledged different visible cards since this view
  // loaded. Merge at write time so an older view cannot erase those exposures.
  const incoming = normalizeTvUpdatesHistory(value, now)
  const stored = loadTvUpdatesHistory(storage, mediaId, now)
  const seen = new Map(Object.entries(stored.seen))
  for (const [key, timestamp] of Object.entries(incoming.seen)) {
    seen.set(key, Math.max(seen.get(key) || 0, timestamp))
  }
  const state = { seen: Object.fromEntries(seen), lastShown: Math.max(stored.lastShown, incoming.lastShown) }
  try { storage.setItem(PREFIX + mediaId, JSON.stringify(state)) } catch { /* Private browsing remains usable. */ }
  return state
}

export function unseenTvUpdates(items, history, now = Date.now()) {
  const { seen } = normalizeTvUpdatesHistory(history, now)
  const known = new Set()
  return (items || []).filter(item => item?.show_id && item?.tmdb_id).map(item => ({
    ...item,
    events: (item.events || []).filter(event => {
      const key = tvUpdateEventKey(item, event)
      if (known.has(key) || seen[key]) return false
      known.add(key)
      return true
    }).sort((a, b) => String(b.air_date || '').localeCompare(String(a.air_date || '')) || Number(b.episode) - Number(a.episode)),
  })).filter(item => item.events.length)
    .sort((a, b) => String(b.events[0].air_date || '').localeCompare(String(a.events[0].air_date || '')) || Number(a.tmdb_id) - Number(b.tmdb_id))
}

export function shouldExpandTvUpdates(items, history, mode = 'weekly', now = Date.now()) {
  return mode === 'weekly' && items.length > 0 &&
    (!history.lastShown || now - history.lastShown >= TV_UPDATES_WEEK)
}

export function markTvUpdatesSeen(history, visibleItems, now = Date.now(), startInterval = true) {
  const state = normalizeTvUpdatesHistory(history, now)
  let changed = false
  for (const item of visibleItems) {
    for (const event of item.events || []) {
      const key = tvUpdateEventKey(item, event)
      if (!state.seen[key]) { state.seen[key] = now; changed = true }
    }
  }
  if (changed && startInterval) state.lastShown = now
  return state
}

// A return from detail keeps the same batch, removing entries collected in the meantime.
export function restoreTvUpdatesBatch(snapshot, freshItems, mediaId) {
  if (!snapshot || Number(snapshot.mediaId) !== Number(mediaId)) return null
  const fresh = new Map((freshItems || []).map(item => [Number(item.tmdb_id), item]))
  return (snapshot.items || []).flatMap(old => {
    const item = fresh.get(Number(old.tmdb_id))
    if (!item) return []
    const events = new Set((old.events || []).map(event => tvUpdateEventKey(old, event)))
    const current = (item.events || []).filter(event => events.has(tvUpdateEventKey(item, event)))
    return current.length ? [{ ...item, events: current }] : []
  })
}

export function tvUpdateEpisodeLabel(event) {
  if (!event) return ''
  return `S${String(event.season).padStart(2, '0')}E${String(event.episode).padStart(2, '0')}`
}
