// 海报墙排序（2026-09）：默认按入库时间（movies.added_at）倒序；
// 排序状态在 URL（?sort=&order=）与 localStorage 之间同步。纯函数，node 单测。

export const WALL_SORTS = [
  { key: 'added', label: '最近添加', defaultOrder: 'desc' },
  { key: 'updated', label: '最近更新', defaultOrder: 'desc' },
  { key: 'rating', label: '评分', defaultOrder: 'desc' },
  { key: 'year', label: '年份', defaultOrder: 'desc' },
  { key: 'title', label: '标题', defaultOrder: 'asc' },
]

export const DEFAULT_WALL_SORT = { key: 'added', order: 'desc' }
export const SORT_STORAGE_KEY = 'jzmedia.wallSort'

const KEYS = new Set(WALL_SORTS.map(s => s.key))

export function defaultOrder(key) {
  const s = WALL_SORTS.find(x => x.key === key)
  return s ? s.defaultOrder : 'desc'
}

export function normalizeWallSort(v) {
  const key = (v && KEYS.has(v.key)) ? v.key : DEFAULT_WALL_SORT.key
  const order = (v && (v.order === 'asc' || v.order === 'desc')) ? v.order : defaultOrder(key)
  return { key, order }
}

// URL query（vue-router route.query）→ 排序状态
export function parseWallSort(query) {
  return normalizeWallSort({ key: query && query.sort, order: query && query.order })
}

// 排序状态 → URL query 片段（默认值不写 URL，保持链接干净）
export function wallSortParams(sort) {
  const s = normalizeWallSort(sort)
  if (s.key === DEFAULT_WALL_SORT.key && s.order === DEFAULT_WALL_SORT.order) return {}
  return { sort: s.key, order: s.order }
}

export function loadWallSort(storage) {
  try {
    return normalizeWallSort(JSON.parse(storage.getItem(SORT_STORAGE_KEY) || '{}'))
  } catch (e) {
    return { ...DEFAULT_WALL_SORT }
  }
}

export function saveWallSort(storage, sort) {
  try {
    storage.setItem(SORT_STORAGE_KEY, JSON.stringify(normalizeWallSort(sort)))
  } catch (e) { /* 忽略 */ }
}

// 点击排序 chip：已选键切方向，新键用该键的默认方向
export function toggleWallSort(cur, key) {
  const c = normalizeWallSort(cur)
  if (c.key === key) return { key, order: c.order === 'desc' ? 'asc' : 'desc' }
  return { key, order: defaultOrder(key) }
}
