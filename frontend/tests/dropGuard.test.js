import test from 'node:test'
import assert from 'node:assert/strict'

import {
  createDropGuard, tickDropGuard, isCopyVideoPath,
  DROP_WINDOW_MS, DROP_TRIGGER_COUNT,
} from '../src/dropGuard.js'

function play (samples) {
  const g = createDropGuard()
  let fires = 0
  let last = null
  for (const s of samples) {
    last = tickDropGuard(g, s.q, { now: s.t, playing: s.playing !== false, startedAt: 0 })
    if (last.fire) fires += 1
  }
  return { fires, last }
}

test('isCopyVideoPath：只有 >1080p 的视频直通路径参与判定', () => {
  assert.equal(isCopyVideoPath('direct', 2160), true)
  assert.equal(isCopyVideoPath('remux', 2160), true)
  assert.equal(isCopyVideoPath('audio_transcode', 2160), true)
  assert.equal(isCopyVideoPath('video_transcode', 2160), false)
  assert.equal(isCopyVideoPath('remux', 1080), false)
  assert.equal(isCopyVideoPath('', 2160), false)
})

test('未满窗不触发（首拍只建基线）', () => {
  const { fires } = play([
    { t: 0, q: { dropped: 0, total: 0 } },
    { t: 10000, q: { dropped: 9, total: 250 } },
    { t: 29000, q: { dropped: 20, total: 725 } },
  ])
  assert.equal(fires, 0)
})

test('满窗且丢帧达到阈值触发一次并带窗口增量', () => {
  const { fires, last } = play([
    { t: 0, q: { dropped: 0, total: 0 } },
    { t: 10000, q: { dropped: 1, total: 250 } },
    { t: DROP_WINDOW_MS + 1000, q: { dropped: DROP_TRIGGER_COUNT, total: 750 } },
  ])
  assert.equal(fires, 1)
  assert.equal(last.drops, DROP_TRIGGER_COUNT)
  assert.equal(last.frames, 750)
})

test('丢帧不足阈值不触发，窗口滚动后可再次判定', () => {
  const { fires } = play([
    { t: 0, q: { dropped: 0, total: 0 } },
    { t: DROP_WINDOW_MS + 1000, q: { dropped: 2, total: 750 } },
    { t: DROP_WINDOW_MS * 2 + 2000, q: { dropped: 4, total: 1500 } },
  ])
  assert.equal(fires, 0)
})

test('暂停时重置窗口（恢复后重新建基线）', () => {
  const { fires } = play([
    { t: 0, q: { dropped: 0, total: 0 } },
    { t: 10000, q: { dropped: 30, total: 250 }, playing: false },
    { t: 20000, q: { dropped: 30, total: 250 } },
    { t: 51000, q: { dropped: 35, total: 1000 } },
  ])
  assert.equal(fires, 1)
})

test('计数器异常（丢帧 > 总数）重置窗口不触发', () => {
  const { fires } = play([
    { t: 0, q: { dropped: 0, total: 0 } },
    { t: 10000, q: { dropped: 9, total: 1 } },
    { t: DROP_WINDOW_MS + 11000, q: { dropped: 9, total: 750 } },
  ])
  assert.equal(fires, 0)
})
