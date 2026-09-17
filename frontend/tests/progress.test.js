import test from 'node:test'
import assert from 'node:assert/strict'
import { pickProgressPosition } from '../src/progress.js'

test('pickProgressPosition：seek 重开中存目标位置（含拖到 0:00）', () => {
  // 用户 2026-09 bug：从 30:28 拖到 14:30 立即关闭，暂停回调/卸载存档曾写回旧 absPos
  assert.equal(pickProgressPosition({
    seekPending: true, seekPreview: 870, absPos: 1828, currentTime: 3
  }), 870)
  assert.equal(pickProgressPosition({
    seekPending: true, seekPreview: 0, absPos: 1828, currentTime: 3
  }), 0)
})

test('pickProgressPosition：正常播放取 absPos（HLS 会话偏移已含在 absPos）', () => {
  assert.equal(pickProgressPosition({
    seekPending: false, seekPreview: 0, absPos: 930, currentTime: 60
  }), 930)
})

test('pickProgressPosition：未起播/无有效时间不存档（防 0:00 覆盖断点）', () => {
  assert.equal(pickProgressPosition({
    seekPending: false, seekPreview: 0, absPos: 0, currentTime: 0
  }), null)
  assert.equal(pickProgressPosition({
    seekPending: false, seekPreview: 0, absPos: 0, currentTime: NaN
  }), null)
  assert.equal(pickProgressPosition({
    seekPending: false, seekPreview: 0, absPos: 0, currentTime: -1
  }), null)
})
