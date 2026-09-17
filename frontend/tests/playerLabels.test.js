import test from 'node:test'
import assert from 'node:assert/strict'
import { subKind, audioLabel, subLabel, subBadge, fmtTime } from '../src/playerLabels.js'

test('subKind：无/文本/ASS/图像（PGS vs 未知=烧录）', () => {
  assert.equal(subKind(null), 'none')
  assert.equal(subKind({ codec: 'subrip' }), 'vtt')
  assert.equal(subKind({ codec: 'ssa' }), 'ass')
  assert.equal(subKind({ image: 1, codec: 'pgs' }), 'pgs')
  assert.equal(subKind({ image: 1, codec: 'dvd_subtitle' }), 'burn')
})

test('audioLabel/subLabel 组合字段', () => {
  assert.equal(audioLabel({ codec: 'eac3', channels: 6, lang: 'eng', title: '5.1' }, 0),
    '音轨1 EAC3 6ch eng 5.1')
  assert.equal(audioLabel({}, 1), '音轨2')
  assert.equal(subLabel({ lang: 'chs', codec: 'ass' }, 0), '字幕1 chs ASS')
  assert.equal(subLabel({ lang: 'chs', codec: 'pgs', image: 1 }, 0), '字幕1 chs')
})

test('subBadge：渲染方式 + 外挂/本地来源', () => {
  assert.equal(subBadge({ codec: 'ass' }), '（ASS 样式）')
  assert.equal(subBadge({ image: 1, codec: 'pgs', source: 'sidecar' }), '（PGS·外挂）')
  assert.equal(subBadge({ codec: 'subrip' }), '')
  assert.equal(subBadge({ codec: 'ass', source: 'local' }), '（ASS 样式·本地）')
  assert.equal(subBadge({ codec: 'srt', source: 'local' }), '（本地）')
})

test('fmtTime：时/分补零与非法值', () => {
  assert.equal(fmtTime(59), '0:59')
  assert.equal(fmtTime(61), '1:01')
  assert.equal(fmtTime(3661), '1:01:01')
  assert.equal(fmtTime(-3), '0:00')
  assert.equal(fmtTime('x'), '0:00')
})
