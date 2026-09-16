import test from 'node:test'
import assert from 'node:assert/strict'
import { hasScore, fmtScore, fullStars, starRow } from '../src/ratings.js'

test('hasScore treats null/0 as missing', () => {
  assert.equal(hasScore(null), false)
  assert.equal(hasScore(0), false)
  assert.equal(hasScore('7.5'), false)
  assert.equal(hasScore(7.5), true)
})

test('fmtScore trims trailing zero', () => {
  assert.equal(fmtScore(8.0), '8')
  assert.equal(fmtScore(7.25), '7.3')
  assert.equal(fmtScore(0), '')
})

test('stars map 0-10 to 5 stars', () => {
  assert.equal(fullStars(0), 0)
  assert.equal(fullStars(10), 5)
  assert.equal(fullStars(7), 4)
  assert.equal(starRow(10), '★★★★★')
  assert.equal(starRow(0), '☆☆☆☆☆')
})
