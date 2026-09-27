// 多库状态（v18 媒体库级）：当前选择 = 媒体库（NAS / media），电影墙/剧集页按它聚合；
// 视频库仅用于工具页（扫描/整理/文件浏览）与上传目标。纯模块状态 + 订阅，不依赖 Vue。
const LIB_KEY = 'jzmedia.lib'      // 旧键：视频库 id（迁移回退 + 工具页记忆）
const MEDIA_KEY = 'jzmedia.media'  // 当前媒体库 id

let _libs = []          // 视频库（GET /api/libraries 的 items）
let _mediaLibs = []     // 媒体库（由视频库按 media_library_id 分组派生）
let _defaultId = null   // 默认视频库 id（服务端 default_id）
let _current = null     // 当前媒体库
let _loading = null
const _listeners = new Set()

function _readKey (key) {
  try {
    const v = localStorage.getItem(key)
    return v == null || v === '' ? null : Number(v)
  } catch (e) { return null }
}

function _writeKey (key, id) {
  try {
    if (id == null) localStorage.removeItem(key)
    else localStorage.setItem(key, String(id))
  } catch (e) { /* 忽略 */ }
}

export function getStoredLibId () { return _readKey(LIB_KEY) }
export function setStoredLibId (id) { _writeKey(LIB_KEY, id) }
export function getStoredMediaId () { return _readKey(MEDIA_KEY) }
export function setStoredMediaId (id) { _writeKey(MEDIA_KEY, id) }

// 视频库列表按媒体库分组 → 媒体库条目（保持服务端顺序；enabled 取媒体库开关）
export function buildMediaLibs (libs) {
  const groups = []
  const idx = new Map()
  for (const l of (libs || [])) {
    const key = Number(l.media_library_id) || 0
    if (!idx.has(key)) {
      idx.set(key, groups.length)
      groups.push({
        id: key || null,
        name: l.media_name || l.name || '',
        source: l.source || 'local',
        read_only: !!l.read_only,
        enabled: l.media_enabled !== false,
        video_libraries: [],
      })
    }
    groups[idx.get(key)].video_libraries.push(l)
  }
  return groups
}

export function pickMedia (items, storedId, fallbackId) {
  const list = Array.isArray(items) ? items : []
  if (!list.length) return null
  const sid = storedId == null ? null : Number(storedId)
  return list.find((m) => Number(m.id) === sid) ||
    list.find((m) => Number(m.id) === Number(fallbackId)) ||
    list.find((m) => m.enabled !== false) ||
    list[0]
}

// 兼容旧调用（视频库级选择）：存储值优先 → 默认库 → 第一个启用库
export function pickLibrary (items, storedId, defaultId) {
  const list = Array.isArray(items) ? items : []
  if (!list.length) return null
  const sid = storedId == null ? null : Number(storedId)
  return list.find((l) => Number(l.id) === sid) ||
    list.find((l) => Number(l.id) === Number(defaultId)) ||
    list.find((l) => l.enabled !== false) ||
    list[0]
}

export function listLibs () { return _libs }

export function listMediaLibs () { return _mediaLibs }

// 视频库是否远程（SMB/NFS）：远程库读盘慢，remux/audio_transcode 档建议先「预缓存到服务器」
export function isRemoteVideoLib (libId) {
  const id = Number(libId)
  if (!id) return false
  const l = (_libs || []).find((x) => Number(x.id) === id)
  return !!l && (l.source === 'smb' || l.source === 'nfs')
}

export function currentLib () { return _current }

export function currentMediaId () {
  return _current ? Number(_current.id) : null
}

// URL/API 参数：多媒体库时才带 media_library，单媒体库保持 URL 干净
export function mediaParam () {
  return _mediaLibs.length > 1 ? currentMediaId() : null
}

// 当前媒体库的视频库：指定类型时严格过滤，禁止跨类型回退。
export function currentMediaVideoLibs (kind) {
  const all = _current ? _current.video_libraries : []
  if (!kind) return all
  const same = all.filter((l) => (l.kind || 'movie') === kind)
  return same
}

// 工具页/上传目标：当前媒体库首选视频库 id（按类型优先，其次默认视频库，最后第一个）
export function preferredVideoLibId (kind) {
  const cands = currentMediaVideoLibs(kind)
  if (cands.length) {
    const def = cands.find((l) => Number(l.id) === Number(_defaultId))
    return Number((def || cands[0]).id)
  }
  return kind ? null : (_defaultId != null ? Number(_defaultId) : null)
}

// 兼容旧调用：Settings 工具页初始选中的视频库
export function currentLibId () { return preferredVideoLibId() }

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
  _mediaLibs = buildMediaLibs(_libs)
  // 回退优先级：旧 localStorage 视频库 → 其媒体库；默认视频库 → 其媒体库
  let fallback = null
  const storedVideo = getStoredLibId()
  if (storedVideo != null) {
    const v = _libs.find((l) => Number(l.id) === Number(storedVideo))
    if (v) fallback = v.media_library_id
  }
  if (fallback == null) {
    const def = _libs.find((l) => Number(l.id) === Number(_defaultId))
    if (def) fallback = def.media_library_id
  }
  _current = pickMedia(_mediaLibs, getStoredMediaId(), fallback)
  if (_current) setStoredMediaId(_current.id)
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

export function switchMedia (id) {
  const media = _mediaLibs.find((m) => Number(m.id) === Number(id))
  if (!media) return null
  _current = media
  setStoredMediaId(media.id)
  // 工具页跟随：记住该媒体库下首个视频库
  const first = media.video_libraries[0]
  if (first) setStoredLibId(first.id)
  _notify()
  return media
}

// 兼容旧调用：视频库级切换（工具页/旧分享链接）
export function switchLib (id) {
  const lib = _libs.find((l) => Number(l.id) === Number(id))
  if (!lib) return null
  setStoredLibId(lib.id)
  return switchMedia(lib.media_library_id)
}

export function resetLibState () {
  _libs = []
  _mediaLibs = []
  _defaultId = null
  _current = null
  _loading = null
  _listeners.clear()
}

export function uploadLibraries(libs, kind) {
  return (libs || []).filter(l => (l.kind || 'movie') === kind && !l.read_only
    && l.enabled !== false && l.enabled !== 0 && l.media_enabled !== false && l.media_enabled !== 0)
}
