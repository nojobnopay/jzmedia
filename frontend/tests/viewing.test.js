import test from 'node:test'
import assert from 'node:assert/strict'
import { viewing, applyViewingSession, refreshViewing, changeViewingMode, ensureManagement,
  requestProtection, submitProtection, cancelProtection, registerViewingPlaybackCleanup, setViewingChangeHandler,
  idleExpired, lockViewingLocally, lockViewing } from '../src/viewing.js'
import { saveBrowse, readBrowse, clearBrowse } from '../src/browseHistory.js'

const session = (extra = {}) => ({ mode: 'normal', enabled: true, management_unlocked: false, generation: 1, expires_at: 0, ...extra })
function reset(extra = {}) {
  cancelProtection()
  Object.assign(viewing, { ready: false, revision: 0, enabled: false, prompt: null, promptBusy: false, promptError: '', locallyLocked: false, suspended: false, ...extra })
  setViewingChangeHandler(null)
  clearBrowse()
}
test('mode changes invalidate full-row browse caches, management unlock keeps the current page', () => {
  reset()
  applyViewingSession(session())
  saveBrowse('old', { path: '/', mediaId: 1, query: {}, items: [{ id: 2, title: 'private' }] })
  applyViewingSession(session({ management_unlocked: true }))
  assert.equal(viewing.revision, 0)
  assert.ok(readBrowse('old'))
  applyViewingSession(session({ mode: 'private', management_unlocked: true, generation: 3 }))
  assert.equal(viewing.revision, 1)
  assert.equal(readBrowse('old'), null)
  applyViewingSession(session({ mode: 'private', generation: 3 }))
  assert.equal(viewing.revision, 1, 'Management expiry must not interrupt private playback; management panels react to the grant separately')
  assert.equal(viewing.management_unlocked, false)
})
test('new catalogue generations invalidate browsing even while management is unlocked', () => {
  reset()
  applyViewingSession(session({ management_unlocked: true }))
  saveBrowse('ordinary', { path: '/', mediaId: 1, query: {}, items: [{ id: 3 }] })
  applyViewingSession(session({ management_unlocked: true, generation: 2 }))
  assert.equal(viewing.revision, 1)
  assert.equal(readBrowse('ordinary'), null)
})
test('local idle expiry clears private data and stale server responses cannot restore it', () => {
  reset()
  applyViewingSession(session({ mode: 'private', management_unlocked: true }))
  saveBrowse('private', { path: '/', mediaId: 1, query: {}, items: [{ id: 2, title: 'private' }] })
  assert.equal(idleExpired(900000, 0), true)
  assert.equal(idleExpired(900000, 0, 899000), false, 'Effective playback extends private viewing only')
  lockViewingLocally()
  assert.equal(viewing.mode, 'normal')
  assert.equal(viewing.management_unlocked, false)
  assert.equal(readBrowse('private'), null)
  applyViewingSession(session({ mode: 'private', management_unlocked: true }))
  assert.equal(viewing.mode, 'normal', 'A successful stale refresh is still not new password authorization')
  applyViewingSession(session({ mode: 'private', management_unlocked: true }), { authorized: true })
  assert.equal(viewing.mode, 'private')
})
test('malformed sessions cannot silently reset a protected mode', () => {
  reset()
  applyViewingSession(session({ mode: 'children' }))
  assert.throws(() => applyViewingSession({}), /响应无效/)
  assert.equal(viewing.mode, 'children')
})
test('local locks notify other tabs and received notifications do not rebroadcast', t => {
  reset()
  applyViewingSession(session({ mode: 'private' }))
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const messages = []
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { setItem: (key, value) => messages.push([key, value]) } })
  t.after(() => descriptor ? Object.defineProperty(globalThis, 'localStorage', descriptor) : delete globalThis.localStorage)
  lockViewingLocally()
  assert.equal(messages.length, 1)
  assert.equal(messages[0][0], 'jzmedia.viewing.changed')
  assert.match(messages[0][1], /^lock:/)
  lockViewingLocally({ notifyTabs: false })
  assert.equal(messages.length, 1, 'Receiving a peer lock cannot create a broadcast loop')
})
test('an explicit private exit clears sensitive content even if the server is offline', async t => {
  reset()
  applyViewingSession(session({ mode: 'private', management_unlocked: true }))
  saveBrowse('private', { path: '/', mediaId: 1, query: {}, items: [{ id: 2 }] })
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch') })
  await assert.rejects(lockViewing(), /Failed to fetch/)
  assert.equal(viewing.mode, 'normal')
  assert.equal(viewing.management_unlocked, false)
  assert.equal(viewing.locallyLocked, true, 'Reconnect must revoke the old server session before restoring browsing')
  assert.equal(readBrowse('private'), null)
})
test('mode-picker exits fail closed offline, and children are shown only after server confirmation', async t => {
  for (const target of ['normal', 'children']) {
    reset()
    applyViewingSession(session({ mode: 'private', management_unlocked: true }))
    t.mock.method(globalThis, 'fetch', async path => {
      assert.equal(path, target === 'normal' ? '/api/viewing/lock' : '/api/viewing/mode')
      assert.equal(viewing.mode, 'normal', 'Private content is already hidden while waiting for the server')
      throw new TypeError('Offline')
    })
    await assert.rejects(changeViewingMode(target), /Offline/)
    assert.equal(viewing.mode, 'normal')
    assert.equal(viewing.locallyLocked, true)
    t.mock.restoreAll()
  }
  reset()
  applyViewingSession(session({ mode: 'private' }))
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => session({ mode: 'children', generation: 2 }) }))
  await changeViewingMode('children')
  assert.equal(viewing.mode, 'children')
  assert.equal(viewing.locallyLocked, false)
})
test('children cannot request management unlock', async () => {
  reset()
  applyViewingSession(session({ mode: 'children' }))
  assert.equal(await ensureManagement(), false)
  assert.equal(viewing.prompt, null)
})
test('first private marking sets a password then grants management without changing mode', async t => {
  reset()
  applyViewingSession(session({ enabled: false, generation: 0 }))
  const requests = []
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    requests.push([path, JSON.parse(options.body)])
    return { ok: true, json: async () => session({ management_unlocked: path.endsWith('/unlock') }) }
  })
  const result = ensureManagement()
  assert.equal(viewing.prompt.purpose, 'setup')
  await submitProtection('demo-pass', 'wrong')
  assert.equal(requests.length, 0)
  await submitProtection('demo-pass', 'demo-pass')
  assert.equal(await result, true)
  assert.equal(viewing.mode, 'normal')
  assert.equal(viewing.management_unlocked, true)
  assert.equal(viewing.revision, 0, 'First setup must preserve the pending content marking action')
  assert.deepEqual(requests.map(([path]) => path), ['/api/viewing/password', '/api/viewing/unlock'])
})
test('wrong passwords remain in the prompt and cancellation resolves the caller', async t => {
  reset()
  applyViewingSession(session())
  t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 403,
    text: async () => JSON.stringify({ detail: '密码不正确', code: 'protection_password_invalid' }) }))
  const pending = requestProtection('mode', 'private')
  await submitProtection('wrong')
  assert.match(viewing.promptError, /密码不正确/)
  assert.equal(viewing.mode, 'normal')
  cancelProtection()
  assert.equal(await pending, false)
})
test('playback is saved before permission changes and stale refreshes cannot restore the old mode', async t => {
  reset()
  applyViewingSession(session())
  let resolveRefresh
  const order = []
  t.mock.method(globalThis, 'fetch', async path => {
    if (path.endsWith('/session')) return new Promise(resolve => { resolveRefresh = () => resolve({ ok: true, json: async () => session() }) })
    order.push('mode-request')
    return { ok: true, json: async () => session({ mode: 'private', generation: 2 }) }
  })
  const stop = registerViewingPlaybackCleanup(async () => { order.push('saved-playback') })
  t.after(stop)
  const refresh = refreshViewing()
  await changeViewingMode('private', 'demo-pass')
  resolveRefresh()
  await refresh
  assert.deepEqual(order, ['saved-playback', 'mode-request'])
  assert.equal(viewing.mode, 'private')
})
