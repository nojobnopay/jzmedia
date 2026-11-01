import test from 'node:test'
import assert from 'node:assert/strict'
import { createScanCleanupFollowups, cleanupResolved, scanCleanupChanges, SCAN_CLEANUP_STORAGE_KEY } from '../src/scanCleanupFollowups.js'

function memoryStorage() {
  const values = new Map()
  return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value) }
}
const job = (jobId = 'scan-1', libraryId = 2, changes = { removed_episode: 3, replaced_episode: 2 }) => ({
  job_id: jobId, library_id: libraryId, state: 'done', summary: { by_library: { [libraryId]: changes } },
})
const resolved = { dry_run: false, candidates: 2, removed: 2, failed: 0, skipped: {} }

test('successful per-library replacement/deletion counters suggest checking, not raw updates or another library totals', () => {
  const value = job()
  value.summary.counts = { removed_movie: 999 }
  value.summary.by_library[3] = { changed_episode: 10 }
  value.summary.tv_ingest = { metadata_results: [{ files: { replaced: 500 } }] }
  assert.deepEqual(scanCleanupChanges(value), { 2: { removed: 3, changed: 2 } })
  assert.deepEqual(scanCleanupChanges(job('movie', 1, { removed_movie: 4, ok: 200 })), { 1: { removed: 4, changed: 0 } })
  assert.deepEqual(scanCleanupChanges(job('normal', 2, { tv_ok: 200 })), {})
  assert.deepEqual(scanCleanupChanges({ job_id: 'ambiguous', state: 'done', summary: { counts: { removed_episode: 3 } } }), {})
})

test('failed, cancelled, offline, and incomplete library scans never create new cleanup prompts', () => {
  for (const state of ['running', 'failed', 'cancelled']) assert.deepEqual(scanCleanupChanges({ ...job(), state }), {})
  for (const failure of [{ library_offline: 1 }, { scan_failed: 1 }, { 'error: source disappeared': 1 }]) {
    assert.deepEqual(scanCleanupChanges(job('incomplete', 2, { removed_episode: 3, ...failure })), {})
  }
  const value = { job_id: 'multi', state: 'done', summary: { by_library: {
    2: { removed_episode: 2, 'error: interrupted': 1 }, 3: { removed_movie: 1 },
  } } }
  assert.deepEqual(scanCleanupChanges(value), { 3: { removed: 1, changed: 0 } })
})

test('legacy explicit single-library summaries remain usable without attributing media-wide counters', () => {
  const value = { job_id: 'legacy', library_id: 2, state: 'done', summary: { counts: { changed_episode: 1 } } }
  assert.deepEqual(scanCleanupChanges(value), { 2: { removed: 0, changed: 1 } })
  value.summary.errors = [{ status: 'error' }]
  assert.deepEqual(scanCleanupChanges(value), {})
  delete value.summary.errors
  value.media_library_id = 1
  assert.deepEqual(scanCleanupChanges(value), {})
})

test('reminders survive refresh and another library scan; no-change and unsuccessful scans do not erase pending work', () => {
  const storage = memoryStorage(), create = () => createScanCleanupFollowups({ storage: () => storage, now: () => 100 })
  const first = create()
  first.record(job())
  first.record(job('other', 1, { removed_movie: 1 }))
  first.record(job('normal', 2, { tv_ok: 20 }))
  first.record({ ...job('cancelled'), state: 'cancelled' })
  const next = create()
  assert.equal(next.state.value.entries[2].jobId, 'scan-1')
  assert.equal(next.state.value.entries[1].removed, 1)
  next.record(job())
  assert.equal(next.state.value.seen.filter(id => id === 'scan-1').length, 1)
})

test('protected caches, partial cleanup and uncertain responses retain reminders', () => {
  for (const result of [null, { ...resolved, failed: 1 }, { ...resolved, removed: 1 },
    { ...resolved, skipped: { protected: 1 } }, { ...resolved, skipped_details: { replacement_missing: 1 } },
    { removed: 1, failed: 0 }, { ...resolved, dry_run: true }]) assert.equal(cleanupResolved(result), false)
  assert.equal(cleanupResolved({ candidates: 0, dry_run: true, skipped: {} }), true)
  assert.equal(cleanupResolved(resolved), true)
})

test('cleanup resolves only reviewed scan versions, and refreshing the old latest job cannot recreate them', () => {
  const storage = memoryStorage(), store = createScanCleanupFollowups({ storage: () => storage })
  store.record(job())
  store.record(job('movie', 1, { removed_movie: 1 }))
  const snapshot = store.snapshot()
  store.record(job('new-source', 2, { changed_episode: 1 }))
  assert.equal(store.settle(resolved, snapshot), true)
  assert.equal(store.state.value.entries[1], undefined)
  assert.equal(store.state.value.entries[2].jobId, 'new-source')
  store.settle(resolved, store.snapshot())
  store.record(job('new-source', 2, { changed_episode: 1 }))
  assert.deepEqual(store.state.value.entries, {})
})

test('invalid stored data is ignored, and unavailable storage retains current-session reminders', () => {
  const storage = memoryStorage()
  storage.setItem(SCAN_CLEANUP_STORAGE_KEY, '{invalid')
  const store = createScanCleanupFollowups({ storage: () => storage })
  assert.deepEqual(store.state.value.entries, {})
  const blocked = createScanCleanupFollowups({ storage: () => ({ getItem: () => null, setItem() { throw Error('quota') } }) })
  blocked.record(job())
  blocked.record(job('movie', 1, { removed_movie: 1 }))
  blocked.reload()
  assert.equal(Object.keys(blocked.state.value.entries).length, 2)
})
