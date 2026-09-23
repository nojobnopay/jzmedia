// 剧集墙纯逻辑（与电影墙 wallSort.js/筛选习惯对齐）：筛选默认值、
// 已选计数、连载状态桶标签、列表 query 构建。node 单测。

// TMDB status 原值 → 前端桶（与后端 tv_status_bucket 对应）
export const STATUS_OPTIONS = [
  { value: 'continuing', label: '连载中' },
  { value: 'ended', label: '已完结' },
  { value: 'other', label: '其他' },
]

const STATUS_LABEL = new Map(STATUS_OPTIONS.map(o => [o.value, o.label]))

export function statusLabel(v) {
  return STATUS_LABEL.get(String(v || '')) || String(v || '')
}

// TMDB 原值 → 桶（卡片上的状态文案与筛选桶一致）
export function bucketOfStatus(s) {
  const v = String(s || '')
  if (v === 'Continuing' || v === 'Returning Series' || v === 'In Production') return 'continuing'
  if (v === 'Ended' || v === 'Canceled' || v === 'Cancelled') return 'ended'
  return 'other'
}

export function statusText(s) {
  const b = bucketOfStatus(s)
  if (b === 'continuing') return '连载中'
  if (b === 'ended') return '已完结'
  return s || ''
}

export function defaultTvSel() {
  return {
    genres: [], regions: [], countries: [], years: [], decades: [],
    tags: [], status: [], watched: null, rating: null, ratingSource: 'tmdb',
  }
}

export function normalizeTvSel(v) {
  const d = defaultTvSel()
  if (!v || typeof v !== 'object') return d
  const arr = (x) => Array.isArray(x) ? x.map(String) : []
  const src = ['tmdb', 'custom'].includes(v.ratingSource) ? v.ratingSource : 'tmdb'
  return {
    genres: arr(v.genres), regions: arr(v.regions), countries: arr(v.countries),
    years: arr(v.years), decades: arr(v.decades), tags: arr(v.tags),
    status: arr(v.status).filter(x => STATUS_LABEL.has(x)),
    watched: v.watched === 1 || v.watched === 0 ? v.watched : null,
    rating: v.rating == null || v.rating === '' ? null : Number(v.rating),
    ratingSource: src,
  }
}

export function countTvActive(sel) {
  const s = sel || {}
  const n = (a) => Array.isArray(a) ? a.length : 0
  return n(s.genres) + n(s.regions) + n(s.countries) + n(s.years) +
    n(s.decades) + n(s.tags) + n(s.status) +
    (s.watched == null ? 0 : 1) + (s.rating == null ? 0 : 1)
}

// 列表 query 构建（与电影墙 buildParams 同习惯）：
// 选了具体国家时大区自动让位（后端两者是 AND，避免华语+US 空交集）。
export function buildTvParams({ q = '', sel = {}, sort = {}, mediaId = null, limit = 60, offset = 0 } = {}) {
  const s = normalizeTvSel(sel)
  const p = new URLSearchParams()
  const qq = String(q || '').trim()
  if (qq) p.set('q', qq)
  //  actors 回填的人名走 q（后端 q 命中 person_names），无需额外参数
  const useRegion = s.countries.length ? [] : s.regions
  for (const [key, vals] of [['genre', s.genres], ['region', useRegion],
      ['country', s.countries], ['year', s.years],
      ['decade', s.decades], ['tag', s.tags], ['status', s.status]]) {
    for (const v of vals) p.append(key, v)
  }
  if (s.watched != null) p.set('watched', String(s.watched))
  if (s.rating != null) p.set('min_rating', String(s.rating))
  if (s.rating != null || (sort && sort.key === 'rating')) {
    p.set('rating_source', s.ratingSource)
  }
  if (sort && sort.key) {
    p.set('sort', sort.key)
    p.set('order', sort.order || 'desc')
  }
  if (mediaId != null) p.set('media_library', String(mediaId))
  p.set('limit', String(Math.max(1, Number(limit) || 60)))
  p.set('offset', String(Math.max(0, Number(offset) || 0)))
  return p.toString()
}
