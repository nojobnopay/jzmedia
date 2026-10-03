import test from 'node:test'
import assert from 'node:assert/strict'
import { TV_UPDATES_WEEK, TV_UPDATES_RETENTION, tvUpdateEventKey, loadTvUpdatesHistory,
  saveTvUpdatesHistory, normalizeTvUpdatesHistory, unseenTvUpdates, shouldExpandTvUpdates,
  markTvUpdatesSeen, restoreTvUpdatesBatch } from '../src/tvUpdates.js'

const now = Date.parse('2026-10-03T12:00:00Z')
const show = (id, events = [id]) => ({ tmdb_id: id, show_id: id + 100, title: '合成剧集' + id,
  events: events.map(n => ({ event_id: 'ep-' + n, season: 1, episode: n, air_date: '2026-10-01' })) })
const storage = () => { const values = new Map(); return { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) } }

test('weekly expansion requires unseen content and never consumes an empty interval', () => {
  const history = normalizeTvUpdatesHistory(null, now)
  assert.equal(shouldExpandTvUpdates([], history, 'weekly', now), false)
  assert.deepEqual(markTvUpdatesSeen(history, [], now), history)
  assert.equal(shouldExpandTvUpdates([show(1)], history, 'weekly', now), true)
  assert.equal(shouldExpandTvUpdates([show(1)], history, 'off', now), false)
  assert.equal(shouldExpandTvUpdates([show(1)], { lastShown: now - TV_UPDATES_WEEK + 1 }, 'weekly', now), false)
  assert.equal(shouldExpandTvUpdates([show(1)], { lastShown: now - TV_UPDATES_WEEK }, 'weekly', now), true)
})

test('only visible cards become seen; subsequent exposure in the batch does not extend the interval', () => {
  const items = Array.from({ length: 8 }, (_, n) => show(n + 1))
  const first = markTvUpdatesSeen({}, items.slice(0, 2), now)
  assert.equal(first.lastShown, now)
  assert.equal(Object.keys(first.seen).length, 2)
  assert.equal(unseenTvUpdates(items, first, now).length, 6)
  const later = markTvUpdatesSeen(first, items.slice(2, 6), now + 10000, false)
  assert.equal(later.lastShown, now)
  assert.deepEqual(unseenTvUpdates(items, later, now + 10000).map(item => item.tmdb_id), [7, 8])
  assert.equal(markTvUpdatesSeen(later, items.slice(0, 6), now + 20000).lastShown, now)
})

test('history is isolated by media library, bounded by 30 days and resilient to unavailable storage', () => {
  const disk = storage()
  const marked = markTvUpdatesSeen({}, [show(1)], now)
  saveTvUpdatesHistory(disk, 7, marked, now)
  assert.deepEqual(loadTvUpdatesHistory(disk, 7, now), marked)
  assert.deepEqual(loadTvUpdatesHistory(disk, 9, now), { seen: {}, lastShown: 0 })
  assert.deepEqual(loadTvUpdatesHistory(disk, 7, now + TV_UPDATES_RETENTION).seen, {})
  assert.deepEqual(normalizeTvUpdatesHistory({ seen: { a: 'bad', b: now + 1, c: now } }, now).seen, { c: now })
  assert.deepEqual(loadTvUpdatesHistory({ getItem() { throw Error('denied') } }, 7, now), { seen: {}, lastShown: 0 })
  assert.deepEqual(saveTvUpdatesHistory({ setItem() { throw Error('denied') } }, 7, marked, now), marked)
})

test('stable event identities suppress metadata edits and duplicate rows', () => {
  const item = show(1, [1, 2])
  const history = markTvUpdatesSeen({}, [item], now)
  const edited = { ...item, title: '改名', events: item.events.map(event => ({ ...event, air_date: '2026-10-02' })) }
  assert.deepEqual(unseenTvUpdates([edited], history, now), [])
  assert.equal(unseenTvUpdates([item, item], {}, now).length, 1)
  const withNew = show(1, [1, 2, 3])
  assert.deepEqual(unseenTvUpdates([withNew], history, now)[0].events.map(event => event.episode), [3])
  assert.notEqual(tvUpdateEventKey(show(1), show(1).events[0]), tvUpdateEventKey(show(2), show(1).events[0]))
})

test('stale tabs merge exposure history without overwriting a newer interval', () => {
  const disk = storage()
  const tabA = loadTvUpdatesHistory(disk, 7, now)
  const tabB = loadTvUpdatesHistory(disk, 7, now)
  const first = saveTvUpdatesHistory(disk, 7, markTvUpdatesSeen(tabA, [show(1)], now), now)
  const second = saveTvUpdatesHistory(disk, 7, markTvUpdatesSeen(tabB, [show(2)], now + 1000), now + 1000)
  assert.deepEqual(unseenTvUpdates([show(1), show(2)], second, now + 1000), [])
  // A delayed save from A must preserve B's later acknowledgement and interval.
  const merged = saveTvUpdatesHistory(disk, 7, first, now + 2000)
  assert.equal(merged.lastShown, now + 1000)
  assert.equal(merged.seen[tvUpdateEventKey(show(2), show(2).events[0])], now + 1000)
  assert.deepEqual(loadTvUpdatesHistory(disk, 7, now + 2000), merged)
  assert.deepEqual(loadTvUpdatesHistory(disk, 9, now + 2000), { seen: {}, lastShown: 0 })
})

test('history merging prunes expired and invalid records from both tabs', () => {
  const disk = storage()
  disk.setItem('jzmedia.tvUpdates.v1.7', JSON.stringify({ lastShown: now,
    seen: { current: now, expired: now - TV_UPDATES_RETENTION, future: now + 1000, invalid: 'bad' } }))
  const result = saveTvUpdatesHistory(disk, 7, { lastShown: now - 100,
    seen: { current: now - 100, fresh: now, stale: now - TV_UPDATES_RETENTION, later: now + 1 } }, now)
  assert.deepEqual(result, { seen: { current: now, fresh: now }, lastShown: now })
})

test('returning from detail preserves a batch and removes collected episodes without adding late new events', () => {
  const snapshot = { mediaId: 7, items: [show(1, [1, 2]), show(2)], expanded: true }
  const restored = restoreTvUpdatesBatch(snapshot, [show(1, [2, 3]), show(3)], 7)
  assert.deepEqual(restored.map(item => item.tmdb_id), [1])
  assert.deepEqual(restored[0].events.map(event => event.episode), [2])
  assert.equal(restoreTvUpdatesBatch(snapshot, [show(1)], 9), null)
  assert.deepEqual(restoreTvUpdatesBatch(snapshot, [], 7), [])
})
