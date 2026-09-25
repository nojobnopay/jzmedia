// 视频库 Tab / 深链解析 / 单步聚焦纯函数测试。
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildTabs, pickTab, kindForSection, stepForSection, sectionNeedsAdvanced,
  resolveFocusTab, pickOpenStep,
} from '../src/libraryToolsTabs.js'

const MEDIA = [
  {
    id: 1, name: 'NAS',
    video_libraries: [
      { id: 11, name: '电影', kind: 'movie' },
      { id: 12, name: '剧集', kind: 'tv' },
      { id: 13, name: '停用库', kind: 'movie', enabled: false, effective_enabled: false },
    ],
  },
  {
    id: 2, name: 'media',
    video_libraries: [{ id: 21, name: '纪录片', kind: 'movie' }],
  },
]

test('buildTabs 每视频库一个 Tab，多媒体库带前缀与类型', () => {
  const tabs = buildTabs(MEDIA)
  assert.deepEqual(tabs.map((t) => t.id), [11, 12, 13, 21])
  assert.equal(tabs[0].label, 'NAS · 电影')
  assert.equal(tabs[0].kind_text, '电影')
  assert.equal(tabs[1].label, 'NAS · 剧集')
  assert.equal(tabs[2].enabled, false)
  assert.equal(tabs[3].label, 'media · 纪录片')
  // 单媒体库不带前缀
  const one = buildTabs([MEDIA[0]])
  assert.equal(one[0].label, '电影')
})

test('pickTab 记忆视频库 → 当前媒体库 → 首个启用', () => {
  const tabs = buildTabs(MEDIA)
  assert.equal(pickTab(tabs, { storedId: 12 }).id, 12)
  assert.equal(pickTab(tabs, { currentMediaId: 2 }).id, 21)
  assert.equal(pickTab(tabs, { storedId: 999, currentMediaId: 999 }).id, 11)
  assert.equal(pickTab([], {}), null)
})

test('深链区块 → 类型 / 步骤 / 更多工具', () => {
  assert.equal(kindForSection('sec-tvorganize'), 'tv')
  assert.equal(kindForSection('sec-pipeline'), 'movie')
  assert.equal(kindForSection('sec-libtools'), null)
  assert.equal(stepForSection('sec-pipeline'), 'scan')
  assert.equal(stepForSection('sec-pending'), 'pending')
  assert.equal(stepForSection('sec-tvorganize'), 'organize')
  assert.equal(stepForSection('sec-restore'), null)
  assert.equal(sectionNeedsAdvanced('sec-restore'), true)
  assert.equal(sectionNeedsAdvanced('sec-files'), true)
  assert.equal(sectionNeedsAdvanced('sec-tvorganize'), false)
})

test('resolveFocusTab：library 优先，media 按区块类型偏好', () => {
  const tabs = buildTabs(MEDIA)
  assert.equal(resolveFocusTab(tabs, { library: 12 }).id, 12)
  assert.equal(resolveFocusTab(tabs, { media: 1, sec: 'sec-pipeline' }).id, 11)
  assert.equal(resolveFocusTab(tabs, { media: 1, sec: 'sec-tvorganize' }).id, 12)
  assert.equal(resolveFocusTab(tabs, { media: 2, sec: 'sec-tvorganize' }).id, 21)
  assert.equal(resolveFocusTab(tabs, { media: 999, sec: 'sec-pipeline' }), null)
  assert.equal(resolveFocusTab(tabs, { library: 999, media: 2 }).id, 21)
})

test('pickOpenStep：优先第一个有待办步骤，否则第一步', () => {
  assert.equal(pickOpenStep([{ key: 'scan', count: 0 }, { key: 'pending', count: 3 }, { key: 'organize', count: 1 }]), 'pending')
  assert.equal(pickOpenStep([{ key: 'scan', count: 0 }, { key: 'pending', count: 0 }, { key: 'organize', count: 2 }]), 'organize')
  assert.equal(pickOpenStep([{ key: 'scan', count: 0 }, { key: 'pending', count: 0 }]), 'scan')
  assert.equal(pickOpenStep([]), null)
})
