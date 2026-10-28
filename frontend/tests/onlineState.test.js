import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineStateClient, createOnlineProgressWriter, nextOnlineEpisode, loadOnlineAutoplay, saveOnlineAutoplay } from '../src/onlineState.js'

const SOURCE = 'src_' + '1'.repeat(32), ITEM = 'itm_' + '2'.repeat(32)
const EP_A = 'ep_' + '3'.repeat(32), EP_B = 'ep_' + '4'.repeat(32), EP_C = 'ep_' + '5'.repeat(32)
const LINE = 'ln_' + 'a'.repeat(32), OTHER_LINE = 'ln_' + 'b'.repeat(32)
const SCOPE = 'c'.repeat(64), NEW_SCOPE = 'd'.repeat(64), SID = 'e'.repeat(43), SID_NEW = 'f'.repeat(43), WRITER = 'g'.repeat(43)
const MOVIE = { source_id: SOURCE, item_id: ITEM, kind: 'movie', line_id: LINE }
const episode = id => ({ ...MOVIE, kind: 'episode', episode_id: id })
const snap = (position, extra = {}) => ({ identity: MOVIE, position, duration: 24, paused: false, completed: false, first_frame: true, ...extra })
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function progress(body, revision = body.writer_revision) {
  return { identity: MOVIE, position: body.position, duration: body.duration, paused: body.paused,
    completed: body.completed, updated_at: 100, writer_revision: revision }
}
function responder(calls, { claim = null, write = null } = {}) {
  return async (path, options) => {
    const body = JSON.parse(options.body)
    calls.push({ path, options, body })
    if (path.endsWith('/claim')) return claim ? claim(body, options) : { identity: MOVIE, writer_id: WRITER, writer_revision: body.expected_revision + 1, scope_token: SCOPE }
    return write ? write(body, options) : { progress: progress(body), scope_token: SCOPE }
  }
}

test('online library and item reads have separate cancellation ownership and carry only stable selectors', async () => {
  const calls = [], shelf = deferred(), detail = deferred()
  const client = createOnlineStateClient({ request: (path, options) => {
    calls.push({ path, options }); return path.includes('/library?') ? shelf.promise : detail.promise
  } })
  const first = client.library('favorites', 20), second = client.item(ITEM, { line_id: LINE })
  assert.equal(calls[0].options.signal.aborted, false)
  const url = new URL(calls[0].path, 'https://jzmedia.invalid')
  assert.equal(url.searchParams.get('limit'), '20'); assert.equal(url.searchParams.get('offset'), '20')
  const selected = new URL(calls[1].path, 'https://jzmedia.invalid')
  assert.equal(selected.searchParams.get('line_id'), LINE)
  client.cancel(); shelf.resolve({ items: ['old'] }); detail.resolve({ item_id: ITEM })
  assert.equal(await first, null); assert.equal(await second, null)
  client.close()
})

test('mutations freeze the initiating scope while history deletion accepts only a valid newer epoch', async () => {
  const calls = []
  const client = createOnlineStateClient({ request: async (path, options) => {
    calls.push({ path, options }); return { item_id: ITEM, favorite: true, deleted: 1, scope_token: options.method === 'DELETE' ? NEW_SCOPE : SCOPE }
  } })
  await client.favorite(ITEM, true, SCOPE)
  const deleted = await client.deleteHistory(ITEM, SCOPE)
  assert.equal(calls[0].options.headers['X-Online-State-Scope'], SCOPE)
  assert.equal(calls[1].options.headers['X-Online-State-Scope'], SCOPE)
  assert.equal(deleted.scope_token, NEW_SCOPE)
  await assert.rejects(client.favorite(ITEM, false, 'header\ninjection'), error => error.code === 'online_state_scope_required')
  assert.equal(calls.length, 2)
  client.close()
})

test('a writer claims the owned session once but ignores every startup snapshot before a real frame', async () => {
  const calls = []
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE, request: responder(calls) })
  writer.session({ identity: MOVIE, session_id: SID })
  await tick()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].path, '/api/online/state/progress/claim')
  assert.equal(writer.update(snap(0, { first_frame: false, reason: 'error' })), false)
  await writer.flush()
  assert.equal(calls.length, 1)
  await writer.close()
})

test('session preparation resolves only after the current writer claim is settled', async () => {
  const calls = [], claim = deferred()
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE,
    request: responder(calls, { claim: () => claim.promise }) })
  let prepared = false
  const preparation = writer.session({ identity: MOVIE, session_id: SID }).then(value => { prepared = value; return value })
  await tick()
  assert.equal(prepared, false)
  assert.equal(calls.length, 1)
  claim.resolve({ identity: MOVIE, writer_id: WRITER, writer_revision: 1, scope_token: SCOPE })
  assert.equal(await preparation, true)
  assert.equal(prepared, true)
  assert.equal(calls.filter(call => call.path.endsWith('/progress')).length, 0)
  await writer.close()
})

test('ticks are coalesced and completion keeps the actual terminal position', async () => {
  const calls = [], claim = deferred()
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE, intervalMs: 100000,
    request: responder(calls, { claim: () => claim.promise }) })
  writer.session({ identity: MOVIE, session_id: SID })
  for (const value of [2, 3, 4, 5]) writer.update(snap(value))
  claim.resolve({ identity: MOVIE, writer_id: WRITER, writer_revision: 1, scope_token: SCOPE })
  await writer.flush()
  const writes = calls.filter(call => call.path.endsWith('/progress'))
  assert.equal(writes.length, 1); assert.equal(writes[0].body.position, 5)
  writer.update(snap(24, { paused: true, completed: true, reason: 'ended' }))
  await writer.close()
  const final = calls.at(-1)
  assert.equal(final.body.position, 24); assert.equal(final.body.completed, true)
  assert.equal(final.options.keepalive, true)
  assert.equal(final.options.credentials, 'same-origin')
  assert.equal(final.options.headers['X-Online-State-Scope'], SCOPE)
})

test('one in-flight progress write retains only the newest pending seek, including a backwards seek', async () => {
  const calls = [], firstWrite = deferred()
  let writes = 0
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE, intervalMs: 100000,
    request: responder(calls, { write: body => ++writes === 1 ? firstWrite.promise : { progress: progress(body), scope_token: SCOPE } }) })
  writer.session({ identity: MOVIE, session_id: SID }); await tick()
  writer.update(snap(15)); const flushing = writer.flush(); await tick()
  writer.update(snap(16)); writer.update(snap(17)); writer.update(snap(3, { paused: true, reason: 'seek' }))
  const before = calls.filter(call => call.path.endsWith('/progress'))
  assert.equal(before.length, 1)
  firstWrite.resolve({ progress: progress(before[0].body), scope_token: SCOPE })
  await flushing; await writer.close()
  const after = calls.filter(call => call.path.endsWith('/progress'))
  assert.equal(after.length, 2); assert.equal(after[1].body.position, 3)
  assert.ok(after[1].body.seq > after[0].body.seq)
})

test('a writer conflict never reads a newer revision or reclaims ownership automatically', async () => {
  const calls = [], notices = []
  const conflict = Object.assign(new Error('private data must not appear'), { status: 409, code: 'online_progress_writer_conflict' })
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE, revision: 7,
    request: responder(calls, { claim: () => Promise.reject(conflict) }), onNotice: message => notices.push(message) })
  writer.session({ identity: MOVIE, session_id: SID }); await tick()
  writer.update(snap(3)); writer.session({ identity: MOVIE, session_id: SID_NEW }); await writer.close()
  assert.equal(calls.length, 1); assert.equal(calls[0].body.expected_revision, 7)
  assert.equal(writer.snapshot().disabled, true)
  assert.equal(notices.length, 1); assert.equal(notices[0].includes('private data'), false)
})

test('renewing the same playback uses the known owned revision and immutable scope', async () => {
  const calls = []
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE, revision: 4, intervalMs: 100000, request: responder(calls) })
  writer.session({ identity: MOVIE, session_id: SID }); await tick()
  writer.update(snap(8)); await writer.flush()
  writer.session({ identity: MOVIE, session_id: SID_NEW }); await tick()
  writer.update(snap(9, { reason: 'pause', paused: true })); await writer.close()
  const claims = calls.filter(call => call.path.endsWith('/claim'))
  assert.equal(claims.length, 2)
  assert.deepEqual(claims.map(call => call.body.expected_revision), [4, 5])
  assert.ok(calls.every(call => call.options.headers['X-Online-State-Scope'] === SCOPE))
})

test('an unexpected response scope disables a writer without exposing the foreign result', async () => {
  const calls = [], saved = []
  const writer = createOnlineProgressWriter({ identity: MOVIE, scopeToken: SCOPE,
    request: responder(calls, { claim: () => ({ identity: MOVIE, writer_id: WRITER, writer_revision: 1, scope_token: NEW_SCOPE }) }),
    onSaved: value => saved.push(value) })
  writer.session({ identity: MOVIE, session_id: SID }); await tick(); writer.update(snap(3)); await writer.close()
  assert.equal(calls.length, 1); assert.equal(saved.length, 0); assert.equal(writer.snapshot().disabled, true)
})

test('autoplay is opt-in and preferences contain only a mode-scoped boolean', () => {
  const values = new Map(), storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
  assert.equal(loadOnlineAutoplay('normal', storage), false)
  saveOnlineAutoplay(true, 'private', storage)
  assert.equal(loadOnlineAutoplay('normal', storage), false)
  assert.equal(loadOnlineAutoplay('private', storage), true)
  assert.deepEqual([...values.values()], ['true'])
  assert.equal(loadOnlineAutoplay('normal', { getItem() { throw new Error('storage unavailable') } }), false)
})

test('next episode follows fresh order only within the exact source, item and line', () => {
  const first = episode(EP_A), second = episode(EP_B), third = episode(EP_C)
  const detail = { source: { id: SOURCE }, item: { id: ITEM, kind: 'tv' }, lines: [
    { id: LINE, entries: [{ identity: first, supported: true }, { identity: second, supported: false }, { identity: third, supported: true }] },
    { id: OTHER_LINE, entries: [{ identity: { ...second, line_id: OTHER_LINE }, supported: true }] },
  ] }
  assert.equal(nextOnlineEpisode(detail, first), null, 'An unsupported next episode must never skip forward')
  assert.equal(nextOnlineEpisode(detail, second).identity.episode_id, EP_C)
  detail.lines[0].entries[1].supported = true
  assert.equal(nextOnlineEpisode(detail, first).identity.episode_id, EP_B)
  assert.equal(nextOnlineEpisode(detail, third), null)
  assert.equal(nextOnlineEpisode({ ...detail, source: { id: 'src_' + '9'.repeat(32) } }, first), null)
  assert.equal(nextOnlineEpisode(detail, { ...first, line_id: OTHER_LINE }), null)
  assert.equal(nextOnlineEpisode(detail, MOVIE), null)
})
