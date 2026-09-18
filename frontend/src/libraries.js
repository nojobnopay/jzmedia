// 多库状态（MULTI_LIBRARY_PLAN）：当前库 = 最近使用（localStorage），无则默认库。
// 纯模块状态 + 订阅，不依赖 Vue，便于 node --test 直测。
const LIB_KEY = 'jzmedia.lib'

let _libs = []
let _defaultId = null
let _current = null
let _loading = null
const _listeners = new Set()

export function pickLibrary (items, storedId, defaultId) {
  const list = Array.isArray(items) ? items : []
  if (!list.length) return null
  const sid = storedId == null ? null : Number(storedId)
  return list.find((l) => Number(l.id) === sid) ||
    list.find((l) => Number(l.id) === Number(defaultId)) ||
    list.find((l) => l.enabled !== false) ||
    list[0]
}

export function getStoredLibId () {
  try {
    const v = localStorage.getItem(LIB_KEY)
    return v == null || v === '' ? null : Number(v)
  } catch (e) { return null }
}

export function setStoredLibId (id) {
  try {
    if (id == null) localStorage.removeItem(LIB_KEY)
    else localStorage.setItem(LIB_KEY, String(id))
  } catch (e) { /* 忽略 */ }
}

export function listLibs () { return _libs }

export function currentLib () { return _current }

export function currentLibId () {
  return _current ? Number(_current.id) : null
}

// URL/API 参数：仅在多于一个库时带上 library，单库保持 URL 干净
export function libParam () {
  return _libs.length > 1 ? currentLibId() : null
}

export function withLib (params = {}) {
  const id = libParam()
  if (id == null) return { ...params }
  return { ...params, library: id }
}

export function onLibChange (fn) {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}

function _notify () {
  for (const fn of Array.from(_listeners)) {
    try { fn(_current) } catch (e) { /* 单个订阅失败不影响其他 */ }
  }
}

export function applyLibs (data) {
  _libs = (data && data.items) || []
  _defaultId = data ? data.default_id : null
  _current = pickLibrary(_libs, getStoredLibId(), _defaultId)
  try {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('jzmedia:libraries-changed'))
    }
  } catch (e) { /* 忽略 */ }
  return _current
}

export async function loadLibs (apiFn, { force = false } = {}) {
  if (_loading && !force) return _loading
  _loading = apiFn('/api/libraries')
    .then((data) => applyLibs(data))
    .finally(() => { _loading = null })
  return _loading
}

export function switchLib (id) {
  const lib = _libs.find((l) => Number(l.id) === Number(id))
  if (!lib) return null
  _current = lib
  setStoredLibId(lib.id)
  _notify()
  return lib
}

export function resetLibState () {
  _libs = []
  _defaultId = null
  _current = null
  _loading = null
  _listeners.clear()
}
