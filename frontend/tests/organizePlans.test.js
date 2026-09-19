// 归档整理展示辅助纯函数测试：目录徽标（含文件数）与明细行截断。
import test from 'node:test'
import assert from 'node:assert/strict'
import { dirBadge, dirFileCount, dirFileLines, projectPlan } from '../src/organizePlans.js'

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

test('projectPlan 强制搬到顶层：目录计划换前缀（含 files 明细）', () => {
  const p = {
    kind: 'dir',
    to: '周星驰.Stephen Chow/功夫 (2004)',
    files: [{ id: 1, from: '周星驰.Stephen Chow/功夫.Kung.Fu.Hustle.2004/Old.mkv',
              to: '周星驰.Stephen Chow/功夫 (2004)/功夫 (2004).mkv' }],
  }
  const r = projectPlan(p, { action: 'relocate', orgMode: 'inplace', toDir: '电影' })
  assert.equal(r.forcedRelocate, true)
  assert.equal(r.skipped, false)
  assert.equal(r.plan.to, '电影/功夫 (2004)')
  assert.equal(r.plan.files[0].to, '电影/功夫 (2004)/功夫 (2004).mkv')
  assert.equal(p.to, '周星驰.Stephen Chow/功夫 (2004)')   // 不改原对象
})

test('projectPlan 强制搬到顶层：逐文件计划', () => {
  const p = { to: '待整理/Hint Movie (2018)/Hint Movie (2018).mkv' }
  const r = projectPlan(p, { action: 'relocate', orgMode: 'inplace', toDir: '电影' })
  assert.equal(r.plan.to, '电影/Hint Movie (2018)/Hint Movie (2018).mkv')
})

test('projectPlan 根级片目录与空/带斜杠目标目录', () => {
  const p = { kind: 'dir', to: '十二猴子 (1995)', files: [] }
  assert.equal(projectPlan(p, { action: 'relocate', orgMode: 'inplace', toDir: '  ' }).plan.to,
               '电影/十二猴子 (1995)')
  assert.equal(projectPlan(p, { action: 'relocate', orgMode: 'inplace', toDir: '动画/' }).plan.to,
               '动画/十二猴子 (1995)')
})

test('projectPlan 跟随上方或全局已是顶层时不二次投影', () => {
  const dir = { kind: 'dir', to: '电影/功夫 (2004)', files: [] }
  assert.equal(projectPlan(dir, { action: 'auto', orgMode: 'inplace' }).plan, dir)
  assert.equal(projectPlan(dir, { action: 'auto', orgMode: 'relocate' }).plan, dir)
  assert.equal(projectPlan(dir, { action: 'relocate', orgMode: 'relocate' }).plan, dir)
})

test('projectPlan 保持不动：标记且路径不变', () => {
  const p = { kind: 'dir', to: '十二猴子 (1995)', files: [] }
  const r = projectPlan(p, { action: 'skip', orgMode: 'inplace', toDir: '电影' })
  assert.equal(r.skipped, true)
  assert.equal(r.forcedRelocate, false)
  assert.equal(r.plan, p)
})
