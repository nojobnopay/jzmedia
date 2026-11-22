import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const id = 'alt_' + 'a'.repeat(32)
const result = (status, items = [], retry_after_ms = 0) => ({ task_id: id, status, items, retry_after_ms })
const candidate = (name, token) => ({ source_name: name, line_label: '高清', throughput_kbps: 12000,
  recommendation_token: token.repeat(32) })

async function setup(t, { supported = true } = {}) {
  const requests = [], replies = [], selections = [], timers = new Map()
  const props = reactive({ identity: { item_id: 'fixture' }, waiting: false, failed: false,
    switchSource: async item => selections.push(item.source_name) })
  const component = await loadSfc(new URL('../src/components/OnlinePlaybackAlternatives.vue', import.meta.url), {
    '../api.js': { api: async (path, options) => {
      if (path === '/api/stream/client-info') return { features: supported ? ['online_alternatives_v2'] : [] }
      requests.push({ path, ...options })
      if (options.method === 'DELETE') return null
      return replies.shift().promise
    } },
  })
  t.mock.method(globalThis, 'setTimeout', (fn, delay) => { timers.set(fn, delay); return fn })
  t.mock.method(globalThis, 'clearTimeout', fn => timers.delete(fn))
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  async function tick(delay) {
    const fn = [...timers].find(([, ms]) => ms === delay)?.[0]
    assert.ok(fn, 'expected a scheduled step at ' + delay)
    timers.delete(fn); fn(); await flush()
  }
  return { requests, replies, selections, props, ui, timers, tick }
}
test('recovery cancels a queued task; a late sample never becomes a choice', async t => {
  const { requests, replies, props, ui, timers, tick } = await setup(t)
  props.waiting = true; await flush()
  props.waiting = false; await flush()
  assert.equal(timers.size, 0)
  props.waiting = true; await flush()
  const opening = deferred(), reading = deferred()
  replies.push(opening, reading)
  await tick(5000)
  opening.resolve(result('queued', [], 400)); await flush()
  assert.match(ui.text(), /等待探测名额/)
  await tick(400)
  props.waiting = false; await flush()
  assert.equal(requests[1].signal.aborted, true)
  assert.equal(requests[2].method, 'DELETE')
  reading.resolve(result('done', [candidate('过期推荐', 'a')])); await flush()
  assert.doesNotMatch(ui.text(), /过期推荐/)
})
test('a playable line appears before the next sample, stays selectable on media error and keeps its target when better results arrive', async t => {
  const { replies, selections, props, ui, tick } = await setup(t)
  const first = candidate('最早可用', 'a'), faster = candidate('第二条', 'b'), third = candidate('另一条可用线路', 'c')
  const a = deferred(), b = deferred(), c = deferred(), d = deferred(); replies.push(a, b, c, d)
  props.waiting = true; await flush(); await tick(5000)
  a.resolve(result('running')); await flush(); await tick(0)
  b.resolve(result('running', [first])); await flush()
  const buttons = () => ui.all().filter(n => n.type === 'button')
  const originalButton = buttons()[0]; assert.match(nodeText(originalButton), /最早可用/)
  await tick(0)
  props.waiting = false; props.failed = true; await flush()
  c.resolve(result('running', [faster, first])); await flush(); await tick(0)
  d.resolve(result('done', [faster, first, third])); await flush()
  assert.match(ui.text(), /12.0 Mbps/)
  assert.equal(buttons().length, 3); assert.equal(originalButton, buttons()[0]); assert.deepEqual(selections, [])
  await originalButton.props.onClick(); assert.deepEqual(selections, ['最早可用'])
  await buttons()[2].props.onClick(); assert.deepEqual(selections, ['最早可用', '另一条可用线路'])
})
test('cancelling a pending switch restores its original choice', async t => {
  const { replies, props, ui, tick } = await setup(t)
  const opening = deferred(), switching = deferred()
  let active
  props.switchSource = (_, signal) => { active = signal; return switching.promise }
  replies.push(opening)
  props.waiting = true; await flush(); await tick(5000)
  opening.resolve(result('done', [candidate('第一线路', 'a')])); await flush()
  const button = ui.all().find(n => n.type === 'button' && nodeText(n).includes('第一线路'))
  const picking = button.props.onClick(); await flush()
  ui.all().find(n => n.type === 'button' && nodeText(n).includes('取消切换')).props.onClick()
  assert.equal(active.aborted, true)
  switching.reject(new Error('cancelled')); await picking; await flush()
  assert.match(ui.text(), /第一线路/)
  assert.doesNotMatch(ui.text(), /准备所选线路|取消切换/)
})

test('a session error after original playback recovers dismisses the alternative controls', async t => {
  const { replies, props, ui, tick } = await setup(t)
  const opening = deferred(), switching = deferred()
  props.switchSource = () => switching.promise
  replies.push(opening)
  props.waiting = true; await flush(); await tick(5000)
  opening.resolve(result('done', [candidate('备用线路', 'a')])); await flush()
  const picking = ui.all().find(n => n.type === 'button').props.onClick()
  props.waiting = false; await flush()
  switching.reject(Object.assign(new Error(), { status: 429 })); await picking; await flush()
  assert.equal(ui.all().filter(n => n.type === 'button').length, 0)
})

test('unsupported servers keep the normal player without issuing any alternative task', async t => {
  const { requests, props, ui, tick } = await setup(t, { supported: false })
  props.waiting = true; await flush(); await tick(5000)
  assert.deepEqual(requests, [])
  assert.doesNotMatch(ui.text(), /探测|线路/)
})
