import test from 'node:test'
import assert from 'node:assert/strict'
import { leavesFileLibrary, needsFileReview, scanTarget } from '../src/fileReview.js'

test('review is required when leaving files, including another library or route', () => {
  const resolve = q => q.library || 1
  assert.equal(leavesFileLibrary({ path: '/settings', query: { sec: 'sec-files', library: 1 } }, 1, resolve), false)
  assert.equal(leavesFileLibrary({ path: '/settings', query: { sec: 'sec-files', library: 2 } }, 1, resolve), true)
  assert.equal(leavesFileLibrary({ path: '/settings', query: { sec: 'sec-sync', library: 1 } }, 1, resolve), true)
  assert.equal(leavesFileLibrary({ path: '/tv', query: {} }, 1, resolve), true)
})

test('partial writes and running copy tasks still require review', () => {
  assert.equal(needsFileReview({ pending: false, count: 0, active_jobs: [] }), false)
  assert.equal(needsFileReview({ pending: true, count: 1 }), true)
  assert.equal(needsFileReview({ active_jobs: [{ job_id: 'copy', state: 'running' }] }), true)
  assert.equal(needsFileReview(null, true), true)
  assert.deepEqual(scanTarget({ id: 2, media_id: 9 }), { path: '/settings', query: { sec: 'sec-sync', library: '2', media: '9' } })
})
