import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_WALL_SORT, SORT_STORAGE_KEY, WALL_SORTS, defaultOrder, loadWallSort,
  normalizeWallSort, parseWallSort, saveWallSort, toggleWallSort, wallSortParams,
} from '../src/wallSort.js'

function fakeStorage(init = {}) {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
  }
}

test('默认排序 = 最近添加↓', () => {
  assert.deepEqual(DEFAULT_WALL_SORT, { key: 'added', order: 'desc' })
  assert.equal(WALL_SORTS[0].key, 'added')
  assert.equal(defaultOrder('title'), 'asc')
})

test('normalizeWallSort：未知键/方向回落', () => {
  assert.deepEqual(normalizeWallSort(null), { key: 'added', order: 'desc' })
  assert.deepEqual(normalizeWallSort({ key: 'bogus', order: 'asc' }), { key: 'added', order: 'asc' })
  assert.deepEqual(normalizeWallSort({ key: 'title' }), { key: 'title', order: 'asc' })
  assert.deepEqual(normalizeWallSort({ key: 'title', order: 'DESC' }), { key: 'title', order: 'asc' })
})

test('parseWallSort / wallSortParams：URL 往返、默认不写', () => {
  assert.deepEqual(parseWallSort({ sort: 'year', order: 'asc' }), { key: 'year', order: 'asc' })
  assert.deepEqual(parseWallSort({}), { key: 'added', order: 'desc' })
  assert.deepEqual(wallSortParams({ key: 'added', order: 'desc' }), {})
  assert.deepEqual(wallSortParams({ key: 'rating', order: 'asc' }), { sort: 'rating', order: 'asc' })
})

test('loadWallSort / saveWallSort：localStorage 记忆与坏值兜底', () => {
  const st = fakeStorage()
  assert.deepEqual(loadWallSort(st), { key: 'added', order: 'desc' })
  saveWallSort(st, { key: 'year', order: 'asc' })
  assert.deepEqual(JSON.parse(st.getItem(SORT_STORAGE_KEY)), { key: 'year', order: 'asc' })
  assert.deepEqual(loadWallSort(st), { key: 'year', order: 'asc' })
  assert.deepEqual(loadWallSort(fakeStorage({ [SORT_STORAGE_KEY]: '{bad json' })),
    { key: 'added', order: 'desc' })
})

test('toggleWallSort：同键切方向、新键用默认方向', () => {
  assert.deepEqual(toggleWallSort({ key: 'added', order: 'desc' }, 'added'), { key: 'added', order: 'asc' })
  assert.deepEqual(toggleWallSort({ key: 'added', order: 'asc' }, 'year'), { key: 'year', order: 'desc' })
  assert.deepEqual(toggleWallSort({ key: 'added', order: 'asc' }, 'title'), { key: 'title', order: 'asc' })
})
