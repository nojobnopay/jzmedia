// 归档整理展示辅助纯函数测试：目录徽标（含文件数）与明细行截断。
import test from 'node:test'
import assert from 'node:assert/strict'
import { dirBadge, dirFileCount, dirFileLines } from '../src/organizePlans.js'

test('dirBadge 显示目录内影片文件数', () => {
  assert.equal(dirBadge({ kind: 'dir', files: [{}] }), '目录 · 1 片')
  assert.equal(dirBadge({ kind: 'dir', files: [{}, {}, {}] }), '目录 · 3 片')
  assert.equal(dirBadge({ kind: 'dir', files: [] }), '目录')
  assert.equal(dirBadge({}), '目录')
  assert.equal(dirFileCount(null), 0)
})

test('dirFileLines 转 basename 并保留完整路径', () => {
  const p = {
    kind: 'dir',
    files: [
      { from: '周星驰.Stephen Chow/功夫.Kung.Fu.Hustle.2004/Old.mkv',
        to: '周星驰.Stephen Chow/功夫 (2004)/功夫 (2004).mkv' },
    ],
  }
  const r = dirFileLines(p)
  assert.equal(r.lines.length, 1)
  assert.equal(r.lines[0].nameFrom, 'Old.mkv')
  assert.equal(r.lines[0].nameTo, '功夫 (2004).mkv')
  assert.equal(r.lines[0].from, p.files[0].from)
  assert.equal(r.more, 0)
})

test('dirFileLines 超过上限只报剩余数量', () => {
  const files = Array.from({ length: 5 }, (_, i) => ({
    from: `d/f${i}.mkv`, to: `n/F${i}.mkv`,
  }))
  const r = dirFileLines({ kind: 'dir', files }, 3)
  assert.equal(r.lines.length, 3)
  assert.equal(r.lines[2].nameTo, 'F2.mkv')
  assert.equal(r.more, 2)
  assert.deepEqual(dirFileLines({ kind: 'dir' }), { lines: [], more: 0 })
})
