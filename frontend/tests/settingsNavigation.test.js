import test from 'node:test'
import assert from 'node:assert/strict'
import { SETTINGS_PAGES, SETTINGS_TASK_PAGES, settingsTarget, settingsBrowseTarget, toolViewForSection } from '../src/settingsNavigation.js'

test('explicit settings page takes priority over remembered library query', () => {
  for (const { id } of SETTINGS_PAGES) {
    assert.equal(settingsTarget({ sec: id, library: '3', media: '2' }).page, id)
  }
  assert.equal(settingsTarget({}).page, 'sec-status')
  assert.equal(settingsTarget({ sec: 'unknown' }).page, 'sec-status')
})

test('legacy detail links retain library scope, section and valid restore selections', () => {
  const target = settingsTarget({ sec: 'sec-restore', library: '3', media: '2', ids: '7,,8,bad,-1,0,1.2' })
  assert.deepEqual(target, { page: 'sec-libtools', sec: 'sec-restore', library: 3, media: 2, ids: [7, 8], files_path: '' })
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

test('file management keeps its own navigation while preserving library scope', () => {
  assert.deepEqual(settingsTarget({ sec: 'sec-files', library: '9', media: '2' }), {
    page: 'sec-files', sec: 'sec-files', library: 9, media: 2, ids: [], files_path: '',
  })
  assert.equal(settingsTarget({ sec: 'sec-tmdb' }).page, 'sec-tmdb')
  assert.deepEqual(SETTINGS_PAGES.filter(page => page.group === '资料与智能').map(page => page.id),
    ['sec-tmdb', 'sec-matching', 'sec-ai', 'sec-offline'])
})

test('file management deep link carries a sanitized relative directory', () => {
  assert.equal(settingsTarget({ sec: 'sec-files', library: '9', files_path: '喜剧/周星驰' }).files_path, '喜剧/周星驰')
  assert.equal(settingsTarget({ sec: 'sec-files', library: '9' }).files_path, '')
  assert.equal(settingsTarget({ sec: 'sec-files', library: '9', files_path: '../secret' }).files_path, '')
  assert.equal(settingsTarget({ sec: 'sec-files', library: '9', files_path: 'a/./b' }).files_path, 'a/b')
})


test('secondary settings tasks keep direct links and return to the parent section', () => {
  assert.equal(settingsTarget({ sec: 'sec-token', media: '2' }).page, 'sec-token')
  assert.equal(SETTINGS_TASK_PAGES.find(page => page.id === 'sec-token').parent, 'sec-auth')
  assert.equal(settingsTarget({ sec: 'sec-maintenance' }).page, 'sec-maintenance')
})

test('settings return retains a media detail or browse query, with a local fallback', () => {
  for (const path of ['/m/101?media=2', '/tv/3/s/1/e/9', '/?q=test&media=2', '/collections', '/online?source=demo&item=1']) {
    assert.equal(settingsBrowseTarget(path, 4), path)
  }
  for (const path of ['https://example.com', '//example.com', '/settings?sec=sec-auth', '/setup', '/unknown', null]) {
    assert.equal(settingsBrowseTarget(path, 4), '/?media=4')
  }
  assert.equal(settingsBrowseTarget(null, 'bad'), '/')
})
