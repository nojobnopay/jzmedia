import test from 'node:test'
import assert from 'node:assert/strict'
import { shouldShowCharacter, castId, castCharacter, castAvatarSrc, normalizeCast } from '../src/cast.js'

test('shouldShowCharacter 大小写不敏感（电影/剧集统一口径）', () => {
  assert.equal(shouldShowCharacter('en'), true)
  assert.equal(shouldShowCharacter('EN'), true)
  assert.equal(shouldShowCharacter('en-US'), true)
  assert.equal(shouldShowCharacter('ja'), false)
  assert.equal(shouldShowCharacter(''), false)
  assert.equal(shouldShowCharacter(null), false)
})

test('castId 兼容 tmdb_id/id 双字段', () => {
  assert.equal(castId({ tmdb_id: 123 }), 123)
  assert.equal(castId({ id: 456 }), 456)
  assert.equal(castId({ tmdb_id: 123, id: 456 }), 123)
  assert.equal(castId({}), 0)
  assert.equal(castId(null), 0)
})

test('castCharacter 兼容 character/character_name 双字段', () => {
  assert.equal(castCharacter({ character: 'Neo' }), 'Neo')
  assert.equal(castCharacter({ character_name: 'Trinity' }), 'Trinity')
  assert.equal(castCharacter({}), '')
})

test('castAvatarSrc 优先级：TMDB profile_path > 本地 avatar > 占位', () => {
  const tmdb = castAvatarSrc({ profile_path: '/abc123.jpg', avatar: 'person_1.jpg' })
  assert.ok(tmdb.includes('/api/tv/cast-avatar'))
  const local = castAvatarSrc({ avatar: 'person_1.jpg' })
  assert.equal(local, '/posters/person_1.jpg')
  assert.equal(castAvatarSrc({ avatar: '-' }), '')
  assert.equal(castAvatarSrc({}), '')
})

test('normalizeCast 归一 + 客串透传', () => {
  const p = normalizeCast({ id: 7, name: 'A', character: 'B', profile_path: '/x.jpg', guest: true })
  assert.equal(p.id, 7)
  assert.equal(p.name, 'A')
  assert.equal(p.character, 'B')
  assert.ok(p.avatarSrc)
  assert.equal(p.guest, true)
})
