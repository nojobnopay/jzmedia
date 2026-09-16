import test from 'node:test'
import assert from 'node:assert/strict'
import { fmtBytes, midEllipsis } from '../src/format.js'

test('fmtBytes：单位档位与非法值', () => {
  assert.equal(fmtBytes(0), '0B')
  assert.equal(fmtBytes(512), '512B')
  assert.equal(fmtBytes(2048), '2.0K')
  assert.equal(fmtBytes(3 * 1024 * 1024), '3.0M')
  assert.equal(fmtBytes(5 * 1024 * 1024 * 1024), '5.00G')
  assert.equal(fmtBytes(null), '0B')
})

test('midEllipsis：短串原样、长串中缩且保留尾部', () => {
  assert.equal(midEllipsis('abc'), 'abc')
  assert.equal(midEllipsis(''), '')
  const s = '待整理/电影/某部电影 (2021)/' + 'x'.repeat(30) + '/Movie.2021.2160p.mkv'
  const out = midEllipsis(s)
  assert.equal(out.length, 48)
  assert.ok(out.includes('...'))
  assert.ok(out.endsWith('Movie.2021.2160p.mkv'.slice(-17)))
  assert.equal(midEllipsis(s, 40).length, 40)
})
