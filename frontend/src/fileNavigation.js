// Only same-tab, explicitly opened file workspaces have a return destination.
export const FILE_ORIGIN_KEY = 'jzmedia.files.origin.v1'
const positive = value => Number.isInteger(Number(value)) && Number(value) > 0 ? Number(value) : null

export function normalizeFileOrigin(value) {
  if (!value || value.version !== 1 || typeof value.token !== 'string' || !/^[\w-]{1,80}$/.test(value.token)) return null
  const library = positive(value.library)
  if (!library) return null
  const kind = value.kind === 'tv' ? 'tv' : 'movie'
  const views = kind === 'tv' ? ['workflow', 'maintenance'] : ['workflow', 'maintenance', 'restore']
  const steps = kind === 'tv' ? ['', 'scan', 'match', 'organize'] : ['', 'scan', 'pending', 'organize']
  return {
    version: 1, token: value.token, library, media: positive(value.media), kind,
    view: views.includes(value.view) ? value.view : 'workflow',
    step: steps.includes(value.step) ? value.step : 'scan',
    scrollTop: Math.max(0, Number.isFinite(Number(value.scrollTop)) ? Number(value.scrollTop) : 0),
    ids: Array.isArray(value.ids) ? [...new Set(value.ids.map(positive).filter(Boolean))] : [],
  }
}

export function createFileOrigin(tab, state, token) {
  return normalizeFileOrigin({ ...state, version: 1, token, library: tab.id, media: tab.media_id,
    kind: tab.kind })
}

export function readFileOrigin(storage) {
  try { return normalizeFileOrigin(JSON.parse((storage || globalThis.sessionStorage)?.getItem(FILE_ORIGIN_KEY) || 'null')) }
  catch { return null }
}
export function writeFileOrigin(value, storage) {
  const snapshot = normalizeFileOrigin(value)
  try {
    const target = storage || globalThis.sessionStorage
    if (snapshot) target?.setItem(FILE_ORIGIN_KEY, JSON.stringify(snapshot))
    else target?.removeItem(FILE_ORIGIN_KEY)
  } catch { /* Storage restrictions keep the current in-memory return destination usable. */ }
  return snapshot
}
export function fileWorkspaceTarget(origin) {
  const query = { sec: 'sec-files', library: String(origin.library), files_from: origin.token }
  if (origin.media != null) query.media = String(origin.media)
  return { path: '/settings', query }
}
export function fileReturnTarget(origin) {
  const query = { sec: 'sec-libtools', library: String(origin.library), files_return: origin.token }
  if (origin.media != null) query.media = String(origin.media)
  return { path: '/settings', query }
}
export function fileOriginMatches(origin, route) {
  return !!origin && route.path === '/settings' && route.query?.sec === 'sec-files' && route.query.files_from === origin.token
}
export function fileOriginTransition(origin, to, from, failure = false) {
  if (failure) return { origin, restore: null }
  if (fileOriginMatches(origin, to)) return { origin, restore: null }
  const returning = fileOriginMatches(origin, from) && to.path === '/settings'
    && to.query?.sec === 'sec-libtools' && Number(to.query.library) === origin.library
    && to.query.files_return === origin.token
  return { origin: null, restore: returning ? origin : null }
}
