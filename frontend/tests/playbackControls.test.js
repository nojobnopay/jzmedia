import test from 'node:test'
import assert from 'node:assert/strict'
import { PLAYBACK_RATES, normalizeRate, applyPlaybackRate, reusableSeekTime, previewFrame, previewImageStyle, surfaceClickRevealsOnly, pauseButtonPlacement } from '../src/playbackControls.js'

const ranges = pairs => ({ length: pairs.length, start: i => pairs[i][0], end: i => pairs[i][1] })

test('手机暂停按钮在工具与字幕之间居中，提示不影响按钮位置', () => {
  const normal = pauseButtonPlacement(208, { mobile: true })
  assert.deepEqual(normal, { visible: true, size: 60, center: 120, showHint: true })
  const narrow = pauseButtonPlacement(148, { mobile: true })
  assert.equal(narrow.size, 52)
  assert.equal(narrow.center - narrow.size / 2, 64)
  assert.equal(narrow.center + narrow.size / 2, 116)
  assert.equal(narrow.showHint, false)
})

test('桌面与全屏暂停按钮以视频居中，极低视口使用底栏播放', () => {
  assert.equal(pauseButtonPlacement(360).center, 180)
  const full = pauseButtonPlacement(375, { mobile: true, fullscreen: true, controlsHeight: 128 })
  assert.equal(full.center, 187.5)
  assert.ok(full.center + full.size / 2 <= 375 - 128 - 8)
  assert.equal(pauseButtonPlacement(240, { mobile: true, fullscreen: true, controlsHeight: 128 }).visible, false)
  for (const height of [0, 40, 100, NaN]) assert.equal(pauseButtonPlacement(height, { mobile: true }).visible, false)
})

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

const tailManifest = () => ({ state: 'ready', completion: 'known_tail_missing',
  duration: 9449.147, count: 945, available_count: 925, coverage_end: 9250,
  interval: 10, columns: 5, rows: 5, width: 480, height: 270,
  warning: { code: 'confirmed_tail_damage', message: '已确认片源尾部损坏，末尾部分没有进度预览' },
  pages: Array.from({ length: 37 }, (_, i) => 'page' + i) })

test('confirmed tail damage exposes only the verified prefix with an exclusive coverage boundary', () => {
  const manifest = tailManifest()
  assert.equal(previewFrame(manifest, 9249.999).url, 'page36')
  assert.equal(previewFrame(manifest, 9250), null)
  assert.equal(previewFrame(manifest, 9440), null)
  assert.equal(previewFrame(manifest, 99999), null)
  assert.equal(manifest.duration, 9449.147)
  assert.equal(manifest.count, 945)
  const padded = { ...manifest, available_count: 932, coverage_end: 9320, pages: [...manifest.pages, 'page37'] }
  assert.equal(previewFrame(padded, 9319).url, 'page37')
  assert.equal(previewFrame(padded, 9320), null, 'Padding in a present final sprite is never exposed as a valid frame')
})

test('malformed new coverage fields cannot expose a sprite or silently use legacy clamping', () => {
  for (const patch of [
    { available_count: 925.5 }, { available_count: '925' }, { available_count: 0 },
    { available_count: 946 }, { count: 944 }, { coverage_end: Infinity }, { coverage_end: -1 },
    { coverage_end: 9450 }, { duration: NaN }, { duration: '9449.147' },
    { completion: 'unknown' }, { warning: { code: 'decoder' } }, { pages: ['page0'] },
  ]) assert.equal(previewFrame({ ...tailManifest(), ...patch }, 100), null, JSON.stringify(patch))
  assert.equal(previewFrame({ interval: 10, count: 27, columns: 5, rows: 5, width: 160, height: 90,
    pages: ['page0', 'page1'], available_count: 25 }, 100), null)
  assert.equal(previewFrame({ interval: 10, count: 27, columns: 5, rows: 5, width: 160, height: 90,
    pages: ['page0', 'page1'], warning: { code: 'confirmed_tail_damage' } }, 100), null)
})

test('160/320/480 像素预览统一显示为 160×90 CSS 像素，精灵图缩放保持行列及分页定位', () => {
  for (const width of [160, 320, 480]) {
    const manifest = { interval: 10, columns: 5, rows: 5, width, height: width * 9 / 16,
      count: 27, pages: ['page0', 'page1'] }
    for (const [time, url, x, y] of [
      [0, 'page0', 0, 0], [40, 'page0', 640, 0], [50, 'page0', 0, 90],
      [240, 'page0', 640, 360], [250, 'page1', 0, 0], [260, 'page1', 160, 0],
      [999, 'page1', 160, 0],
    ]) {
      assert.deepEqual(previewImageStyle(previewFrame(manifest, time)), {
        width: '160px', height: '90px', backgroundImage: `url("${url}")`,
        backgroundPosition: `-${x}px -${y}px`, backgroundSize: '800px 450px',
      })
    }
  }
  assert.deepEqual(previewImageStyle(null), {})
})

test('a preview scaled for a short dialog preserves the selected sprite crop', () => {
  const style = previewImageStyle({ url: '/preview.jpg', x: 480, y: 270,
    width: 480, height: 270, sheetWidth: 2400, sheetHeight: 1350 }, 96)
  assert.deepEqual(style, { width: '96px', height: '54px', backgroundImage: 'url("/preview.jpg")',
    backgroundPosition: '-96px -54px', backgroundSize: '480px 270px' })
})

test('窗口和全屏的隐藏态首次点击只显控件，已显示时才暂停', () => {
  assert.equal(surfaceClickRevealsOnly(false), true)
  assert.equal(surfaceClickRevealsOnly(true), false)
})
