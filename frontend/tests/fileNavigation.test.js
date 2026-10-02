import test from 'node:test'
import assert from 'node:assert/strict'
import { createFileOrigin, normalizeFileOrigin, readFileOrigin, writeFileOrigin,
  fileWorkspaceTarget, fileReturnTarget, fileOriginMatches, fileOriginTransition, FILE_ORIGIN_KEY } from '../src/fileNavigation.js'

const snapshot = () => createFileOrigin({ id: 7, media_id: 2, kind: 'movie' },
  { view: 'restore', step: 'pending', scrollTop: 745, ids: [11, 12] }, 'visit-1')
const storage = () => {
  const values = new Map()
  return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
}

test('file shortcut captures a scoped same-tab destination including view, step and restore selection', () => {
  const origin = snapshot()
  assert.deepEqual(origin, { version: 1, token: 'visit-1', library: 7, media: 2, kind: 'movie',
    view: 'restore', step: 'pending', scrollTop: 745, ids: [11, 12] })
  assert.deepEqual(fileWorkspaceTarget(origin), { path: '/settings', query: {
    sec: 'sec-files', library: '7', media: '2', files_from: 'visit-1',
  } })
  assert.deepEqual(fileReturnTarget(origin), { path: '/settings', query: {
    sec: 'sec-libtools', library: '7', media: '2', files_return: 'visit-1',
  } })
})
test('origin survives refresh and file-library switches but is absent for direct sidebar entries', () => {
  const session = storage(), origin = snapshot()
  writeFileOrigin(origin, session)
  const restored = readFileOrigin(session)
  assert.deepEqual(restored, origin)
  const changedLibrary = { path: '/settings', query: { sec: 'sec-files', library: '9', media: '3', files_from: origin.token } }
  assert.equal(fileOriginMatches(restored, changedLibrary), true)
  assert.deepEqual(fileOriginTransition(restored, changedLibrary, fileWorkspaceTarget(origin)), { origin, restore: null })
  const direct = { path: '/settings', query: { sec: 'sec-files', library: '7' } }
  assert.equal(fileOriginMatches(restored, direct), false)
  assert.deepEqual(fileOriginTransition(restored, direct, changedLibrary), { origin: null, restore: null })
})
test('cancelled navigation preserves origin; approved return consumes it; scan redirects never restore', () => {
  const origin = snapshot(), from = fileWorkspaceTarget(origin), returning = fileReturnTarget(origin)
  assert.deepEqual(fileOriginTransition(origin, returning, from, true), { origin, restore: null })
  assert.deepEqual(fileOriginTransition(origin, returning, from), { origin: null, restore: origin })
  const scan = { path: '/settings', query: { sec: 'sec-sync', library: '9', media: '3' } }
  assert.deepEqual(fileOriginTransition(origin, scan, from), { origin: null, restore: null })
  assert.deepEqual(fileOriginTransition(origin, { path: '/m/11', query: {} }, from), { origin: null, restore: null })
  assert.deepEqual(fileOriginTransition(origin, { path: '/settings', query: { ...returning.query, library: '999' } }, from), { origin: null, restore: null })
})
test('stored snapshots cannot supply arbitrary return routes or invalid view state', () => {
  assert.equal(normalizeFileOrigin({ ...snapshot(), token: '../wrong' }), null)
  assert.equal(normalizeFileOrigin({ ...snapshot(), library: -1 }), null)
  const clean = normalizeFileOrigin({ ...snapshot(), kind: 'tv', view: 'restore', step: 'pending', scrollTop: -50,
    ids: [3, '3', -1, 'bad', 0], path: 'https://external.test' })
  assert.equal(clean.view, 'workflow')
  assert.equal(clean.step, 'scan')
  assert.equal(clean.scrollTop, 0)
  assert.deepEqual(clean.ids, [3])
  assert.equal('path' in clean, false)
  const session = storage()
  session.setItem(FILE_ORIGIN_KEY, '{bad')
  assert.equal(readFileOrigin(session), null)
  writeFileOrigin(snapshot(), session)
  writeFileOrigin(null, session)
  assert.equal(session.getItem(FILE_ORIGIN_KEY), null)
})
