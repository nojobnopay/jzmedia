import test from 'node:test'
import assert from 'node:assert/strict'
import { SETTINGS_PAGES, settingsTarget, toolViewForSection } from '../src/settingsNavigation.js'

test('explicit settings page takes priority over remembered library query', () => {
  for (const { id } of SETTINGS_PAGES) {
    assert.equal(settingsTarget({ sec: id, library: '3', media: '2' }).page, id)
  }
  assert.equal(settingsTarget({}).page, 'sec-status')
  assert.equal(settingsTarget({ sec: 'unknown' }).page, 'sec-status')
})

test('legacy detail links retain library scope, section and valid restore selections', () => {
  const target = settingsTarget({ sec: 'sec-restore', library: '3', media: '2', ids: '7,,8,bad,-1,0,1.2' })
  assert.deepEqual(target, { page: 'sec-libtools', sec: 'sec-restore', library: 3, media: 2, ids: [7, 8] })
  assert.equal(settingsTarget({ library: '3' }).page, 'sec-libtools')
  assert.equal(settingsTarget({ media: '2' }).page, 'sec-libtools')
  assert.equal(settingsTarget({ ids: ['7', '8'] }).page, 'sec-libtools')
  assert.equal(settingsTarget({ library: 'bad', ids: ',' }).page, 'sec-status')
  assert.equal(settingsTarget({ sec: ['sec-display'], library: ['3'] }).page, 'sec-display')
})

test('hidden tool pages open for their deep links', () => {
  assert.equal(toolViewForSection('sec-restore'), 'restore')
  assert.equal(toolViewForSection('', [8]), 'restore')
  assert.equal(toolViewForSection('sec-meta'), 'maintenance')
  assert.equal(toolViewForSection('sec-files'), 'files')
  for (const sec of ['sec-libtools', 'sec-pending', 'sec-organize', 'sec-tvorganize']) {
    assert.equal(toolViewForSection(sec), 'workflow')
    assert.equal(settingsTarget({ sec }).page, 'sec-libtools')
  }
})
