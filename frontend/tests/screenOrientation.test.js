import test from 'node:test'
import assert from 'node:assert/strict'
import {
  landscapeLockWanted, lockLandscape, orientationEnv, unlockOrientation,
} from '../src/screenOrientation.js'

function host({ coarse = false, portrait = false, canLock = true, reject = false } = {}) {
  const calls = []
  return {
    calls,
    window: {
      matchMedia: (q) => ({
        matches: q.includes('coarse') ? coarse : (q.includes('portrait') ? portrait : false),
      }),
    },
    screen: canLock ? {
      orientation: {
        lock: (o) => { calls.push(['lock', o]); return reject ? Promise.reject(new Error('denied')) : Promise.resolve() },
        unlock: () => { calls.push(['unlock']) },
      },
    } : {},
  }
}

test('手机竖屏且支持 API 时才锁定横屏', () => {
  assert.equal(landscapeLockWanted({ coarse: true, portrait: true, canLock: true }), true)
  assert.equal(landscapeLockWanted({ coarse: false, portrait: true, canLock: true }), false)
  assert.equal(landscapeLockWanted({ coarse: true, portrait: false, canLock: true }), false)
  assert.equal(landscapeLockWanted({ coarse: true, portrait: true, canLock: false }), false)
  assert.equal(landscapeLockWanted({}), false)
  assert.equal(landscapeLockWanted(null), false)
})

test('环境快照读取媒体查询与 API 可用性', () => {
  assert.deepEqual(orientationEnv(host({ coarse: true, portrait: true })), { coarse: true, portrait: true, canLock: true })
  assert.deepEqual(orientationEnv(host({ canLock: false })), { coarse: false, portrait: false, canLock: false })
  assert.deepEqual(orientationEnv({}), { coarse: false, portrait: false, canLock: false })
})

test('锁定被拒静默，解锁无 API 为空操作', async () => {
  const h = host({ reject: true })
  lockLandscape(h)
  await new Promise((r) => setTimeout(r, 10))
  assert.deepEqual(h.calls, [['lock', 'landscape']])
  unlockOrientation(h)
  assert.deepEqual(h.calls, [['lock', 'landscape'], ['unlock']])
  unlockOrientation({})
})
