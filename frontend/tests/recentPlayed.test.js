import test from 'node:test'
import assert from 'node:assert/strict'
import { RECENT_ALL_KEY, canScroll, loadRecentAll, progressWidth,
         saveRecentAll, scrollStep } from '../src/recentPlayed.js'

function fakeStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
  }
}

test('progressWidth：百分比钳制与可见下限', () => {
  assert.equal(progressWidth({ percent: 0.5 }), '50.0%')
  assert.equal(progressWidth({ percent: 1 }), '100.0%')
  assert.equal(progressWidth({ percent: 1.5 }), '100.0%')
  assert.equal(progressWidth({ percent: 0.001 }), '2.0%')    // 极小进度保底可见
  assert.equal(progressWidth({ percent: 0 }), '2%')
  assert.equal(progressWidth(null), '2%')
  assert.equal(progressWidth({}), '2%')
  assert.equal(progressWidth({ percent: 0.5 }, 4), '50.0%')
})

test('recentAll：localStorage 记忆', () => {
  const st = fakeStorage()
  assert.equal(loadRecentAll(st), false)
  saveRecentAll(st, true)
  assert.equal(st.getItem(RECENT_ALL_KEY), '1')
  assert.equal(loadRecentAll(st), true)
  saveRecentAll(st, false)
  assert.equal(loadRecentAll(st), false)
})

test('canScroll：两端与容差', () => {
  assert.deepEqual(canScroll({ scrollLeft: 0, clientWidth: 300, scrollWidth: 900 }),
    { left: false, right: true })
  assert.deepEqual(canScroll({ scrollLeft: 600, clientWidth: 300, scrollWidth: 900 }),
    { left: true, right: false })
  assert.deepEqual(canScroll({ scrollLeft: 300, clientWidth: 300, scrollWidth: 900 }),
    { left: true, right: true })
  assert.deepEqual(canScroll({ scrollLeft: 1, clientWidth: 300, scrollWidth: 300 }),
    { left: false, right: false })          // 亚像素容差
  assert.deepEqual(canScroll(), { left: false, right: false })
})

test('scrollStep：一屏 80% 且不小于一张卡', () => {
  assert.equal(scrollStep(1000), 800)
  assert.equal(scrollStep(100), 160)
  assert.equal(scrollStep(0), 160)
  assert.equal(scrollStep(null), 160)
})
