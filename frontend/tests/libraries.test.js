import test from 'node:test'
import assert from 'node:assert/strict'

import {
  pickLibrary, applyLibs, currentLibId, libParam, withLib,
  switchLib, listLibs, resetLibState, onLibChange,
} from '../src/libraries.js'

const DATA = {
  items: [
    { id: 1, name: '默认库', enabled: true },
    { id: 2, name: 'NAS', enabled: true },
  ],
  default_id: 1,
}

test('pickLibrary：存储值优先，其次默认库，最后第一个启用库', () => {
  assert.equal(pickLibrary(DATA.items, 2, 1).id, 2)
  assert.equal(pickLibrary(DATA.items, 99, 1).id, 1)
  assert.equal(pickLibrary(DATA.items, null, 2).id, 2)
  assert.equal(pickLibrary([{ id: 5, enabled: false }, { id: 6, enabled: true }], null, null).id, 6)
  assert.equal(pickLibrary([], 1, 1), null)
})

test('applyLibs：当前库 = 存储/默认；单库时 libParam 为空（URL 干净）', () => {
  resetLibState()
  const cur = applyLibs({ items: [DATA.items[0]], default_id: 1 })
  assert.equal(cur.id, 1)
  assert.equal(libParam(), null)
  assert.deepEqual(withLib({ q: 'x' }), { q: 'x' })
})

test('switchLib：切换后 libParam/withLib 带 library，且通知订阅者', () => {
  resetLibState()
  applyLibs(DATA)
  let seen = null
  const off = onLibChange((lib) => { seen = lib.id })
  assert.equal(currentLibId(), 1)
  switchLib(2)
  assert.equal(currentLibId(), 2)
  assert.equal(seen, 2)
  assert.equal(libParam(), 2)
  assert.deepEqual(withLib({ q: 'x' }), { q: 'x', library: 2 })
  off()
  switchLib(1)
  assert.equal(seen, 2)   // 已退订
  assert.equal(listLibs().length, 2)
})
