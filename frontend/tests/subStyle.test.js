import test from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeSubStyle, subFontPx, pickSubAnchor, subBarPad, subInnerPad,
} from '../src/subStyle.js'

test('normalizeSubStyle defaults and clamps', () => {
  assert.deepEqual(normalizeSubStyle(null), { bg: 0, outline: 1, pos: 'auto', size: 2 })
  assert.deepEqual(normalizeSubStyle({ bg: 9, outline: -1, pos: 'x', size: 5 }),
    { bg: 0, outline: 1, pos: 'auto', size: 2 })
  assert.deepEqual(normalizeSubStyle({ bg: 2, outline: 0, pos: 'inside', size: 3 }),
    { bg: 2, outline: 0, pos: 'inside', size: 3 })
})

test('subFontPx scales with picture height and clamps', () => {
  assert.equal(subFontPx(1000, 2), 46) // 0.048*1000=48 -> max 46
  assert.equal(subFontPx(1000, 1), 40)
  assert.equal(subFontPx(200, 1), 15) // 8 -> min
  assert.equal(subFontPx(0, 2), 15)
})

test('pickSubAnchor auto favors bottom bar when tall enough', () => {
  assert.equal(pickSubAnchor(60, 30, 'auto'), 'outside')
  assert.equal(pickSubAnchor(20, 30, 'auto'), 'inside')
  assert.equal(pickSubAnchor(60, 30, 'inside'), 'inside')
  assert.equal(pickSubAnchor(20, 30, 'outside'), 'outside')
})

test('paddings stay in range', () => {
  assert.equal(subBarPad(0), 3)
  assert.equal(subBarPad(1000), 16)
  assert.equal(subInnerPad(0), 6)
  assert.equal(subInnerPad(1000), 42)
})
