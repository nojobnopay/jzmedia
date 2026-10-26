import test from 'node:test'
import assert from 'node:assert/strict'
import { useCasting, castStateLabel } from '../src/useCasting.js'

const pending = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const make = (request, extra = {}) => useCasting({ media: () => ({ version_id: 12, kind: 'episode' }), position: () => 123, request, ...extra })
const session = id => ({ id, active: true, state: 'TRANSITIONING', position_known: false, device: { name: '电视' } })
const appDevice = { id: 'tv_bedroom', name: '主卧 jzmedia-tv', address: '192.168.1.72', receiver_type: 'jzmedia_tv', volume_control: false }
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }

for (const receiverType of ['dlna', 'jzmedia_tv']) {
  test(`${receiverType} seek keeps the submitted target through command acknowledgement and stale playback reports`, async t => {
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
    const response = pending()
    const original = { ...session('playing'), state: 'PLAYING', receiver_type: receiverType,
      duration: 300, position: 20, last_position: 20, position_known: true }
    let reported = { ...original, position: 23, last_position: 23 }
    const casting = make(async (path) => path.endsWith('/control') ? response.promise : reported)
    t.after(() => casting.dispose())
    casting.session.value = original
    const work = casting.control('seek', 110)
    assert.equal(casting.displayPosition.value, 110, 'The target must stay visible as soon as the command is submitted')
    assert.equal(casting.session.value.position, 20, 'Requested progress is never saved as actual TV progress')
    assert.equal(casting.pendingSeek.value.phase, 'sending')
    response.resolve({ ...original, state: 'TRANSITIONING', position_known: receiverType === 'jzmedia_tv' })
    await work
    assert.equal(casting.displayPosition.value, 110)
    assert.equal(casting.pendingSeek.value.phase, 'waiting')
    assert.match(casting.stateLabel.value, /正在跳转.*等待电视确认/)
    t.mock.timers.tick(3000)
    await settle()
    assert.equal(casting.session.value.position, 23)
    assert.equal(casting.displayPosition.value, 110, 'A stale PLAYING response must not undo the submitted target')
    reported = { ...original, position: 113, last_position: 113 }
    t.mock.timers.tick(3000)
    await settle()
    assert.equal(casting.pendingSeek.value, null)
    assert.equal(casting.seekPending.value, false)
    assert.equal(casting.displayPosition.value, 113)
    assert.equal(casting.stateLabel.value, '电视正在播放')
  })
}

test('a subsequent absolute seek replaces the target, and backward seeks ignore old progress ahead of it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 120, last_position: 120, position_known: true }
  let reported = { ...original, position: 123, last_position: 123 }
  const casting = make(async path => path.endsWith('/control') ? { ...original, state: 'TRANSITIONING' } : reported)
  t.after(() => casting.dispose())
  casting.session.value = original
  await casting.control('seek', 200)
  await casting.control('seek', 110)
  assert.equal(casting.displayPosition.value, 110)
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, true)
  assert.equal(casting.displayPosition.value, 110)
  reported = { ...original, state: 'PAUSED_PLAYBACK', position: 110, last_position: 110 }
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.displayPosition.value, 110)
})

test('a backward ten-second seek never confirms a frozen old position as playback time passes', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 120, last_position: 120, position_known: true }
  const casting = make(async path => path.endsWith('/control') ? { ...original, state: 'TRANSITIONING' } : original)
  t.after(() => casting.dispose())
  casting.session.value = original
  await casting.control('seek', 110)
  for (let i = 0; i < 4; i++) {
    t.mock.timers.tick(3000)
    await settle()
    assert.equal(casting.seekPending.value, true, `The unchanged 120-second report is stale after ${(i + 1) * 3} seconds`)
    assert.equal(casting.displayPosition.value, 110)
    assert.equal(casting.session.value.position, 120)
  }
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, false)
  assert.match(casting.error.value, /电视尚未确认跳转/)
})

test('a rejected old report cannot age into confirmation even when it differs from the submitted origin', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 120, last_position: 120, position_known: true }
  const frozen = { ...original, position: 121, last_position: 121 }
  const casting = make(async path => path.endsWith('/control') ? { ...original, state: 'TRANSITIONING' } : frozen)
  t.after(() => casting.dispose())
  casting.session.value = original
  await casting.control('seek', 110)
  for (let i = 0; i < 4; i++) { t.mock.timers.tick(3000); await settle() }
  assert.equal(casting.seekPending.value, true)
  assert.equal(casting.displayPosition.value, 110)
})

test('a slow command acknowledgement includes elapsed playback since submission when confirming seek progress', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const acknowledgement = pending()
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  let polls = 0
  const casting = make(async path => {
    if (path.endsWith('/control')) return acknowledgement.promise
    polls++
    return { ...original, position: 121, last_position: 121 }
  })
  t.after(() => casting.dispose())
  casting.session.value = original
  const work = casting.control('seek', 110)
  t.mock.timers.tick(8000)
  await settle()
  assert.equal(casting.pendingSeek.value.phase, 'sending')
  assert.equal(casting.displayPosition.value, 110)
  acknowledgement.resolve({ ...original, state: 'TRANSITIONING' })
  await work
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(polls, 1)
  assert.equal(casting.seekPending.value, false, 'The TV has played eleven seconds since seeking, including the slow acknowledgement')
  assert.equal(casting.displayPosition.value, 121)
  assert.equal(casting.error.value, '')
})

test('a slow backward seek acknowledgement waits past a matching old-position sample and then confirms new progress', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const acknowledgement = pending()
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 120, last_position: 120, position_known: true }
  let reported = original
  const casting = make(async path => path.endsWith('/control') ? acknowledgement.promise : reported)
  t.after(() => casting.dispose())
  casting.session.value = original
  const work = casting.control('seek', 110)
  t.mock.timers.tick(7000)
  acknowledgement.resolve({ ...original, state: 'TRANSITIONING' })
  await work
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, true, 'At ten seconds the true new progress could equal the old position, so that single ambiguous report cannot confirm it')
  reported = { ...original, position: 123, last_position: 123 }
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.displayPosition.value, 123)
  assert.equal(casting.error.value, '')
})

test('pausing after a slow seek acknowledgement confirms the real position including playback before the pause', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const acknowledgement = pending()
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  const paused = { ...original, state: 'PAUSED_PLAYBACK', position: 118, last_position: 118 }
  const casting = make(async (path, options) => {
    if (path.endsWith('/control')) return JSON.parse(options.body).command === 'seek'
      ? acknowledgement.promise : { ...paused, state: 'TRANSITIONING' }
    return paused
  })
  t.after(() => casting.dispose())
  casting.session.value = original
  const work = casting.control('seek', 110)
  t.mock.timers.tick(8000)
  acknowledgement.resolve({ ...original, state: 'TRANSITIONING' })
  await work
  await casting.control('pause')
  assert.equal(casting.seekPending.value, true)
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.displayPosition.value, 118)
  assert.equal(casting.session.value.state, 'PAUSED_PLAYBACK')
  assert.equal(casting.error.value, '')
})

test('a rejected seek restores reported progress with its error', async t => {
  const casting = make(async () => { throw Object.assign(new Error('跳转位置超出视频范围'), { status: 422 }) })
  t.after(() => casting.dispose())
  casting.session.value = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  assert.equal(await casting.control('seek', 110), false)
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.displayPosition.value, 20)
  assert.match(casting.error.value, /超出视频范围/)
})

test('an uncertain seek keeps its target until a real readback confirms execution and clears the transient error', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  const casting = make(async path => {
    if (path.endsWith('/control')) throw new TypeError('Failed to fetch')
    return { ...original, position: 112, last_position: 112 }
  })
  t.after(() => casting.dispose())
  casting.session.value = original
  assert.equal(await casting.control('seek', 110), false)
  assert.equal(casting.displayPosition.value, 110)
  assert.equal(casting.error.value, 'Failed to fetch')
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.displayPosition.value, 112)
  assert.equal(casting.error.value, '')
})

test('a seek without confirming progress expires with a useful error instead of keeping guessed progress forever', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  const casting = make(async path => path.endsWith('/control') ? { ...original, state: 'TRANSITIONING' } : original)
  t.after(() => casting.dispose())
  casting.session.value = original
  await casting.control('seek', 110)
  for (let i = 0; i < 5; i++) { t.mock.timers.tick(3000); await settle() }
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.displayPosition.value, 20)
  assert.match(casting.error.value, /电视尚未确认跳转.*重试/)
})

test('stopping, forgetting or disposing a cast clears pending seek feedback and its timeout', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  for (const action of ['stop', 'forget', 'dispose']) {
    const original = { ...session('playing'), state: 'PLAYING', duration: 300,
      position: 20, last_position: 20, position_known: true }
    const casting = make(async (_path, options) => JSON.parse(options.body).command === 'stop'
      ? { ...original, active: false, state: 'STOPPED' } : { ...original, state: 'TRANSITIONING' })
    t.after(() => casting.dispose())
    casting.session.value = original
    await casting.control('seek', 110)
    if (action === 'stop') await casting.control('stop')
    else casting[action]()
    assert.equal(casting.pendingSeek.value, null)
    t.mock.timers.tick(15000)
    await settle()
    assert.equal(casting.error.value, '')
  }
})

test('a late seek response after forgetting a session cannot restore its target or progress', async t => {
  const response = pending()
  const casting = make(async () => response.promise)
  t.after(() => casting.dispose())
  casting.session.value = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  const work = casting.control('seek', 110)
  casting.forget()
  response.resolve({ ...session('playing'), position_known: true, position: 110 })
  await work
  assert.equal(casting.session.value, null)
  assert.equal(casting.pendingSeek.value, null)
})

test('pause and volume preserve a pending seek, and an aborted concurrent poll cannot confirm it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const stale = pending()
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  let reads = 0, staleSignal
  const notified = []
  const casting = make(async (path, options) => {
    if (path.endsWith('/control')) {
      const body = JSON.parse(options.body)
      return { ...original, state: body.command === 'volume' ? 'PLAYING' : 'TRANSITIONING', volume: body.value }
    }
    if (++reads === 1) { staleSignal = options.signal; return stale.promise }
    return { ...original, state: 'PAUSED_PLAYBACK', position: 110, last_position: 110 }
  }, { onSession: value => { notified.push(value) } })
  t.after(() => casting.dispose())
  casting.session.value = original
  await casting.control('seek', 110)
  t.mock.timers.tick(3000)
  await settle()
  await casting.control('pause')
  assert.equal(staleSignal.aborted, true)
  assert.equal(casting.displayPosition.value, 110)
  assert.equal(casting.seekPending.value, true)
  stale.resolve({ ...original, position: 110, last_position: 110 })
  await settle()
  assert.equal(casting.seekPending.value, true, 'An aborted read cannot confirm a command even if its position happens to match')
  await casting.control('volume', 70)
  assert.equal(casting.session.value.volume, 70)
  assert.equal(casting.displayPosition.value, 110)
  assert.equal(casting.seekPending.value, true)
  assert.ok(notified.every(value => value.last_position === 20), 'Phone handoff receives only reported positions, never the optimistic target')
  t.mock.timers.tick(3000)
  await settle()
  assert.equal(casting.seekPending.value, false)
  assert.equal(casting.session.value.last_position, 110)
})

test('a restore started before seeking cannot overwrite the command, while a newly observed session clears its target', async t => {
  const oldRead = pending()
  const original = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  const replacement = { ...original, id: 'replacement', position: 40, last_position: 40 }
  let reads = 0
  const casting = make(async path => {
    if (path.startsWith('/api/cast/current')) return ++reads === 1 ? oldRead.promise : { session: replacement }
    return { ...original, state: 'TRANSITIONING' }
  })
  t.after(() => casting.dispose())
  casting.session.value = original
  const oldRestore = casting.restore()
  await casting.control('seek', 110)
  oldRead.resolve({ session: { ...original, position: 110, last_position: 110 } })
  await oldRestore
  assert.equal(casting.seekPending.value, true, 'The restore predates the user command and must be ignored')
  await casting.restore()
  assert.equal(casting.session.value.id, 'replacement')
  assert.equal(casting.pendingSeek.value, null)
  assert.equal(casting.displayPosition.value, 40)
})

test('a late seek failure after forgetting a session cannot add an error to its replacement', async t => {
  const response = pending()
  const casting = make(async () => response.promise)
  t.after(() => casting.dispose())
  casting.session.value = { ...session('playing'), state: 'PLAYING', duration: 300,
    position: 20, last_position: 20, position_known: true }
  const work = casting.control('seek', 110)
  casting.forget()
  casting.session.value = { ...session('replacement'), position_known: true, position: 40 }
  response.resolve(Promise.reject(new Error('迟到网络错误')))
  await work
  assert.equal(casting.session.value.id, 'replacement')
  assert.equal(casting.displayPosition.value, 40)
  assert.equal(casting.pendingSeek.value, null)
  assert.equal(casting.error.value, '')
})

test('TV App discovery requires an explicit receiver choice and ignores a closed picker response', async t => {
  const listing = pending()
  const casting = make(async () => listing.promise)
  t.after(() => casting.dispose())
  casting.open.value = true
  const opening = casting.chooseReceiver('jzmedia_tv')
  assert.equal(casting.receiverType.value, 'jzmedia_tv')
  casting.close()
  listing.resolve({ items: [appDevice] })
  await opening
  assert.deepEqual(casting.appDevices.value, [])
  assert.equal(casting.selected.value, '')
  assert.equal(casting.searchingApps.value, false)
})

test('online TV Apps never auto-select the first device and preserve only an explicitly still-online selection', async t => {
  let items = [appDevice, { ...appDevice, id: 'tv_living', name: '客厅' }]
  const casting = make(async () => ({ items }))
  t.after(() => casting.dispose())
  await casting.chooseReceiver('jzmedia_tv')
  assert.deepEqual(casting.appDevices.value, items)
  assert.equal(casting.selected.value, '')
  casting.selected.value = appDevice.id
  items = [items[1]]
  await casting.refreshApps()
  assert.equal(casting.selected.value, '', 'An offline target must not silently become another television')
})

test('TV App creation passes the frozen absolute position and explicit phone track choices', async t => {
  const calls = [], saving = pending()
  let position = 142.5
  const casting = make(async (path, options) => {
    calls.push({ path, options })
    if (path === '/api/cast/apps') return { items: [appDevice] }
    return { ...session('app-session'), receiver_type: 'jzmedia_tv', state: 'WAITING_FOR_APP', resume_pending: true,
      resume_target: 142.5, version_id: 12, kind: 'episode', device: appDevice }
  }, { position: () => position, media: () => ({ version_id: 12, kind: 'episode', audio: 1, sub: -1 }), beforePlay: () => saving.promise })
  t.after(() => casting.dispose())
  await casting.chooseReceiver('jzmedia_tv')
  casting.selected.value = appDevice.id
  const work = casting.play()
  position = 199
  saving.resolve()
  await work
  const sent = JSON.parse(calls.find(call => call.path === '/api/cast/sessions').options.body)
  assert.deepEqual(sent, { version_id: 12, kind: 'episode', audio: 1, sub: -1, device_id: appDevice.id,
    base_url: '', position: 142.5, receiver_type: 'jzmedia_tv' })
  assert.equal(casting.active.value, true)
  assert.equal(casting.session.value.position_known, false)
  assert.match(casting.stateLabel.value, /等待.*jzmedia-tv/)
})

test('switching a DLNA cast to TV App stops first and keeps its real position despite the stopped response resetting', async t => {
  const calls = []
  let position = 212
  const casting = make(async (path, options) => {
    calls.push({ path, options })
    if (path.endsWith('/control')) { position = 0; return { ...session('old'), active: false, state: 'STOPPED', receiver_type: 'dlna' } }
    if (path === '/api/cast/apps') return { items: [appDevice] }
    return { ...session('new'), receiver_type: 'jzmedia_tv', device: appDevice }
  }, { position: () => position })
  t.after(() => casting.dispose())
  casting.session.value = { ...session('old'), receiver_type: 'dlna', position: 212, position_known: true }
  await casting.chooseReceiver('jzmedia_tv')
  assert.ok(calls[0].path.endsWith('/control'))
  assert.equal(JSON.parse(calls[0].options.body).command, 'stop')
  assert.equal(casting.selected.value, '')
  casting.selected.value = appDevice.id
  await casting.play()
  assert.equal(JSON.parse(calls.find(call => call.path === '/api/cast/sessions').options.body).position, 212)
})

test('a failed DLNA Stop never starts TV App discovery or changes the receiver route', async t => {
  const calls = []
  const casting = make(async path => { calls.push(path); throw Object.assign(new Error('502 TV not reachable'), { status: 502 }) })
  t.after(() => casting.dispose())
  casting.session.value = { ...session('old'), receiver_type: 'dlna' }
  assert.equal(await casting.chooseReceiver('jzmedia_tv'), false)
  assert.equal(casting.receiverType.value, 'dlna')
  assert.equal(casting.active.value, true)
  assert.equal(calls.length, 1)
})

test('switching to TV App uses newer real progress sampled by a successful Stop', async t => {
  let sent
  const casting = make(async (path, options) => {
    if (path.endsWith('/control')) return { ...session('old'), active: false, state: 'STOPPED',
      position: 217, position_known: true, last_position: 217 }
    if (path === '/api/cast/apps') return { items: [appDevice] }
    sent = JSON.parse(options.body)
    return { ...session('new'), receiver_type: 'jzmedia_tv', device: appDevice }
  }, { position: () => 212 })
  t.after(() => casting.dispose())
  casting.session.value = { ...session('old'), receiver_type: 'dlna', position: 212, position_known: true }
  await casting.chooseReceiver('jzmedia_tv')
  casting.selected.value = appDevice.id
  await casting.play()
  assert.equal(sent.position, 217)
})

test('an old server gives a useful TV App upgrade message without touching DLNA discovery', async t => {
  const calls = []
  const casting = make(async path => { calls.push(path); throw Object.assign(new Error('404'), { status: 404 }) })
  t.after(() => casting.dispose())
  await casting.chooseReceiver('jzmedia_tv')
  assert.match(casting.appsError.value, /升级服务器和 jzmedia-tv/)
  assert.deepEqual(calls, ['/api/cast/apps'])
})

test('an immediately rejected App session restores the phone without presenting active TV playback', async t => {
  let restored = 0, held = 0
  const casting = make(async () => ({ ...session('rejected'), active: false, state: 'EXPIRED', receiver_type: 'jzmedia_tv' }),
    { onPlayFailure: () => { restored++ }, onSession: () => { held++ } })
  t.after(() => casting.dispose())
  casting.receiverType.value = 'jzmedia_tv'
  casting.selected.value = appDevice.id
  await casting.play()
  assert.equal(restored, 1)
  assert.equal(held, 0)
  assert.equal(casting.active.value, false)
})

test('closing discovery cancels it and ignores late devices', async () => {
  const result = pending(), calls = []
  const casting = make(async (path, options) => { calls.push({ path, options }); return result.promise })
  const work = casting.discover()
  casting.close()
  assert.equal(calls[0].options.signal.aborted, true)
  result.resolve({ items: [{ id: 'late' }] })
  await work
  assert.deepEqual(casting.devices.value, [])
  assert.equal(casting.searching.value, false)
  casting.dispose()
})

test('only latest discovery may replace selection and the server address', async () => {
  const a = pending(), b = pending()
  let index = 0
  const casting = make(async () => (++index === 1 ? a.promise : b.promise))
  const first = casting.discover(), second = casting.discover()
  b.resolve({ items: [{ id: 'new' }], base_url: 'http://192.168.1.5:8080' })
  await second
  casting.selected.value = 'new'
  a.resolve({ items: [{ id: 'old' }], base_url: 'http://192.168.1.6' })
  await first
  assert.equal(casting.selected.value, 'new')
  assert.equal(casting.serverUrl.value, 'http://192.168.1.5:8080')
  casting.dispose()
})

test('manual addition cancels discovery and selects the validated receiver', async () => {
  const discovery = pending(), calls = []
  const item = { id: 'tv', name: '主卧的小米电视' }
  const casting = make(async (path, options) => {
    calls.push({ path, options })
    return path === '/api/cast/discover' ? discovery.promise : { item, base_url: 'http://192.168.1.98:58080' }
  })
  const search = casting.discover()
  casting.manualLocation.value = ' http://192.168.1.72:49152/description.xml '
  await casting.addManual()
  assert.equal(calls[0].options.signal.aborted, true)
  assert.deepEqual(JSON.parse(calls[1].options.body), { location: 'http://192.168.1.72:49152/description.xml' })
  discovery.resolve({ items: [] })
  await search
  assert.deepEqual(casting.devices.value, [item])
  assert.equal(casting.selected.value, 'tv')
  assert.equal(casting.serverUrl.value, 'http://192.168.1.98:58080')
  casting.dispose()
})

test('play waits for local progress, sends episode identity and locks closing until acknowledged', async () => {
  const result = pending(), calls = []
  let saved = false, accepted = null
  const casting = make(async (path, options) => { assert.ok(saved); calls.push({ path, options }); return result.promise },
    { beforePlay: async () => { saved = true }, onSession: value => { accepted = value } })
  casting.selected.value = 'device'
  casting.serverUrl.value = 'http://192.168.1.5:8080'
  casting.open.value = true
  const work = casting.play()
  casting.close()
  assert.equal(casting.open.value, true)
  await Promise.resolve()
  assert.deepEqual(JSON.parse(calls[0].options.body), { device_id: 'device', base_url: 'http://192.168.1.5:8080', version_id: 12, kind: 'episode', position: 123, receiver_type: 'dlna' })
  result.resolve(session('cast1'))
  await work
  assert.equal(accepted.id, 'cast1')
  assert.equal(casting.active.value, true)
  casting.close()
  casting.dispose()
  assert.equal(calls.length, 1, 'Closing or unloading must not issue Stop')
})

test('late current-session response cannot replace a newly requested cast', async () => {
  const previous = pending()
  const casting = make(async path => path.startsWith('/api/cast/current') ? previous.promise : session('new'))
  const restore = casting.restore()
  casting.selected.value = 'device'
  await casting.play()
  previous.resolve({ session: session('old') })
  await restore
  assert.equal(casting.session.value.id, 'new')
  casting.dispose()
})

test('stop failure keeps remote session and returns false for phone handoff', async () => {
  const casting = make(async path => {
    if (path.startsWith('/api/cast/current')) return { session: session('active') }
    throw new Error('502 电视离线')
  })
  await casting.restore()
  assert.equal(await casting.control('stop'), false)
  assert.equal(casting.active.value, true)
  assert.match(casting.error.value, /电视离线/)
  casting.dispose()
})

test('disposed controller ignores a late creation response', async () => {
  const result = pending()
  let notified = 0
  const casting = make(async () => result.promise, { onSession: () => { notified++ } })
  casting.selected.value = 'device'
  const work = casting.play()
  await Promise.resolve()
  casting.dispose()
  result.resolve(session('late'))
  await work
  assert.equal(notified, 0)
  assert.equal(casting.session.value, null)
})

test('remote state labels distinguish command acknowledgement from actual playback', () => {
  assert.match(castStateLabel(session('pending')), /等待电视状态/)
  assert.equal(castStateLabel({ state: 'PLAYING' }), '电视正在播放')
  assert.equal(castStateLabel({ state: 'STOPPED' }), '投屏已停止')
  assert.equal(castStateLabel({ state: 'REPLACED' }), '电视已切换到其他视频')
})

test('play captures identity, receiver and absolute position before a delayed local save', async () => {
  const saving = pending(), calls = []
  let position = 100, kind = 'movie', prepared, failures = 0
  const casting = make(async (path, options) => { calls.push({ path, body: JSON.parse(options.body) }); return session('new') }, {
    media: () => ({ version_id: 7, kind }), position: () => position,
    beforePlay: target => { prepared = target; return saving.promise }, onPlayFailure: () => { failures++ },
  })
  casting.selected.value = 'first'
  casting.serverUrl.value = 'http://192.168.1.8:8080'
  const work = casting.play()
  assert.equal(prepared, 100)
  position = 135; kind = 'episode'; casting.selected.value = 'second'
  saving.resolve()
  await work
  assert.deepEqual(calls[0].body, { version_id: 7, kind: 'movie', device_id: 'first', base_url: 'http://192.168.1.8:8080', position: 100, receiver_type: 'dlna' })
  assert.equal(failures, 0)
  casting.dispose()
})

test('a failed cast restores its preparation once, but never after disposal', async () => {
  const result = pending()
  let restored = 0
  const casting = make(async () => result.promise, { onPlayFailure: () => { restored++ } })
  casting.selected.value = 'tv'
  const work = casting.play()
  await Promise.resolve()
  result.resolve(Promise.reject(Object.assign(new Error('接收器不支持'), { status: 422 })))
  await work
  assert.equal(restored, 1)
  assert.equal(casting.busy.value, false)
  assert.equal(casting.error.value, '接收器不支持')
  casting.dispose()
  const late = pending()
  const another = make(async () => late.promise, { onPlayFailure: () => { restored++ } })
  another.selected.value = 'tv'
  const waiting = another.play()
  await Promise.resolve()
  another.dispose()
  late.resolve(Promise.reject(new Error('迟到失败')))
  await waiting
  assert.equal(restored, 1)
})

test('an uncertain creation adopts only a new matching receiver session instead of resuming the phone', async () => {
  let resumed = 0, held = 0
  const calls = []
  const remote = { ...session('started-remotely'), version_id: 12, kind: 'episode', device: { id: 'tv', name: '电视' } }
  const casting = make(async (path, options) => {
    calls.push({ path, options })
    if (options.method === 'POST') throw new TypeError('Failed to fetch')
    return { session: remote }
  }, { onPlayFailure: () => { resumed++ }, onSession: () => { held++ } })
  casting.selected.value = 'tv'
  await casting.play()
  assert.equal(casting.session.value.id, remote.id)
  assert.equal(casting.uncertain.value, false)
  assert.equal(held, 1)
  assert.equal(resumed, 0)
  assert.equal(calls.length, 2)
  assert.equal(calls[1].options.timeout, 5000)
  assert.equal(calls.some(call => call.path.endsWith('/control')), false)
  casting.dispose()
})

for (const status of [408, 500, 502, 504]) {
  test(`HTTP ${status} creation checks for an already-started matching TV before restoring the phone`, async () => {
    let resumed = 0, reads = 0
    const remote = { ...session('started-remotely'), version_id: 12, kind: 'episode', device: { id: 'tv', name: '电视' } }
    const casting = make(async (_path, options) => {
      if (options.method === 'POST') throw Object.assign(new Error(`${status} operation result unknown`), { status })
      reads++
      return { session: remote }
    }, { onPlayFailure: () => { resumed++ } })
    casting.selected.value = 'tv'
    await casting.play()
    assert.equal(reads, 1)
    assert.equal(casting.session.value.id, remote.id)
    assert.equal(resumed, 0)
    casting.dispose()
  })
}

test('a failed creation resumes the phone only after a readable current response confirms absence', async () => {
  let resumed = 0
  const casting = make(async (_path, options) => {
    if (options.method === 'POST') throw new Error('请求超时，操作结果未知')
    return { session: null }
  }, { onPlayFailure: () => { resumed++ } })
  casting.selected.value = 'tv'
  await casting.play()
  assert.equal(resumed, 1)
  assert.equal(casting.uncertain.value, false)
  casting.dispose()
})

test('creation plus read failure keeps the phone held and rechecks without repeating Play or Stop', async () => {
  let readsWork = false, resumed = 0, held = 0, posts = 0
  const casting = make(async (_path, options) => {
    if (options.method === 'POST') { posts++; throw new TypeError('Failed to fetch') }
    if (!readsWork) throw new TypeError('Failed to fetch')
    return { session: null }
  }, { onPlayFailure: () => { resumed++ }, onPlayUncertain: () => { held++ } })
  casting.selected.value = 'tv'
  await casting.play()
  assert.equal(casting.uncertain.value, true)
  assert.equal(resumed, 0)
  assert.equal(held, 1)
  assert.match(casting.error.value, /手机保持暂停/)
  await casting.play()
  assert.equal(posts, 1, 'Unknown results must not automatically repeat the create request')
  readsWork = true
  await casting.reconcile()
  assert.equal(casting.uncertain.value, false)
  assert.doesNotMatch(casting.error.value, /手机保持暂停/)
  assert.equal(resumed, 1)
  assert.equal(posts, 1)
  casting.dispose()
})

for (const conflict of ['previous-session', 'different-receiver', 'different-media']) {
  test(`unknown creation never adopts or stops a ${conflict} response`, async () => {
    let resumed = 0, posts = 0
    const remote = { ...session(conflict === 'previous-session' ? 'previous' : 'another'),
      version_id: conflict === 'different-media' ? 13 : 12, kind: 'episode',
      device: { id: conflict === 'different-receiver' ? 'other-tv' : 'tv', name: '电视' } }
    const casting = make(async (_path, options) => {
      if (options.method === 'POST') { posts++; throw new TypeError('Failed to fetch') }
      return { session: remote }
    }, { onPlayFailure: () => { resumed++ } })
    casting.session.value = { ...session('previous'), active: false }
    casting.selected.value = 'tv'
    await casting.play()
    assert.equal(casting.uncertain.value, true)
    assert.equal(casting.active.value, false)
    assert.equal(resumed, 0)
    assert.equal(posts, 1)
    casting.dispose()
  })
}

test('late uncertainty readbacks after disposal cannot adopt a session or resume the phone', async () => {
  const reading = pending()
  let resumed = 0, accepted = 0, signal
  const casting = make(async (_path, options) => {
    if (options.method === 'POST') throw new TypeError('Failed to fetch')
    signal = options.signal
    return reading.promise
  }, { onPlayFailure: () => { resumed++ }, onSession: () => { accepted++ } })
  casting.selected.value = 'tv'
  const work = casting.play()
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
  casting.dispose()
  assert.equal(signal.aborted, true)
  reading.resolve({ session: null })
  await work
  assert.equal(resumed, 0)
  assert.equal(accepted, 0)
})
