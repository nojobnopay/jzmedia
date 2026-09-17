import test from 'node:test'
import assert from 'node:assert/strict'
import { vttMs, parseVtt, vttPlain, activeCues, pickDefaultSub,
  decodeSubtitleBytes, localSubCodec } from '../src/subtitleParse.js'

test('vttMs：时/分/秒/毫秒与容错', () => {
  assert.equal(vttMs('00:01:02.500'), 62500)
  assert.equal(vttMs('01:02.500'), 62500)
  assert.equal(vttMs('1:02:03,004'), 3723004)
  assert.equal(vttMs('bad'), null)
  assert.equal(vttMs(''), null)
})

test('parseVtt：块解析/标签剥离/排序/跳过 STYLE 与注释', () => {
  const text = [
    'WEBVTT', '',
    'STYLE', '::cue { color: white }', '',
    'NOTE something', '', '',
    '00:00:02.000 --> 00:00:04.000 align:start',
    'Hello <b>world</b>', '',
    '00:00:00.500 --> 00:00:01.500',
    'First line', 'Second line', '',
    '00:00:09.000 --> 00:00:08.000',
    'invalid end',
  ].join('\n')
  const cues = parseVtt(text)
  assert.equal(cues.length, 2)
  assert.deepEqual(cues.map(c => [c.start, c.end]), [[500, 1500], [2000, 4000]])
  assert.equal(cues[0].text, 'First line\nSecond line')
  assert.equal(cues[1].text, 'Hello world')
  assert.equal(cues[1].align, 'start')
})

test('vttPlain：无 DOM 时仅去标签', () => {
  assert.equal(vttPlain('<i>hi</i> there'), 'hi there')
  assert.equal(vttPlain(''), '')
})

test('activeCues：区间命中/边界半开/提前中断', () => {
  const cues = [{ start: 0, end: 1000 }, { start: 1000, end: 2000 }, { start: 5000, end: 6000 }]
  assert.deepEqual(activeCues(cues, 0).length, 1)
  assert.deepEqual(activeCues(cues, 999).length, 1)
  assert.deepEqual(activeCues(cues, 1000).map(c => c.start), [1000])   // [start,end) 半开
  assert.deepEqual(activeCues(cues, 2000), [])
  assert.deepEqual(activeCues(cues, 5500).map(c => c.start), [5000])
  assert.deepEqual(activeCues(null, 10), [])
})

test('pickDefaultSub：PGS 中文默认轨可自动选（用户反馈：芭蕾杀姬 8 条 PGS 全中文）', () => {
  const pgs = (i, d) => ({ index: i, codec: 'pgs', image: 1, lang: 'chi', default: d, source: 'embedded' })
  const list = [pgs(0, 1), pgs(1, 0), pgs(2, 0), pgs(3, 0), pgs(4, 0), pgs(5, 0), pgs(6, 0), pgs(7, 0)]
  assert.equal(pickDefaultSub(list), 0)
  assert.equal(pickDefaultSub([pgs(1, 0), pgs(2, 0)]), 0)   // 无 default → 首条中文 PGS
})

test('pickDefaultSub：VobSub（烧录轨）不自动选，优先顺序仍生效', () => {
  const list = [
    { index: 0, codec: 'vobsub', image: 1, lang: 'chi', default: 1, source: 'embedded' },
    { index: 1, codec: 'subrip', image: 0, lang: 'eng', default: 0, source: 'embedded' },
    { index: 2, codec: 'subrip', image: 0, lang: 'chi', default: 0, source: 'sidecar' }
  ]
  assert.equal(pickDefaultSub(list), 2)                     // 跳过 vobsub，落到中文文本
  assert.equal(pickDefaultSub([
    { index: 0, codec: 'vobsub', image: 1, lang: 'chi', default: 1 }
  ]), -1)
})

test('pickDefaultSub：语言优先（中文 default > 首条中文 > 任意 default）', () => {
  const list = [
    { index: 0, codec: 'subrip', image: 0, lang: 'eng', default: 1, source: 'embedded' },
    { index: 1, codec: 'ass', image: 0, lang: 'chi', default: 1, source: 'sidecar' },
    { index: 2, codec: 'subrip', image: 0, lang: 'chi', default: 0, source: 'sidecar' }
  ]
  assert.equal(pickDefaultSub(list), 1)
  // 外语 default 不压中文字幕（用户确认：语言优先）
  assert.equal(pickDefaultSub([
    { index: 0, codec: 'subrip', image: 0, lang: 'eng', default: 1, source: 'embedded' },
    { index: 1, codec: 'subrip', image: 0, lang: 'chi', default: 0, source: 'embedded' }
  ]), 1)
  // 无中文时回落任意 default，再无可选则 -1
  assert.equal(pickDefaultSub([{ index: 0, codec: 'subrip', image: 0, lang: 'eng', default: 1 }]), 0)
  assert.equal(pickDefaultSub([{ index: 0, codec: 'subrip', image: 0, lang: 'eng', default: 0 }]), -1)
  assert.equal(pickDefaultSub([]), -1)
  assert.equal(pickDefaultSub(null), -1)
})

test('decodeSubtitleBytes：BOM 优先 / UTF-8 严格 / GB18030 兜底', () => {
  assert.equal(decodeSubtitleBytes(new Uint8Array([0xEF, 0xBB, 0xBF, 0xE4, 0xB8, 0xAD])), '中')  // UTF-8 BOM
  assert.equal(decodeSubtitleBytes(new Uint8Array([0xFF, 0xFE, 0x2D, 0x4E])), '中')              // UTF-16LE BOM
  assert.equal(decodeSubtitleBytes(new Uint8Array([0xD6, 0xD0])), '中')                            // GBK 兜底
  assert.equal(decodeSubtitleBytes(new Uint8Array([])), '')
})

test('parseVtt：SRT 文本可直接解析（逗号时间戳 + 序号行）', () => {
  const srt = '1\r\n00:00:02,914 --> 00:00:11,358\r\n大桥下面\r\n\r\n2\r\n00:00:46,484 --> 00:00:48,000\r\n第二句\r\n'
  const cues = parseVtt(srt)
  assert.deepEqual(cues.map(c => [c.start, c.end, c.text]),
    [[2914, 11358, '大桥下面'], [46484, 48000, '第二句']])
})

test('localSubCodec：仅文本字幕可临时加载（图片格式拒绝）', () => {
  assert.equal(localSubCodec('大桥下面.srt'), 'srt')
  assert.equal(localSubCodec('Sub.VTT'), 'vtt')
  assert.equal(localSubCodec('a.ass'), 'ass')
  assert.equal(localSubCodec('a.SSA'), 'ssa')
  assert.equal(localSubCodec('a.sup'), '')
  assert.equal(localSubCodec('a.sub'), '')
  assert.equal(localSubCodec('a.idx'), '')
  assert.equal(localSubCodec(''), '')
})
