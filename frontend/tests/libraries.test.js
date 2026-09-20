import test from 'node:test'
import assert from 'node:assert/strict'

import {
  applyLibs, currentLibId, currentMediaId, currentMediaVideoLibs,
  listLibs, listMediaLibs, mediaParam, onLibChange, pickMedia,
  preferredVideoLibId, resetLibState, switchLib, switchMedia,
} from '../src/libraries.js'

const DATA = {
  items: [
    { id: 1, name: '电影', kind: 'movie', media_library_id: 10, media_name: 'NAS', enabled: true, media_enabled: true },
    { id: 2, name: 'TV Shows', kind: 'tv', media_library_id: 10, media_name: 'NAS', enabled: true, media_enabled: true },
    { id: 3, name: 'Unrated', kind: 'movie', media_library_id: 10, media_name: 'NAS', enabled: true, media_enabled: true },
    { id: 4, name: '电影', kind: 'movie', media_library_id: 20, media_name: 'sample_media', enabled: true, media_enabled: true },
  ],
  default_id: 4,
}

test('pickMedia：存储值优先，其次回退媒体库，最后第一个启用库', () => {
  const medias = [
    { id: 10, name: 'NAS', enabled: true },
    { id: 20, name: 'sample_media', enabled: true },
  ]
  assert.equal(pickMedia(medias, 20, 10).id, 20)
  assert.equal(pickMedia(medias, 99, 10).id, 10)
  assert.equal(pickMedia(medias, null, null).id, 10)
  assert.equal(pickMedia([{ id: 5, enabled: false }, { id: 6, enabled: true }], null, null).id, 6)
  assert.equal(pickMedia([], 1, 1), null)
})

test('applyLibs：当前媒体库 = 默认视频库所属媒体库；多媒体库时 mediaParam 带参', () => {
  resetLibState()
  const cur = applyLibs(DATA)
  assert.equal(cur.id, 20)
  assert.equal(currentMediaId(), 20)
  assert.equal(mediaParam(), 20)
  assert.equal(listMediaLibs().length, 2)
  assert.equal(listLibs().length, 4)
  // 单媒体库 → URL 干净
  resetLibState()
  applyLibs({ items: DATA.items.slice(0, 3), default_id: 1 })
  assert.equal(currentMediaId(), 10)
  assert.equal(mediaParam(), null)
})

test('switchMedia：切换媒体库并通知订阅者；currentLibId 为工具页首选视频库', () => {
  resetLibState()
  applyLibs(DATA)
  let seen = null
  const off = onLibChange((m) => { seen = m.id })
  switchMedia(10)
  assert.equal(currentMediaId(), 10)
  assert.equal(seen, 10)
  assert.equal(currentLibId(), 1)                 // 首选（非默认库）→ 第一个视频库
  assert.equal(preferredVideoLibId('tv'), 2)
  off()
  switchMedia(20)
  assert.equal(seen, 10)                          // 已退订
})

test('currentMediaVideoLibs / preferredVideoLibId：按类型过滤，无匹配退回全部', () => {
  resetLibState()
  applyLibs(DATA)
  switchMedia(10)
  assert.deepEqual(currentMediaVideoLibs('movie').map((l) => l.id), [1, 3])
  assert.deepEqual(currentMediaVideoLibs('tv').map((l) => l.id), [2])
  assert.deepEqual(currentMediaVideoLibs().map((l) => l.id), [1, 2, 3])
  assert.equal(preferredVideoLibId('movie'), 1)
  assert.equal(preferredVideoLibId('tv'), 2)
  switchMedia(20)
  assert.equal(preferredVideoLibId('tv'), 4)      // 无剧集库 → 退回全部里的首个
})

test('switchLib：旧视频库 id 映射到所属媒体库', () => {
  resetLibState()
  applyLibs(DATA)
  switchMedia(20)
  switchLib(2)                                    // NAS / TV Shows
  assert.equal(currentMediaId(), 10)
  assert.equal(preferredVideoLibId('tv'), 2)
  assert.equal(switchLib(999), null)
})
