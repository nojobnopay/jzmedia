import test from 'node:test'
import assert from 'node:assert/strict'

import { parseSmbInput, smbUrlOf } from '../src/smb.js'

test('UNC / 正反斜杠 / 无目录 / 无前导斜杠', () => {
  const a = parseSmbInput('\\\\NAS\\video\\Movies')
  assert.deepEqual([a.host, a.share, a.subpath, a.error], ['NAS', 'video', 'Movies', ''])
  const b = parseSmbInput('//NAS/video')
  assert.deepEqual([b.host, b.share, b.subpath, b.error], ['NAS', 'video', '', ''])
  const c = parseSmbInput('NAS\\video\\TV Shows')
  assert.deepEqual([c.host, c.share, c.subpath, c.error], ['NAS', 'video', 'TV Shows', ''])
  const d = parseSmbInput('\\\\100.101.102.103\\video\\\\Movies')
  assert.deepEqual([d.host, d.share, d.subpath, d.error], ['100.101.102.103', 'video', 'Movies', ''])
})

test('smb:// URL 与用户/百分号解码', () => {
  const a = parseSmbInput('smb://user@nas/media/%E7%94%B5%E5%BD%B1')
  assert.deepEqual([a.host, a.share, a.subpath, a.username, a.error],
    ['nas', 'media', '电影', 'user', ''])
  const b = parseSmbInput('SMB://nas/video')
  assert.equal(b.share, 'video')
})

test('错误分型', () => {
  assert.ok(parseSmbInput('\\NAS').error.includes('共享名'))
  assert.ok(parseSmbInput('Z:\\Movies').error.includes('映射盘'))
  assert.ok(parseSmbInput('').error.includes('请输入'))
})

test('smbUrlOf 回填', () => {
  assert.equal(smbUrlOf({ smb_host: 'nas', smb_share: 'video', smb_subpath: 'Movies' }),
    '\\\\nas\\video\\Movies')
  assert.equal(smbUrlOf({ smb_host: '', smb_share: 'video' }), '')
})
