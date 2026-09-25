import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ACTION_HELP, ACTION_LABELS, actionTotals, basename, dirname, planTotal, groupText,
  groupSamples, untouchedText, manualText, planActionChips, noteText, splitPlans,
  defaultChecked, showFlags, riskShowIds, dirTotalsText,
} from '../src/tvOrganizePlans.js'

test('basename/dirname', () => {
  assert.equal(basename('a/b/c.mkv'), 'c.mkv')
  assert.equal(basename('c.mkv'), 'c.mkv')
  assert.equal(dirname('a/b/c.mkv'), 'a/b')
  assert.equal(dirname('c.mkv'), '')
})

test('planTotal sums counts', () => {
  assert.equal(planTotal({ counts: { root: 1, rename: 12, extras: 2 } }), 15)
  assert.equal(planTotal({}), 0)
})

test('groupText for dir and file groups', () => {
  assert.equal(groupText({ action: 'root', dir: true, from: 'A/Breaking.Bad.2008', to: 'A/绝命毒师 (2008)' }),
    '剧根改名：Breaking.Bad.2008/ → 绝命毒师 (2008)/')
  assert.equal(groupText({ action: 'rename', count: 12, to: 'Show/Season 01' }),
    '正片统一命名：12 项 → Season 01/')
  // 正片与附属文件分开（避免"正片 3 / 文件 12"两个口径）
  assert.equal(groupText({ action: 'rename', episodes: 3, files: 9, count: 12, to: 'Show/Season 01' }),
    '正片统一命名：3 个正片 + 9 个附属 → Season 01/')
})

test('groupSamples caps at max and reports more', () => {
  const g = {
    count: 5,
    samples: [
      { from: 'a/01.mp4', to: 'b/剧-S01E01-一.mp4' },
      { from: 'a/02.mp4', to: 'b/剧-S01E02-二.mp4' },
      { from: 'a/03.mp4', to: 'b/剧-S01E03-三.mp4' },
      { from: 'a/04.mp4', to: 'b/剧-S01E04-四.mp4' },
    ],
  }
  const { lines, more } = groupSamples(g)
  assert.equal(lines.length, 3)
  assert.equal(lines[0].from, '01.mp4')
  assert.equal(lines[0].to, '剧-S01E01-一.mp4')
  assert.equal(lines[0].title, 'a/01.mp4 → b/剧-S01E01-一.mp4')
  assert.equal(more, 2)
})

test('untouchedText/manualText', () => {
  assert.equal(untouchedText({ dir: 'Show/Featurettes/Season 3', count: 13 }),
    'Season 3/：13 个（层级过深，需手动整理）')
  assert.equal(manualText({ file: 'Show/01.mp4', reason: 'needs_review' }),
    '01.mp4：待确认集号')
  assert.equal(manualText({ file: 'Show', reason: 'absolute' }),
    'Show：绝对集号风险（Plex 季集拆分可能不同）')
  assert.equal(manualText({ file: 'Show/01.mp4', reason: 'bad_episode' }),
    '01.mp4：集号异常（无法生成规范名）')
})

test('ACTION_HELP covers all actions with human help', () => {
  assert.deepEqual(Object.keys(ACTION_HELP),
    ['root', 'seasondir', 'wrapper', 'season', 'specials', 'extras', 'rename'])
  for (const [k, v] of Object.entries(ACTION_HELP)) {
    assert.ok(v.label && v.desc && v.example, k)
  }
  assert.equal(ACTION_LABELS.rename, '正片统一命名')
})

test('planActionChips aggregates groups by action (same source as details)', () => {
  const plan = { groups: [
    { action: 'root', dir: true, from: 'a', to: 'b' },
    { action: 'season', episodes: 2, files: 1, count: 3, to: 'Show/Season 01' },
    { action: 'rename', episodes: 3, files: 9, count: 12, to: 'Show/Season 01' },
  ] }
  assert.deepEqual(planActionChips(plan),
    ['剧根改名 1', '补 Season 目录 2（+1 附属）', '正片统一命名 3（+9 附属）'])
  assert.deepEqual(planActionChips(null), [])
})

test('splitPlans splits action vs note-only', () => {
  const a = { show_id: 1, counts: { rename: 2 } }
  const n1 = { show_id: 2, counts: {}, kept_count: 1 }
  const n2 = { show_id: 3, counts: {}, untouched_count: 5 }
  const { actionPlans, notePlans } = splitPlans([a, n1, n2])
  assert.deepEqual(actionPlans.map(p => p.show_id), [1])
  assert.deepEqual(notePlans.map(p => p.show_id), [2, 3])
  assert.deepEqual(splitPlans(null), { actionPlans: [], notePlans: [] })
})

test('noteText explains why a show is listed', () => {
  assert.equal(noteText({ kept_count: 1 }), '保持原名 1 项（本地集）')
  assert.equal(noteText({ untouched_count: 245 }), '深层花絮 245 个未整理')
  assert.equal(noteText({ manual: [{}], manual_more: 2, conflicts: [{}] }),
    '需手动 3 项；冲突 1 项')
  assert.equal(noteText({}), '无需操作')
})

test('actionTotals sums shows/episodes/files and note counts', () => {
  const t = actionTotals([
    { groups: [{ action: 'rename', episodes: 3, files: 9, count: 12, to: 'd' }],
      manual: [], conflicts: [], kept_count: 0, untouched_count: 0 },
    { groups: [], kept_count: 1, untouched_count: 245, conflicts: [{}] },
  ])
  assert.equal(t.shows, 2)
  assert.equal(t.episodes, 3)
  assert.equal(t.files, 9)
  assert.equal(t.kept, 1)
  assert.equal(t.untouched, 245)
  assert.equal(t.conflicts, 1)
})

test('defaultChecked skips blocked/absolute_risk shows', () => {
  assert.equal(defaultChecked({}), true)
  assert.equal(defaultChecked({ blocked: true }), false)
  assert.equal(defaultChecked({ absolute_risk: true }), false)
  // 已确认绝对风险：风险剧参与默认勾选（做种阻断仍不勾）
  assert.equal(defaultChecked({ absolute_risk: true }, { allowAbs: true }), true)
  assert.equal(defaultChecked({ absolute_risk: true, blocked: true }, { allowAbs: true }), false)
})

test('riskShowIds lists absolute-risk shows', () => {
  assert.deepEqual(riskShowIds([{ show_id: 1 }, { show_id: 2, absolute_risk: true }]), [2])
  assert.deepEqual(riskShowIds(null), [])
})

test('dirTotalsText shows final per-dir counts (incl. in-place)', () => {
  const plan = {
    dir_totals: [
      { dir: 'Show (1992)/Season 01', count: 480 },
      { dir: 'Show (1992)/Season 02', count: 873 },
    ],
  }
  assert.equal(dirTotalsText(plan), 'Season 01 480 · Season 02 873')
  assert.equal(dirTotalsText({}), '')
  const many = { dir_totals: Array.from({ length: 10 }, (_, i) => ({ dir: `S${i}`, count: i + 1 })) }
  assert.ok(dirTotalsText(many, 8).includes('…共 10 个目录'))
})

test('showFlags', () => {
  assert.deepEqual(showFlags({ blocked: true, absolute_risk: true, manual: [{}] }),
    ['做种阻断', '绝对集号风险', '需手动 1'])
  // manual 列表被后端截断（_MANUAL_CAP）时，徽标必须计入 manual_more
  assert.deepEqual(showFlags({ manual: [{}, {}], manual_more: 21 }), ['需手动 23'])
  assert.deepEqual(showFlags({ manual_more: 2 }), ['需手动 2'])
  assert.deepEqual(showFlags({}), [])
})
