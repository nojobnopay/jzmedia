import test from 'node:test'
import assert from 'node:assert/strict'
import { vttMs, parseVtt, vttPlain } from '../src/subtitleParse.js'

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
