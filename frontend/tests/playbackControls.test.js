import test from 'node:test'
import assert from 'node:assert/strict'
import { PLAYBACK_RATES, normalizeRate, applyPlaybackRate, reusableSeekTime, previewFrame } from '../src/playbackControls.js'

const ranges = pairs => ({ length: pairs.length, start: i => pairs[i][0], end: i => pairs[i][1] })

test('所有倍速在视频元素重建后恢复，并保留音调', () => {
  for (const rate of PLAYBACK_RATES) {
    const video = { playbackRate: 1, defaultPlaybackRate: 1, preservesPitch: false }
    applyPlaybackRate(video, rate)
    assert.equal(video.playbackRate, rate)
    assert.equal(video.defaultPlaybackRate, rate)
    assert.equal(video.preservesPitch, true)
  }
  for (const invalid of [-1, 0, 4, NaN, 'bad', null]) assert.equal(normalizeRate(invalid), 1)
  assert.equal(normalizeRate('1.25'), 1.25)
})

test('HLS 复用按真实媒体起点换算，覆盖缓冲空洞和可定位但未下载的分片', () => {
  const video = { buffered: ranges([[0, 12], [20, 30]]), seekable: ranges([[0, 40]]) }
  assert.equal(reusableSeekTime(video, 110, 100), 10)
  assert.equal(reusableSeekTime(video, 115, 100), 15)
  assert.equal(reusableSeekTime(video, 140, 100), null)
  assert.equal(reusableSeekTime(video, 99, 100), null)
  video.seekable = ranges([])
  assert.equal(reusableSeekTime(video, 115, 100), null)
  assert.equal(reusableSeekTime(video, 125, 100), 25)
  assert.equal(reusableSeekTime(null, 10), null)
})

test('缩略图跨页、片尾及未完成页面不会错图；原片时间不受倍速影响', () => {
  const manifest = { interval: 10, columns: 5, rows: 5, width: 160, height: 90,
    count: 27, pages: ['page0', 'page1'] }
  assert.deepEqual(previewFrame(manifest, 249), {
    url: 'page0', x: 640, y: 360, width: 160, height: 90, sheetWidth: 800, sheetHeight: 450,
  })
  assert.equal(previewFrame(manifest, 250).url, 'page1')
  assert.equal(previewFrame(manifest, 270).x, 160)
  manifest.pages.pop()
  assert.equal(previewFrame(manifest, 260), null)
  assert.equal(previewFrame(null, 0), null)
})
