// Shared, side-effect-free filter state for the movie/TV walls and dialog drafts.
// Keep the existing query semantics: year and decade remain separate conditions.
const ARRAY_FIELDS = ['genres', 'regions', 'countries', 'years', 'decades', 'tags']
const STATUS_LABELS = { continuing: '连载中', ended: '已完结', other: '其他' }
const RATING_LABELS = { tmdb: 'TMDB', douban: '豆瓣', custom: '自评' }
const GROUP_FIELDS = {
  genres: ['genres'], location: ['regions', 'countries'], period: ['decades', 'years'],
  watched: ['watched'], rating: ['rating'], tags: ['tags'], status: ['status'],
}

function strings(value, transform = v => v) {
  if (!Array.isArray(value)) return []
  return [...new Set(value
    .filter(v => typeof v === 'string' || typeof v === 'number' && Number.isFinite(v))
    .map(v => transform(String(v).trim())).filter(Boolean))]
}

function ratingValue(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && !value.trim()) return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 && number <= 10 ? number : null
}

function watchedValue(value) {
  return value === 0 || value === '0' ? 0 : value === 1 || value === '1' ? 1 : null
}

export function defaultBrowseFilters(kind = 'movie') {
  const value = Object.fromEntries(ARRAY_FIELDS.map(key => [key, []]))
  if (kind === 'tv') value.status = []
  return { ...value, watched: null, rating: null, ratingSource: 'tmdb' }
}

export function normalizeBrowseFilters(value, kind = 'movie') {
  const input = value && typeof value === 'object' ? value : {}
  const result = defaultBrowseFilters(kind)
  for (const key of ARRAY_FIELDS) result[key] = strings(input[key])
  result.countries = strings(input.countries, code => code.toUpperCase())
  // Clear inactive regions instead of retaining hidden filters that could revive.
  if (result.countries.length) result.regions = []
  if (kind === 'tv') result.status = strings(input.status, status => status.toLowerCase())
    .filter(status => Object.hasOwn(STATUS_LABELS, status))
  result.watched = watchedValue(input.watched)
  result.rating = ratingValue(input.rating)
  const sources = kind === 'tv' ? ['tmdb', 'custom'] : ['tmdb', 'douban', 'custom']
  if (sources.includes(input.ratingSource)) result.ratingSource = input.ratingSource
  return result
}

function countryLabel(code, facets) {
  const countries = Array.isArray(facets?.countries) ? facets.countries : []
  const match = countries.find(country => country &&
    String(country.code || '未知').trim().toUpperCase() === code)
  return typeof match?.name === 'string' && match.name.trim() ? match.name.trim() : code
}

export function filterChips(value, facets = {}, kind = 'movie') {
  const selected = normalizeBrowseFilters(value, kind)
  const chips = []
  const add = (key, label = v => v) => {
    for (const value of selected[key] || []) chips.push({ key, value, label: label(value) })
  }
  add('genres')
  add('regions')
  add('countries', code => countryLabel(code, facets))
  add('decades', decade => decade + '年代')
  add('years', year => year + '年')
  if (selected.watched != null) chips.push({ key: 'watched', value: selected.watched,
    label: kind === 'tv' ? (selected.watched ? '已看完' : '未看完') : (selected.watched ? '已看' : '未看') })
  if (selected.rating != null) chips.push({ key: 'rating', value: selected.rating,
    label: `${RATING_LABELS[selected.ratingSource]} ${selected.rating}分以上` })
  add('tags')
  if (kind === 'tv') add('status', status => STATUS_LABELS[status])
  return chips
}

export function countBrowseFilters(value, kind = 'movie') {
  return filterChips(value, {}, kind).length
}

export function removeBrowseFilter(value, chip, kind = 'movie') {
  const result = normalizeBrowseFilters(value, kind)
  if (!chip || typeof chip !== 'object') return result
  const key = chip.key
  if (ARRAY_FIELDS.includes(key) || key === 'status' && kind === 'tv') {
    const transform = key === 'countries' ? v => v.toUpperCase()
      : key === 'status' ? v => v.toLowerCase() : v => v
    const target = strings([chip.value], transform)[0]
    result[key] = result[key].filter(item => item !== target)
  } else if (key === 'rating' && result.rating === ratingValue(chip.value)) {
    // The source also controls rating sort; removing its threshold retains it.
    result.rating = null
  } else if (key === 'watched' && result.watched === watchedValue(chip.value)) {
    result.watched = null
  }
  return result
}

export function resetFilterGroup(value, group, kind = 'movie') {
  const result = normalizeBrowseFilters(value, kind)
  for (const key of GROUP_FIELDS[group] || []) {
    if (key === 'status' && kind !== 'tv') continue
    result[key] = key === 'rating' || key === 'watched' ? null : []
  }
  if (group === 'rating') result.ratingSource = 'tmdb'
  return result
}

export function filterGroupSummary(value, group, facets = {}, kind = 'movie') {
  const fields = GROUP_FIELDS[group] || []
  const labels = filterChips(value, facets, kind).filter(chip => fields.includes(chip.key)).map(chip => chip.label)
  if (!labels.length) return '不限'
  return labels.slice(0, 2).join(' · ') + (labels.length > 2 ? ` +${labels.length - 2}` : '')
}
