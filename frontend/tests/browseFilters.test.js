import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultBrowseFilters, normalizeBrowseFilters, filterChips, countBrowseFilters,
  removeBrowseFilter, resetFilterGroup, filterGroupSummary } from '../src/browseFilters.js'

test('dialog drafts and defaults do not share arrays with their source or another draft', () => {
  const source = { ...defaultBrowseFilters('tv'), genres: ['剧情'], years: ['2020'], status: ['ended'] }
  for (const value of Object.values(source)) if (Array.isArray(value)) Object.freeze(value)
  Object.freeze(source)
  const first = normalizeBrowseFilters(source, 'tv'), second = normalizeBrowseFilters(source, 'tv')
  for (const [key, value] of Object.entries(first)) {
    if (!Array.isArray(value)) continue
    assert.notEqual(value, source[key])
    value.push('draft only')
    assert.equal(second[key].includes('draft only'), false)
  }
  const empty = defaultBrowseFilters()
  empty.tags.push('new')
  assert.deepEqual(defaultBrowseFilters().tags, [])
  assert.deepEqual(source.genres, ['剧情'])
})

test('country precedence counts only effective conditions and never revives a hidden region', () => {
  for (const kind of ['movie', 'tv']) {
    const source = { regions: ['华语'], countries: [' hk ', 'HK', ''] }
    const selected = normalizeBrowseFilters(source, kind)
    assert.deepEqual(selected.countries, ['HK'])
    assert.deepEqual(selected.regions, [])
    assert.equal(countBrowseFilters(source, kind), 1)
    const chip = filterChips(source, { countries: [{ code: 'HK', name: '中国香港' }] }, kind)[0]
    assert.deepEqual(chip, { key: 'countries', value: 'HK', label: '中国香港' })
    const removed = removeBrowseFilter(selected, chip, kind)
    assert.deepEqual(removed.countries, [])
    assert.deepEqual(removed.regions, [])
    assert.equal(countBrowseFilters(removed, kind), 0)
    assert.deepEqual(source.regions, ['华语'])
  }
})

test('normalization deduplicates nonempty values without erasing selections missing from current facets', () => {
  const value = normalizeBrowseFilters({ genres: [' 剧情 ', '剧情', '', null, {}, '已移出资料的类型'],
    tags: ['家庭', '家庭', '重看'], years: [2020, '2020', '1994'], decades: ['1990'], regions: [] })
  assert.deepEqual(value.genres, ['剧情', '已移出资料的类型'])
  assert.deepEqual(value.tags, ['家庭', '重看'])
  assert.deepEqual(value.years, ['2020', '1994'])
  assert.deepEqual(value.decades, ['1990'])
  assert.deepEqual(filterChips(value, {}).filter(chip => chip.key === 'genres').map(chip => chip.value), value.genres)
  assert.deepEqual(normalizeBrowseFilters(null), defaultBrowseFilters())
  assert.deepEqual(normalizeBrowseFilters({ genres: '剧情', tags: false }).genres, [])
})

test('rating chips preserve zero and nonpreset thresholds and include the selected source exactly once', () => {
  for (const [rating, source, label] of [[0, 'custom', '自评 0分以上'], ['7.5', 'douban', '豆瓣 7.5分以上'], [10, 'tmdb', 'TMDB 10分以上']]) {
    const selected = normalizeBrowseFilters({ rating, ratingSource: source })
    assert.equal(selected.rating, Number(rating))
    assert.deepEqual(filterChips(selected), [{ key: 'rating', value: Number(rating), label }])
    assert.equal(countBrowseFilters(selected), 1)
  }
  assert.equal(countBrowseFilters({ ratingSource: 'custom' }), 0)
  for (const rating of ['', ' ', -1, 11, NaN, Infinity, 'not a score', true, [], null]) {
    assert.equal(normalizeBrowseFilters({ rating }).rating, null)
  }
})

test('partial or missing country facets keep usable labels and removable unknown selections', () => {
  const selected = { countries: ['CN', 'US', '未知'] }
  const facets = { countries: [{ code: 'CN', name: '中国' }, null, { code: '', name: '资料未知' }] }
  assert.deepEqual(filterChips(selected, facets).map(chip => chip.label), ['中国', 'US', '资料未知'])
  for (const incomplete of [{}, null, { countries: null }, { countries: [{ code: 'CN', name: '' }] }]) {
    assert.deepEqual(filterChips(selected, incomplete).map(chip => chip.label), ['CN', 'US', '未知'])
  }
  const result = removeBrowseFilter(selected, { key: 'countries', value: 'US' })
  assert.deepEqual(result.countries, ['CN', '未知'])
})

test('each chip removes only its own condition and does not mutate applied state', () => {
  const selected = normalizeBrowseFilters({ genres: ['剧情', '喜剧'], regions: ['华语'], years: ['1994'],
    decades: ['1990'], tags: ['重看'], watched: 0, rating: 7.5, ratingSource: 'custom', status: ['ended'] }, 'tv')
  const before = structuredClone(selected), chips = filterChips(selected, {}, 'tv')
  for (const chip of chips) {
    const next = removeBrowseFilter(selected, chip, 'tv')
    assert.equal(countBrowseFilters(next, 'tv'), chips.length - 1)
    assert.equal(filterChips(next, {}, 'tv').some(other => other.key === chip.key && other.value === chip.value), false)
    assert.equal(next.ratingSource, 'custom')
    assert.deepEqual(selected, before)
  }
  assert.deepEqual(removeBrowseFilter(selected, { key: 'rating', value: 8 }, 'tv'), selected)
  assert.deepEqual(removeBrowseFilter(selected, { key: 'unknown', value: 'x' }, 'tv'), selected)
  assert.deepEqual(removeBrowseFilter(selected, null, 'tv'), selected)
})

test('group reset clears all fields of that group while keeping independent conditions', () => {
  const input = { genres: ['剧情'], countries: ['HK'], decades: ['1990'], years: ['1994'],
    watched: 1, rating: 8.2, ratingSource: 'custom', tags: ['重看'], status: ['continuing', 'ended'] }
  const source = normalizeBrowseFilters(input, 'tv'), before = structuredClone(source)
  const fields = { genres: ['genres'], location: ['regions', 'countries'], period: ['decades', 'years'],
    watched: ['watched'], rating: ['rating', 'ratingSource'], tags: ['tags'], status: ['status'] }
  const defaults = defaultBrowseFilters('tv')
  for (const [group, resetFields] of Object.entries(fields)) {
    const result = resetFilterGroup(source, group, 'tv')
    for (const key of Object.keys(source)) assert.deepEqual(result[key], resetFields.includes(key) ? defaults[key] : source[key])
    assert.deepEqual(source, before)
  }
  assert.deepEqual(resetFilterGroup(source, 'unknown', 'tv'), source)
  assert.deepEqual(resetFilterGroup({ regions: ['欧美'] }, 'location').regions, [])
})

test('TV accepts only supported status and rating sources and uses completion labels', () => {
  const tv = normalizeBrowseFilters({ status: ['ended', ' continuing ', 'ENDED', 'nope', 'other'], ratingSource: 'douban', watched: '0' }, 'tv')
  assert.deepEqual(tv.status, ['ended', 'continuing', 'other'])
  assert.equal(tv.ratingSource, 'tmdb')
  assert.deepEqual(filterChips(tv, {}, 'tv').map(chip => chip.label), ['未看完', '已完结', '连载中', '其他'])
  assert.equal(filterChips({ watched: 1 }, {}, 'tv')[0].label, '已看完')
  assert.equal(filterChips({ watched: 0 })[0].label, '未看')
  assert.equal(filterChips({ watched: 1 })[0].label, '已看')
  assert.equal(countBrowseFilters({ watched: false }), 0)
  assert.equal(countBrowseFilters({ watched: 2 }), 0)
  const movie = normalizeBrowseFilters(tv)
  assert.equal(Object.hasOwn(movie, 'status'), false)
  assert.equal(countBrowseFilters({ status: ['ended'] }), 0)
})

test('group summaries use effective conditions, show a short overflow count, and retain nonpreset ratings', () => {
  assert.equal(filterGroupSummary({}, 'genres'), '不限')
  assert.equal(filterGroupSummary({ genres: ['剧情', '喜剧', '科幻', '冒险'] }, 'genres'), '剧情 · 喜剧 +2')
  assert.equal(filterGroupSummary({ countries: ['HK'], regions: ['欧美'] }, 'location', { countries: [{ code: 'HK', name: '中国香港' }] }), '中国香港')
  assert.equal(filterGroupSummary({ decades: ['1990'], years: ['1994'] }, 'period'), '1990年代 · 1994年')
  assert.equal(filterGroupSummary({ rating: 7.5, ratingSource: 'custom' }, 'rating'), '自评 7.5分以上')
  assert.equal(filterGroupSummary({ status: ['ended', 'continuing'] }, 'status', {}, 'tv'), '已完结 · 连载中')
})
