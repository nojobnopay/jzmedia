// posterUrl 版本参数（回归网）：换海报原地覆盖文件、URL 不变，必须能带 ?v= 强制取新图。
import test from 'node:test'
import assert from 'node:assert/strict'

import { posterUrl } from '../src/api.js'

test('posterUrl 基本拼接与空值', () => {
  assert.equal(posterUrl('posters/479455.jpg'), '/posters/479455.jpg')
  assert.equal(posterUrl('479455.jpg'), '/posters/479455.jpg')
  assert.equal(posterUrl(''), '')
  assert.equal(posterUrl(null), '')
})

test('posterUrl 版本参数（updated_at / 换海报自增）', () => {
  assert.equal(posterUrl('posters/479455.jpg', 1789802489),
    '/posters/479455.jpg?v=1789802489')
  assert.equal(posterUrl('posters/479455.jpg', 'a b'),
    '/posters/479455.jpg?v=a%20b')
  assert.equal(posterUrl('posters/479455.jpg', 0), '/posters/479455.jpg')
})
