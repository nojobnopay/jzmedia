import { normalizeWallSort } from './wallSort.js'

export const AI_FILTER_FIELDS = [
  { key: 'genre', label: '类型' }, { key: 'region', label: '产地大区' },
  { key: 'country', label: '国家／地区代码（如 CN、US）' },
  { key: 'year', label: '年份' }, { key: 'decade', label: '年代（如 1990）' },
  { key: 'tag', label: '标签' },
]
const list = value => Array.isArray(value) ? value.map(String) : String(value || '').split(/[,，、]/).map(v => v.trim()).filter(Boolean)
export function aiFilterDraft(filters = {}) {
  const out = { ...filters, q: filters.q || '', watched: filters.watched ?? '', min_rating: filters.min_rating ?? '',
    rating_source: filters.rating_source || 'tmdb', sort: filters.sort || 'added', order: filters.order || 'desc' }
  for (const { key } of AI_FILTER_FIELDS) out[key] = list(filters[key]).join(', ')
  out.status = list(filters.status)
  return out
}
export function aiFilterError(draft) {
  if (!draft) return ''
  if (draft.min_rating !== '' && draft.min_rating != null &&
      (!Number.isFinite(Number(draft.min_rating)) || Number(draft.min_rating) < 0 || Number(draft.min_rating) > 10)) return '最低评分请输入 0 到 10。'
  for (const [key, label] of [['year', '年份'], ['decade', '年代']]) {
    if (list(draft[key]).some(v => !/^\d{4}$/.test(v) || Number(v) < 1800 || Number(v) > 2200)) return label + '请输入 1800 到 2200 的四位整数，多项用逗号分隔。'
  }
  if (list(draft.decade).some(v => Number(v) % 10)) return '年代请输入整十年份，例如 1990。'
  if (list(draft.country).some(v => v !== '未知' && !/^[a-z]{2}$/i.test(v))) return '国家／地区请填写两位代码，例如 CN、HK、US；资料缺失可填“未知”。'
  return ''
}
export function aiFiltersToWall(draft, kind = 'movie') {
  const watched = draft.watched === 0 || draft.watched === '0' ? 0 : draft.watched === 1 || draft.watched === '1' ? 1 : null
  const value = draft.min_rating === '' || draft.min_rating == null ? null : Number(draft.min_rating)
  const rating = value != null && Number.isFinite(value) && value >= 0 && value <= 10 ? value : null
  const sel = {
    genres: list(draft.genre), regions: list(draft.region), countries: list(draft.country).map(v => v.toUpperCase()),
    years: list(draft.year), decades: list(draft.decade), tags: list(draft.tag), watched, rating,
    ratingSource: (kind === 'tv' ? ['tmdb', 'custom'] : ['tmdb', 'douban', 'custom']).includes(draft.rating_source) ? draft.rating_source : 'tmdb',
  }
  // The existing walls give countries precedence. Do not leave inactive regions in the count or URL.
  if (sel.countries.length) sel.regions = []
  if (kind === 'tv') sel.status = list(draft.status).filter(v => ['continuing', 'ended', 'other'].includes(v))
  return { q: String(draft.q || '').trim(), sel, sort: normalizeWallSort({ key: draft.sort, order: draft.order }) }
}
