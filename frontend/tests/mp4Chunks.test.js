import test from 'node:test'
import assert from 'node:assert/strict'
import { createMp4Queue, mp4ClipEnd, detachedMediaTimeline, mp4AtEnd } from '../src/mp4Chunks.js'

function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { resolve, promise } }
test('MP4 短片结束映射原片时间，最终片钳到全片时长', () => {
  assert.equal(mp4ClipEnd(1392, 30.008, 4902), 1422)
  assert.equal(mp4ClipEnd(1392, Infinity, 4902), null)
  assert.equal(mp4ClipEnd(1392, 0, 4902), null)
  assert.equal(mp4ClipEnd(4890, 30.008, 4902), 4902)
})
test('识别有画面但没有网页缓冲信息的独立媒体层，不把暂停当接管', () => {
  const v = { readyState: 4, paused: false, ended: false, buffered: { length: 0 } }
  assert.equal(detachedMediaTimeline(v), true)
  assert.equal(detachedMediaTimeline({ ...v, paused: true }), false)
  assert.equal(detachedMediaTimeline({ ...v, readyState: 1 }), false)
  assert.equal(detachedMediaTimeline({ ...v, buffered: { length: 1 } }), false)
})
test('服务器片长判定结束，拒绝旧源在新片段中途的 ended 事件', () => {
  assert.equal(mp4AtEnd({ ended: true, currentTime: 30.1 }, 30.1), true)
  assert.equal(mp4AtEnd({ ended: false, currentTime: 30, seeking: false }, 30.1), true)
  assert.equal(mp4AtEnd({ ended: false, currentTime: 2, seeking: false }, 30.1), false)
  assert.equal(mp4AtEnd({ ended: false, currentTime: 30, seeking: true }, 30.1), false)
})
test('下一片只预取一次，领取后转交 sid 所有权，不在清场时误删当前片', async () => {
  let calls = 0
  const deleted = []
  const q = createMp4Queue({ create: async body => { calls++; return { session_id: 'next', body } },
    close: sid => deleted.push(sid), isCurrent: () => true })
  q.prepare(60, { audio: 1 }, 3)
  q.prepare(60, { audio: 1 }, 3)
  const s = await q.take(60, 3)
  assert.equal(calls, 1)
  assert.equal(s.body.audio, 1)
  assert.equal(s.body.start, 60)
  assert.equal(q.sessionId(), undefined)
  q.clear()
  assert.deepEqual(deleted, [])
})
test('关窗/seek 取消预取；未遵守 abort 的晚响应也删除，禁止转交旧 sid', async () => {
  const pending = deferred(), deleted = []
  let signal, valid = true
  const q = createMp4Queue({ create: (_body, s) => { signal = s; return pending.promise },
    close: sid => deleted.push(sid), isCurrent: () => valid })
  q.prepare(60, {}, 1)
  const result = q.take(60, 1)
  q.clear(); valid = false
  assert.equal(signal.aborted, true)
  pending.resolve({ session_id: 'late' })
  assert.equal(await result, null)
  assert.deepEqual(deleted, ['late'])
})
test('已就绪预取被替换时删除旧 sid，代际变化也丢弃结果', async () => {
  const deleted = []
  const q = createMp4Queue({ create: async b => ({ session_id: String(b.start) }),
    close: sid => deleted.push(sid), isCurrent: g => g === 1 })
  q.prepare(60, {}, 1)
  await Promise.resolve()
  assert.equal(q.sessionId(), '60')
  q.prepare(90, {}, 2)
  assert.equal(await q.take(90, 2), null)
  assert.deepEqual(deleted, ['60', '90'])
})
