import test from 'node:test'
import assert from 'node:assert/strict'
import { fmtBytes, fmtDate, fmtRemaining, midEllipsis } from '../src/format.js'

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

test('fmtDate：本地日期、0/非法隐藏', () => {
  const ts = Math.floor(new Date(2026, 8, 12, 10, 30).getTime() / 1000)
  assert.equal(fmtDate(ts), '2026-09-12')
  assert.equal(fmtDate(0), '')
  assert.equal(fmtDate(null), '')
  assert.equal(fmtDate('bogus'), '')
})

test('fmtRemaining：分钟/小时档位与最小文案', () => {
  assert.equal(fmtRemaining(0), '剩 <1 分钟')
  assert.equal(fmtRemaining(30), '剩 <1 分钟')
  assert.equal(fmtRemaining(42 * 60), '剩 42 分钟')
  assert.equal(fmtRemaining(59.6 * 60), '剩 59 分钟')   // 向下取整，不虚报
  assert.equal(fmtRemaining(3600), '剩 1 时 00 分')
  assert.equal(fmtRemaining(3600 + 8 * 60), '剩 1 时 08 分')
  assert.equal(fmtRemaining(-5), '剩 <1 分钟')
})
