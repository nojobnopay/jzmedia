import test from 'node:test'
import assert from 'node:assert/strict'
import { visibilityPatch, visibilityPreview } from '../src/contentVisibility.js'

const result = (range, children, status) => Object.fromEntries(
  visibilityPreview(range, children, status ? { children_auto_status: status } : null)
    .map(row => [row.mode, row.value]))

test('private saves cannot overwrite or contradict a parent child decision', () => {
  for (const choice of ['auto', 'allow', 'block', 'keep']) {
    assert.deepEqual(visibilityPatch('movie', [1, 2], 'private', choice),
      { kind: 'movie', ids: [1, 2], is_private: true })
    assert.deepEqual(result('private', choice, 'allow'),
      { normal: '不显示', children: '不显示', private: '显示' })
  }
})

test('allowing children retains normal visibility, while a child ban only hides children', () => {
  assert.deepEqual(visibilityPatch('show', [3], 'normal', 'allow'),
    { kind: 'show', ids: [3], is_private: false, children_override: 'allow' })
  assert.deepEqual(result('normal', 'allow', 'block'),
    { normal: '显示', children: '显示', private: '不显示' })
  assert.deepEqual(result('normal', 'block', 'allow'),
    { normal: '显示', children: '不显示', private: '不显示' })
})

test('automatic previews use the saved decision and keep unknown or mixed results explicit', () => {
  assert.equal(result('normal', 'auto', 'allow').children, '显示')
  assert.equal(result('normal', 'auto', 'block').children, '不显示')
  assert.equal(result('normal', 'auto', 'unknown').children, '不显示（资料不足）')
  assert.equal(result('normal', 'auto').children, '按每部资料筛选')
})

test('batch keep is a no-op even after editing and returning to the original range', () => {
  assert.deepEqual(visibilityPatch('movie', [1, 2], 'keep', 'allow'), { kind: 'movie', ids: [1, 2] })
  assert.deepEqual(result('keep', 'allow', 'allow'),
    { normal: '保持各自设置', children: '保持各自设置', private: '保持各自设置' })
  assert.deepEqual(visibilityPatch('movie', [1, 2], 'normal', 'keep'),
    { kind: 'movie', ids: [1, 2], is_private: false })
})
