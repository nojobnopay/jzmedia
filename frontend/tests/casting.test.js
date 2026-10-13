import test from 'node:test'
import assert from 'node:assert/strict'
import { useCasting, castStateLabel } from '../src/useCasting.js'

const pending = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const make = (request, extra = {}) => useCasting({ media: () => ({ version_id: 12, kind: 'episode' }), position: () => 123, request, ...extra })
const session = id => ({ id, active: true, state: 'TRANSITIONING', position_known: false, device: { name: '电视' } })

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
  assert.deepEqual(JSON.parse(calls[0].options.body), { device_id: 'device', base_url: 'http://192.168.1.5:8080', version_id: 12, kind: 'episode', position: 123 })
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
