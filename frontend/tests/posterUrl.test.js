// posterUrl 版本参数（回归网）：换海报原地覆盖文件、URL 不变，必须能带 ?v= 强制取新图。
// posters 按功能拆子目录后：保留子路径，只剥头部 posters/。
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

test('posterUrl 子目录保留（posters 功能拆分后）', () => {
  assert.equal(posterUrl('posters/movies/479455.jpg'), '/posters/movies/479455.jpg')
  assert.equal(posterUrl('posters/persons/1.jpg'), '/posters/persons/1.jpg')
  assert.equal(posterUrl('posters/tv/100.jpg'), '/posters/tv/100.jpg')
  assert.equal(posterUrl('posters/tv/100_s1.jpg'), '/posters/tv/100_s1.jpg')
  assert.equal(posterUrl('posters/backdrops/movie_1.jpg'), '/posters/backdrops/movie_1.jpg')
  assert.equal(posterUrl('movies/479455.jpg'), '/posters/movies/479455.jpg')
  assert.equal(posterUrl('posters/movies/479455.jpg', 7), '/posters/movies/479455.jpg?v=7')
})
