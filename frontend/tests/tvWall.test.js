// tvWall 纯逻辑单测（剧集墙筛选/状态桶/query 构建，与电影墙习惯对齐）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { STATUS_OPTIONS, bucketOfStatus, buildTvParams, countTvActive,
  defaultTvSel, normalizeTvSel, statusLabel, statusText } from '../src/tvWall.js'

test('状态桶映射与标签', () => {
  assert.equal(bucketOfStatus('Returning Series'), 'continuing')
  assert.equal(bucketOfStatus('In Production'), 'continuing')
  assert.equal(bucketOfStatus('Ended'), 'ended')
  assert.equal(bucketOfStatus('Canceled'), 'ended')
  assert.equal(bucketOfStatus(''), 'other')
  assert.equal(bucketOfStatus('Unknown'), 'other')
  assert.equal(statusText('Returning Series'), '连载中')
  assert.equal(statusText('Ended'), '已完结')
  assert.equal(statusLabel('continuing'), '连载中')
  assert.equal(statusLabel('ended'), '已完结')
  assert.equal(statusLabel('other'), '其他')
  assert.equal(STATUS_OPTIONS.length, 3)
})

test('已选计数', () => {
  const s = defaultTvSel()
  assert.equal(countTvActive(s), 0)
  s.genres.push('科幻')
  s.status.push('ended')
  s.watched = 1
  s.rating = 8
  assert.equal(countTvActive(s), 4)
})

test('normalizeTvSel 过滤非法状态与评分来源', () => {
  const s = normalizeTvSel({ status: ['ended', 'nope'], ratingSource: 'douban',
    watched: 2, rating: '8' })
  assert.deepEqual(s.status, ['ended'])
  assert.equal(s.ratingSource, 'tmdb')
  assert.equal(s.watched, null)
  assert.equal(s.rating, 8)
})

test('buildTvParams：国家选中时大区让位', () => {
  const qs = buildTvParams({ q: '武林', sel: { ...defaultTvSel(), regions: ['华语'], countries: ['CN'] },
    sort: { key: 'added', order: 'desc' }, mediaId: 3, limit: 60, offset: 0 })
  const p = new URLSearchParams(qs)
  assert.equal(p.get('q'), '武林')
  assert.equal(p.get('country'), 'CN')
  assert.equal(p.get('region'), null)
  assert.equal(p.get('media_library'), '3')
  assert.equal(p.get('sort'), 'added')
})

test('buildTvParams：筛选与排序参数齐全', () => {
  const qs = buildTvParams({ q: '', sel: { ...defaultTvSel(), genres: ['科幻', '喜剧'],
    tags: ['太空'], status: ['continuing'], watched: 0, rating: 8, ratingSource: 'custom' },
    sort: { key: 'rating', order: 'desc' }, mediaId: null, limit: 60, offset: 120 })
  const p = new URLSearchParams(qs)
  assert.deepEqual(p.getAll('genre'), ['科幻', '喜剧'])
  assert.deepEqual(p.getAll('tag'), ['太空'])
  assert.equal(p.get('status'), 'continuing')
  assert.equal(p.get('watched'), '0')
  assert.equal(p.get('min_rating'), '8')
  assert.equal(p.get('rating_source'), 'custom')
  assert.equal(p.get('sort'), 'rating')
  assert.equal(p.get('offset'), '120')
  assert.equal(p.get('media_library'), null)
})
