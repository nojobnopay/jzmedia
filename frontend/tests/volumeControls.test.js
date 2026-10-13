import test from 'node:test'
import assert from 'node:assert/strict'
import { createVolumePopoverState, normalizeVolume, volumeForKey, supportsMediaVolume } from '../src/volumeControls.js'

test('鼠标经过或键盘聚焦后首次激活固定浮层，第二次激活关闭', () => {
  const state = createVolumePopoverState()
  assert.deepEqual(state.openTemporarily(false), { event: 'open', source: 'hover' })
  assert.deepEqual(state.click(true), { event: 'open', source: 'activate' })
  // Pointer/focus notifications after pinning must not turn the next click
  // back into a first activation.
  assert.deepEqual(state.openTemporarily(true, 'focus'), { event: 'open', source: 'focus' })
  assert.deepEqual(state.click(true), { event: 'toggle' })
})

test('临时展开尚未收到父级更新时首次点击仍保持，外部关闭清除固定状态', () => {
  const state = createVolumePopoverState()
  state.openTemporarily(false)
  assert.deepEqual(state.click(false), { event: 'open', source: 'activate' })
  state.close()
  state.openTemporarily(false)
  assert.deepEqual(state.click(true), { event: 'open', source: 'activate' })
  state.close()
  // A touch click has no preceding hover/focus opening.
  assert.deepEqual(state.click(false), { event: 'toggle' })
  assert.deepEqual(state.click(true), { event: 'toggle' })
})

test('父级保留其他菜单而拒绝悬停时，显式点击仍携带可切换菜单的来源', () => {
  const state = createVolumePopoverState()
  assert.deepEqual(state.openTemporarily(false, 'hover'), { event: 'open', source: 'hover' })
  // The parent's open prop remains false when an existing menu owns focus.
  // Activation must remain distinguishable from another passive hover request.
  assert.deepEqual(state.click(false), { event: 'open', source: 'activate' })
  assert.deepEqual(state.click(true), { event: 'toggle' })
})

test('悬停临时浮层离开后收起且不恢复焦点，避免重新打开', () => {
  const state = createVolumePopoverState()
  state.openTemporarily(false, 'hover')
  assert.deepEqual(state.releaseTemporary(), { event: 'close', options: { restoreFocus: false } })
  assert.equal(state.releaseTemporary(), null)
  // A later hover still opens normally, without carrying a pinned activation.
  state.openTemporarily(false, 'hover')
  assert.deepEqual(state.click(true), { event: 'open', source: 'activate' })
  assert.equal(state.releaseTemporary(), null)
})

test('键盘焦点、滑条拖动和点击固定会阻止离开关闭，触屏不依赖悬停', () => {
  const state = createVolumePopoverState()
  state.openTemporarily(false, 'focus')
  assert.equal(state.releaseTemporary({ focused: true }), null)
  assert.equal(state.releaseTemporary({ dragging: true }), null)
  assert.deepEqual(state.releaseTemporary(), { event: 'close', options: { restoreFocus: false } })
  state.close()
  assert.deepEqual(state.click(false), { event: 'toggle' })
  assert.equal(state.releaseTemporary(), null)
  state.openTemporarily(true, 'hover')
  assert.equal(state.releaseTemporary(), null)
  assert.deepEqual(state.click(true), { event: 'toggle' })
})

test('竖向音量键盘上增下减、静音起调和两端不越界', () => {
  assert.equal(volumeForKey('ArrowUp', 0), 0.05)
  assert.equal(volumeForKey('ArrowDown', 0.5), 0.45)
  assert.equal(volumeForKey('ArrowUp', 0.99), 1)
  assert.equal(volumeForKey('ArrowDown', 0.01), 0)
  assert.equal(volumeForKey('Home', 0.4), 0)
  assert.equal(volumeForKey('End', 0.4), 1)
  assert.equal(volumeForKey('Escape', 0.4), null)
  assert.equal(normalizeVolume(NaN), 1)
})

test('可写音量探测恢复原值，不改变静音与播放状态', () => {
  for (const volume of [0, 0.25, 0.5, 0.75, 1]) {
    const video = { volume, muted: true, paused: true }
    assert.equal(supportsMediaVolume(video), true)
    assert.deepEqual(video, { volume, muted: true, paused: true })
  }
})

test('手机忽略写入、只读或异常媒体元素不提供无效滑条', () => {
  assert.equal(supportsMediaVolume(null), false)
  assert.equal(supportsMediaVolume({ get volume() { return 1 }, set volume(_value) {} }), false)
  assert.equal(supportsMediaVolume({ get volume() { return 1 } }), false)
  assert.equal(supportsMediaVolume({ get volume() { throw new Error('unavailable') } }), false)
  assert.equal(supportsMediaVolume({ get volume() { return 1 }, set volume(_value) { throw new Error('readonly') } }), false)
  assert.equal(supportsMediaVolume({ get volume() { return NaN }, set volume(_value) {} }), false)
})

test('iOS 和桌面模式 iPad 通过系统音量键调节，保留媒体当前音量', () => {
  const video = { volume: 0.75, muted: false }
  assert.equal(supportsMediaVolume(video, { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)' }), false)
  assert.equal(supportsMediaVolume(video, { platform: 'MacIntel', maxTouchPoints: 5 }), false)
  assert.equal(video.volume, 0.75)
  assert.equal(supportsMediaVolume(video, { platform: 'MacIntel', maxTouchPoints: 0 }), true)
})
