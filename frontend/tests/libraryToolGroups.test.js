// 媒体库工具分组纯函数测试：按视频库分表、筛选、徽章、媒体根 subpath 映射。
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  videoLibIds, libIdsParam, libById, libLabel, groupByVideoLib, filterByLib,
  countByLib, mediaPendingCount, libBySubpath, subpathContains,
} from '../src/libraryToolGroups.js'

const LIBS = [
  { id: 11, name: '电影', kind: 'movie', subpath: '电影' },
  { id: 12, name: '纪录片', kind: 'movie', subpath: '纪录片' },
  { id: 13, name: '剧集', kind: 'tv', subpath: '剧集', enabled: false, effective_enabled: false },
]

test('videoLibIds 按类型/启用态过滤', () => {
  assert.deepEqual(videoLibIds(LIBS), [11, 12, 13])
  assert.deepEqual(videoLibIds(LIBS, { kind: 'tv' }), [13])
  assert.deepEqual(videoLibIds(LIBS, { kind: 'movie' }), [11, 12])
  assert.deepEqual(videoLibIds(LIBS, { enabledOnly: true }), [11, 12])
  assert.equal(libIdsParam(LIBS, { kind: 'movie' }), '11,12')
})

test('libById / libLabel', () => {
  assert.equal(libById(LIBS, '12').name, '纪录片')
  assert.equal(libById(LIBS, 99), null)
  assert.equal(libLabel(LIBS[0]), '电影 · 电影')
  assert.equal(libLabel(LIBS[2]), '剧集 · 剧集')
  assert.equal(libLabel(null), '未识别库')
})

test('groupByVideoLib 保序分组，未识别库归尾', () => {
  const items = [
    { id: 1, library_id: 12 },
    { id: 2, library_id: 11 },
    { id: 3, library_id: 11 },
    { id: 4, library_id: 999 },
  ]
  const groups = groupByVideoLib(items, LIBS)
  assert.deepEqual(groups.map((g) => g.library_id), [11, 12, null])
  assert.deepEqual(groups[0].items.map((i) => i.id), [2, 3])
  assert.deepEqual(groups[2].items.map((i) => i.id), [4])
  assert.deepEqual(groupByVideoLib(items, LIBS, { includeEmpty: true }).length, 4)
})

test('filterByLib / countByLib', () => {
  const items = [{ library_id: 11 }, { library_id: 11 }, { library_id: 12 }]
  assert.equal(filterByLib(items, null).length, 3)
  assert.equal(filterByLib(items, 11).length, 2)
  assert.deepEqual(countByLib(items), { 11: 2, 12: 1 })
})

test('mediaPendingCount 只统计该媒体库视频库', () => {
  const data = {
    unmatched: [{ library_id: 11 }, { library_id: 42 }],
    needs_review: [{ library_id: 12 }],
    suspect_title_high: [{ library_id: 11 }],
    orphan_extras: [{ library_id: 99 }],
  }
  assert.equal(mediaPendingCount(data, [11, 12]), 3)
  assert.equal(mediaPendingCount(null, [11]), 0)
})

test('媒体根 subpath 映射', () => {
  assert.equal(libBySubpath(LIBS, '电影').id, 11)
  assert.equal(libBySubpath(LIBS, '/电影/').id, 11)
  assert.equal(libBySubpath(LIBS, '电影/子目录'), null)
  assert.equal(libBySubpath(LIBS, ''), null)
  assert.equal(subpathContains(LIBS, '电影/2020'), false)
  assert.equal(subpathContains([{ subpath: '媒体/电影' }], '媒体'), true)
  assert.equal(subpathContains([{ subpath: '媒体/电影' }], '媒体/电影'), false)
})
