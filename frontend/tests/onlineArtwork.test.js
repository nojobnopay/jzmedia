import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred } from './helpers/renderHarness.js'

const path = '/api/online/catalog/artwork/src_' + '1'.repeat(32) + '/' + '2'.repeat(32)
async function setup(t, initialPath = path) {
  const viewing = Vue.reactive({ ready: true, suspended: false, locallyLocked: false, mode: 'normal', generation: 0, revision: 0 })
  const calls = [], cleanups = new Set(), refreshes = []
  const component = await loadSfc(new URL('../src/components/OnlineArtwork.vue', import.meta.url), {
    '../viewing.js': { viewing, refreshViewing: async () => refreshes.push(true), registerViewingPlaybackCleanup(cleanup) {
      cleanups.add(cleanup); return () => cleanups.delete(cleanup)
    } },
    '../onlineArtwork.js': { onlineArtworkPath: value => typeof value === 'string' && /^\/api\/online\/catalog\/artwork\/src_[0-9a-f]{32}\/[0-9a-f]{32}$/.exec(value)?.[0] === value ? value : '',
      onlineArtworkQueue: { enqueue(value, options) {
        const pending = deferred()
        const call = { value, options, pending, cancelled: false, priorities: [] }
        calls.push(call)
        return { promise: pending.promise, cancel() { call.cancelled = true }, setPriority(priority) { call.priorities.push(priority) } }
      } },
    },
  })
  const props = Vue.reactive({ title: '合成海报', path: initialPath, retryKey: 0 })
  const ui = renderHarness(component, props)
  let mounted = true
  const unmount = () => { if (mounted) { mounted = false; ui.app.unmount() } }
  t.after(unmount)
  return { ui, calls, props, viewing, cleanups, refreshes, unmount }
}

test('online posters use only queued opaque leases and distinguish loading, decode failure and missing art', async t => {
  const { ui, calls, props } = await setup(t, 'https://untrusted.invalid/poster.jpg')
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  assert.equal(calls.length, 0)
  assert.equal(ui.text().includes('暂无海报'), true)
  props.path = path
  await flush()
  assert.equal(calls[0].value, path)
  assert.equal(ui.text().includes('正在加载海报'), true)
  calls[0].pending.resolve({ url: 'blob:fixture-artwork' })
  await flush()
  assert.equal(ui.find(node => node.type === 'img').props.src, 'blob:fixture-artwork')
  ui.find(node => node.type === 'img').props.onError()
  await flush()
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  assert.equal(calls[0].cancelled, true)
  assert.equal(ui.text().includes('合成海报'), true)
  assert.equal(ui.text().includes('海报暂时无法加载'), true)
  assert.equal(calls.length, 1)
})

test('offscreen posters wait for visibility and abort a scrolling-away request before accepting its late bytes', async t => {
  let observed, disconnected = false
  const original = globalThis.IntersectionObserver
  globalThis.IntersectionObserver = class {
    constructor(callback) { observed = callback }
    observe() {}
    disconnect() { disconnected = true }
  }
  t.after(() => { if (original === undefined) delete globalThis.IntersectionObserver; else globalThis.IntersectionObserver = original })
  const { ui, calls, unmount } = await setup(t)
  assert.equal(calls.length, 0)
  observed([{ isIntersecting: true, boundingClientRect: { top: 80 } }])
  await flush()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].options.priority, 80)
  observed([{ isIntersecting: false }])
  calls[0].pending.resolve({ url: 'blob:stale-offscreen' })
  await flush()
  assert.equal(calls[0].cancelled, true)
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  observed([{ isIntersecting: true, boundingClientRect: { top: 0 } }])
  await flush()
  assert.equal(calls.length, 2)
  calls[1].pending.resolve({ url: 'blob:visible' })
  await flush()
  assert.equal(ui.find(node => node.type === 'img').props.src, 'blob:visible')
  unmount()
  assert.equal(disconnected, true)
  assert.equal(calls[1].cancelled, true)
})

test('scope changes, explicit cleanup and unmount revoke current images and ignore previous completions', async t => {
  const { ui, calls, viewing, cleanups } = await setup(t)
  calls[0].pending.resolve({ url: 'blob:private-old' })
  await flush()
  assert.equal(ui.find(node => node.type === 'img').props.src, 'blob:private-old')
  for (const cleanup of cleanups) cleanup()
  await flush()
  assert.equal(calls[0].cancelled, true)
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  assert.equal(calls.length, 1)
  viewing.generation++
  await flush()
  assert.equal(calls.length, 2)
  viewing.locallyLocked = true
  calls[1].pending.resolve({ url: 'blob:late-locked' })
  await flush()
  assert.equal(calls[1].cancelled, true)
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  assert.equal(calls.length, 2)
})

test('only transient failures retry once after a delay, never terminal or authorization failures', async t => {
  const timers = []
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => { timers.push({ callback, delay }); return timers.length })
  const { ui, calls, props, refreshes } = await setup(t)
  calls[0].pending.reject(Object.assign(new Error('fixed failure'), { retryable: true, status: 503 }))
  await flush()
  assert.equal(calls.length, 1)
  assert.equal(timers.length, 1)
  assert.equal(timers[0].delay, 400)
  timers[0].callback()
  await flush()
  assert.equal(calls.length, 2)
  calls[1].pending.reject(Object.assign(new Error('fixed failure'), { retryable: true, status: 503 }))
  await flush()
  assert.equal(timers.length, 1)
  assert.equal(ui.text().includes('海报暂时无法加载'), true)
  props.path = path.replace(/2{32}$/, '3'.repeat(32))
  await flush()
  calls[2].pending.reject(Object.assign(new Error('fixed failure'), { retryable: false, status: 401 }))
  await flush()
  assert.equal(timers.length, 1)
  assert.equal(refreshes.length, 1)
})

test('eviction clears a poster without a visible reload loop', async t => {
  const { ui, calls } = await setup(t)
  calls[0].pending.resolve({ url: 'blob:bounded' })
  await flush()
  calls[0].options.onEvict()
  await flush()
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  assert.equal(calls.length, 1)
  assert.equal(ui.text().includes('等待加载海报'), true)
})

test('manual refresh preserves successful image nodes and does not cancel an ongoing download', async t => {
  const { ui, calls, props } = await setup(t)
  props.retryKey++
  await flush()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].cancelled, false)
  calls[0].pending.resolve({ url: 'blob:keep-poster' })
  await flush()
  const image = ui.find(node => node.type === 'img')
  image.props.onLoad({ target: { src: 'blob:keep-poster' } })
  props.title = '已更新至第2集'
  props.retryKey++
  await flush()
  assert.equal(ui.find(node => node.type === 'img'), image)
  assert.equal(image.props.src, 'blob:keep-poster')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].cancelled, false)
})

test('manual refresh retries a failed poster but never clears an authorization pause', async t => {
  const { ui, calls, props } = await setup(t)
  calls[0].pending.reject(Object.assign(new Error('fixed decode failure'), { retryable: false, status: 502 }))
  await flush()
  assert.equal(ui.text().includes('海报暂时无法加载'), true)
  props.retryKey++
  await flush()
  assert.equal(calls.length, 2)
  calls[1].pending.reject(Object.assign(new Error('fixed unauthorized'), { retryable: false, status: 403 }))
  await flush()
  props.retryKey++
  await flush()
  assert.equal(calls.length, 2)
  assert.equal(ui.text().includes('海报暂时无法加载'), true)
})

test('manual refresh leaves waiting offscreen posters untouched until they become visible', async t => {
  let observed
  const original = globalThis.IntersectionObserver
  globalThis.IntersectionObserver = class {
    constructor(callback) { observed = callback }
    observe() {}
    disconnect() {}
  }
  t.after(() => { if (original === undefined) delete globalThis.IntersectionObserver; else globalThis.IntersectionObserver = original })
  const { calls, props } = await setup(t)
  props.retryKey++
  await flush()
  assert.equal(calls.length, 0)
  observed([{ isIntersecting: true, boundingClientRect: { top: 20 } }])
  await flush()
  assert.equal(calls.length, 1)
})
