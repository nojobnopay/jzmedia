import test from 'node:test'
import assert from 'node:assert/strict'
import { episodeDisplayTitle } from '../src/episodeTitles.js'

test('episode display titles use the server value and retain old-server title fallback', () => {
  assert.equal(episodeDisplayTitle({ title: '第 1 集', display_title: '第1-2集' }), '第1-2集')
  assert.equal(episodeDisplayTitle({ title: '初次交锋', display_title: '初次交锋' }), '初次交锋')
  assert.equal(episodeDisplayTitle({ title: '旧服务端集名' }), '旧服务端集名')
  assert.equal(episodeDisplayTitle({ title: '原始资料', display_title: '' }), '')
  assert.equal(episodeDisplayTitle(null), '')
})
