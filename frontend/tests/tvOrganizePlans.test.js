import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  basename, dirname, planTotal, groupText, groupSamples, untouchedText,
  manualText, showTotalText, defaultChecked, showFlags,
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
  assert.equal(more, 2)
})

test('untouchedText/manualText', () => {
  assert.equal(untouchedText({ dir: 'Show/Featurettes/Season 3', count: 13 }),
    'Season 3/：13 个（层级过深，需手动整理）')
  assert.equal(manualText({ file: 'Show/01.mp4', reason: 'needs_review' }),
    '01.mp4：待确认集号')
  assert.equal(manualText({ file: 'Show', reason: 'absolute' }),
    'Show：绝对集号风险（Plex 季集拆分可能不同）')
})

test('showTotalText lists non-zero actions in canonical order', () => {
  const text = showTotalText({ counts: { rename: 24, root: 1, extras: 0 } })
  assert.equal(text, '剧根改名 1，正片统一命名 24')
})

test('defaultChecked skips blocked/absolute_risk shows', () => {
  assert.equal(defaultChecked({}), true)
  assert.equal(defaultChecked({ blocked: true }), false)
  assert.equal(defaultChecked({ absolute_risk: true }), false)
})

test('showFlags', () => {
  assert.deepEqual(showFlags({ blocked: true, absolute_risk: true, manual: [{}] }),
    ['做种阻断', '绝对集号风险', '需手动 1'])
  assert.deepEqual(showFlags({}), [])
})
