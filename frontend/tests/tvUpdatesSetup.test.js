import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'
import { loadTvUpdatesHistory } from '../src/tvUpdates.js'

const item = id => ({ tmdb_id: id, show_id: 100 + id, title: '合成剧集' + id, year: 2026, poster_path: '',
  events: [{ event_id: 'ep-' + id, season: 1, episode: id, air_date: '2026-10-01', title: '新的一集' }] })

function globals(t, extra = {}) {
  const values = new Map(), observers = []
  class Observer {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this) }
    observe(el) { this.targets.push(el) }
    disconnect() { this.disconnected = true }
    show(targets = this.targets) { this.callback(targets.map(target => ({ target, isIntersecting: true, intersectionRatio: 1 }))) }
  }
  const disk = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
  const replacement = { localStorage: disk, IntersectionObserver: Observer, ...extra }
  const descriptors = new Map(Object.keys(replacement).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  for (const [key, value] of Object.entries(replacement)) Object.defineProperty(globalThis, key, { configurable: true, value })
  t.after(() => {
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })
  return { disk, observers }
}

async function updates(t, initial = {}, respond = async () => ({ items: [] })) {
  const env = globals(t)
  const requests = [], exposed = Vue.ref(null)
  const props = Vue.reactive({ mediaLibraryId: 7, active: true, mode: 'weekly', ...initial })
  const component = await loadSfc(new URL('../src/components/TvUpdatesRow.vue', import.meta.url), {
    '../api.js': { posterUrl: path => path, api: (path, options) => { requests.push({ path, options }); return respond(path, options) } },
  })
  const wrapper = { setup: () => () => Vue.h(component, { ...props, ref: exposed }) }
  const ui = renderHarness(wrapper)
  t.after(() => ui.app.unmount())
  await flush()
  return { ...ui, ...env, props, exposed, requests }
}

test('only mounted, visible update cards consume the weekly reminder; expanding acknowledges the rest', async t => {
  const ui = await updates(t, {}, async () => ({ items: Array.from({ length: 8 }, (_, n) => item(n + 1)) }))
  assert.match(ui.text(), /8 部尚未收藏新集/)
  const first = ui.observers.at(-1)
  assert.equal(first.targets.length, 6)
  assert.equal(loadTvUpdatesHistory(ui.disk, 7).lastShown, 0)
  first.show(first.targets.slice(0, 2))
  assert.equal(Object.keys(loadTvUpdatesHistory(ui.disk, 7).seen).length, 2)
  ui.find(node => node.type === 'button' && nodeText(node).includes('展开其余')).props.onClick()
  await flush()
  const expanded = ui.observers.at(-1)
  assert.equal(expanded.targets.length, 8)
  expanded.show()
  assert.equal(Object.keys(loadTvUpdatesHistory(ui.disk, 7).seen).length, 8)
  assert.ok(ui.all().every(node => !(node.props.class || '').includes?.('poster-play')))
})

test('off and filtered walls do not request updates or consume intervals', async t => {
  const ui = await updates(t, { mode: 'off' })
  assert.equal(ui.requests.length, 0)
  ui.props.active = false
  ui.props.mode = 'weekly'
  await flush()
  assert.equal(ui.requests.length, 0)
  ui.props.active = true
  await flush()
  assert.equal(ui.requests.length, 1)
  assert.equal(loadTvUpdatesHistory(ui.disk, 7).lastShown, 0)
})

test('switching media libraries aborts obsolete requests and ignores late responses', async t => {
  const first = deferred(), second = deferred()
  const ui = await updates(t, {}, path => path.endsWith('=7') ? first.promise : second.promise)
  ui.props.mediaLibraryId = 9
  await flush()
  assert.equal(ui.requests[0].options.signal.aborted, true)
  second.resolve({ items: [item(9)] })
  await ui.exposed.value.ready()
  await flush()
  first.resolve({ items: [item(7)] })
  await flush()
  assert.match(ui.text(), /合成剧集9/)
  assert.doesNotMatch(ui.text(), /合成剧集7/)
  assert.equal(ui.exposed.value.snapshot().mediaId, 9)
})

test('return snapshots preserve expansion despite already-seen events and remove collected cards', async t => {
  const ui = await updates(t, { initialSnapshot: { mediaId: 7, items: [item(1), item(2)], expanded: true, showAll: true, intervalClaimed: true } },
    async () => ({ items: [item(2), item(3)] }))
  await ui.exposed.value.ready()
  assert.match(ui.text(), /合成剧集2/)
  assert.doesNotMatch(ui.text(), /合成剧集1|合成剧集3/)
  assert.equal(ui.exposed.value.snapshot().expanded, true)
  assert.equal(ui.exposed.value.snapshot().showAll, true)
})

test('an empty old batch cannot suppress newly discovered updates on return', async t => {
  const ui = await updates(t, { initialSnapshot: { mediaId: 7, items: [], expanded: false, batchAt: Date.now() } },
    async () => ({ items: [item(3)] }))
  assert.match(ui.text(), /合成剧集3/)
  assert.equal(ui.exposed.value.snapshot().expanded, true)
})

test('a return after a full week starts a fresh recommendation batch', async t => {
  const ui = await updates(t, { initialSnapshot: { mediaId: 7, items: [item(1)], expanded: false,
    batchAt: Date.now() - 8 * 86400000 } }, async () => ({ items: [item(2)] }))
  assert.match(ui.text(), /合成剧集2/)
  assert.doesNotMatch(ui.text(), /合成剧集1/)
  assert.equal(ui.exposed.value.snapshot().expanded, true)
})

test('failed update reads remain inline and can be retried without dismissing the wall', async t => {
  let count = 0
  const ui = await updates(t, {}, async () => { if (!count++) throw Error('网络中断'); return { items: [item(1)] } })
  assert.match(ui.text(), /暂时无法读取剧集更新：网络中断/)
  await ui.exposed.value.reload()
  await flush()
  assert.match(ui.text(), /合成剧集1/)
  assert.doesNotMatch(ui.text(), /网络中断/)
})

test('maintenance polls only an active running panel and manual checks retain normal authentication', async t => {
  let nextTimer = 0
  const timers = new Map()
  globals(t, { setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer }, clearTimeout: id => timers.delete(id) })
  const requests = [], props = Vue.reactive({ active: false })
  let running = false
  const component = await loadSfc(new URL('../src/components/TvAiringMaintenance.vue', import.meta.url), {
    '../api.js': { api: async (path, options) => {
      requests.push({ path, options })
      if (path.endsWith('/check')) {
        running = true
        return { configured: true, total: 3, checked: 1, pending: 2, failed: 0, running: false, status: 'queued' }
      }
      return { configured: true, total: 3, checked: 1, pending: 2, failed: 0, running, current: running ? '合成剧集' : '' }
    } },
  })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  await flush()
  assert.equal(requests.length, 0)
  props.active = true
  await flush()
  assert.equal(requests.length, 1)
  assert.equal(timers.size, 0)
  ui.find(node => node.type === 'button' && nodeText(node).includes('立即检查')).props.onClick()
  await flush()
  assert.equal(requests[1].options.method, 'POST')
  assert.equal(requests[1].options.body, '{}')
  assert.equal(requests[1].options.authPrompt, undefined)
  assert.match(ui.text(), /检查已排队/)
  assert.equal(timers.size, 1)
  const [timerId, poll] = [...timers][0]
  timers.delete(timerId)
  await poll()
  await flush()
  assert.equal(requests.length, 3)
  assert.match(ui.text(), /正在检查剧集播出资料/)
  assert.doesNotMatch(ui.text(), /合成剧集/)
  props.active = false
  await flush()
  assert.equal(timers.size, 0)
  assert.equal(requests[2].options.signal.aborted, true)
  props.active = true
  await flush()
  assert.equal(requests.length, 4)
  assert.equal(timers.size, 1)
})
