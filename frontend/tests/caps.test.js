import test from 'node:test'
import assert from 'node:assert/strict'

let sequence = 0
async function browser(t, { mse = false, native = false, managed = false, tracks = false, decodingInfo } = {}) {
  const canDecode = type => /avc1\.(42|4D|64)|mp4a\.40\./.test(type)
  const video = { canPlayType: type => type.includes('mpegurl') || type.includes('mpegURL')
    ? (native ? 'maybe' : '') : (canDecode(type) ? 'probably' : '') }
  if (tracks) video.audioTracks = []
  const ms = mse ? { isTypeSupported: canDecode } : undefined
  const values = { window: { MediaSource: ms, ManagedMediaSource: managed ? ms : undefined,
    matchMedia: () => ({ matches: false }) }, document: { createElement: () => video },
    navigator: { userAgent: 'synthetic-browser', ...(decodingInfo ? { mediaCapabilities: { decodingInfo } } : {}) },
    sessionStorage: { getItem: () => null, setItem: () => {} } }
  for (const [key, value] of Object.entries(values)) {
    const old = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
    t.after(() => { if (old) Object.defineProperty(globalThis, key, old); else delete globalThis[key] })
  }
  return import('../src/caps.js?browser=' + sequence++)
}

test('无 MSE 仍识别原生 MP4 的 H264/AAC，文件探测与 MSE 缓存互不污染', async t => {
  const mod = await browser(t)
  const caps = await mod.getCaps()
  assert.equal(caps.mse, false)
  assert.equal(caps.video.h264, false)
  assert.equal(caps.file.video.h264, true)
  assert.equal(caps.file.audio.aac, true)
  const s = 'video/mp4; codecs="avc1.640028"'
  assert.deepEqual(await mod.probeStrings([s]), { [s]: false })
  assert.deepEqual(await mod.probeStrings([s], null, 'file'), { [s]: true })
  assert.deepEqual(await mod.probeStrings([s]), { [s]: false })
})

test('没有 MSE 的原生 HLS 浏览器使用文件解码能力和 TS 兼容策略', async t => {
  const mod = await browser(t, { native: true })
  const caps = await mod.getCaps()
  assert.equal(caps.native_hls, true)
  assert.equal(caps.hls_compat, true)
  assert.equal(caps.video.h264, true)
  assert.equal(caps.audio.aac, true)
  assert.equal(caps.native_audio_tracks, false)
  assert.equal(caps.hdr_decode, false, 'canPlayType 没有 PQ 证据时不推断 HDR 解码')
})

test('MSE 可用时优先 hls.js，避免 Chromium 的原生 HLS maybe 误报', async t => {
  const mod = await browser(t, { mse: true, native: true })
  const caps = await mod.getCaps()
  assert.equal(caps.mse, true)
  assert.equal(caps.native_hls_available, true)
  assert.equal(caps.native_hls, false)
  assert.equal(caps.hls_compat, false)
})

test('ManagedMediaSource 浏览器保留原生 HLS 和真实音轨 API', async t => {
  const mod = await browser(t, { mse: true, native: true, managed: true, tracks: true })
  const caps = await mod.getCaps()
  assert.equal(caps.native_hls, true)
  assert.equal(caps.hls_compat, false)
  assert.equal(caps.native_audio_tracks, true)
})

test('MediaCapabilities 明确拒绝时不被 canPlayType 覆盖，分别使用 file/media-source', async t => {
  const modes = new Set()
  const mod = await browser(t, { mse: true, decodingInfo: async cfg => {
    modes.add(cfg.type)
    return { supported: cfg.type === 'file' && /avc1\.64|mp4a/.test(cfg.video?.contentType || cfg.audio?.contentType || '') }
  } })
  const caps = await mod.getCaps()
  assert.deepEqual([...modes].sort(), ['file', 'media-source'])
  assert.equal(caps.video.h264, false)
  assert.equal(caps.file.video.h264, true)
  assert.equal(caps.file.video.h264_hi10p, false)
})

test('失效的 SourceBuffer 不被识别为可用 MSE', async t => {
  const mod = await browser(t, { mse: true, native: true })
  window.SourceBuffer = function () {}
  assert.equal(mod.playbackSupport().mse, false)
  assert.equal(mod.playbackSupport().native_hls, true)
})

test('小米无 MSE 使用普通 MP4，保留原生 HLS 声明用于诊断', async t => {
  const mod = await browser(t, { native: true })
  navigator.userAgent = 'Chrome/135.0 Mobile Safari XiaoMi/MiuiBrowser/20.28.1020918'
  const caps = await mod.getCaps()
  assert.equal(caps.native_hls_available, true)
  assert.equal(caps.native_hls, false)
  assert.equal(caps.mp4_compat, true)
  assert.equal(caps.file.video.h264, true)
  assert.equal(caps.video.h264, true)
})

test('小米浏览器 MSE 可用时保留 hls.js，不由 UA 强制兼容转换', async t => {
  const mod = await browser(t, { mse: true, native: true })
  navigator.userAgent = 'XiaoMi/MiuiBrowser/20.28.1020918'
  assert.equal((await mod.getCaps()).mp4_compat, false)
})

test('切换浏览器播放模式改变 MSE 时，旧能力缓存失效', async t => {
  const mod = await browser(t, { native: true })
  navigator.userAgent = 'XiaoMi/MiuiBrowser/20.28.1020918'
  const storage = new Map()
  sessionStorage.getItem = key => storage.get(key)
  sessionStorage.setItem = (key, value) => storage.set(key, value)
  assert.equal((await mod.getCaps()).mp4_compat, true)
  window.MediaSource = { isTypeSupported: type => /avc1\.(42|4D|64)|mp4a\.40\./.test(type) }
  const reloaded = await import('../src/caps.js?browser=' + sequence++)
  const caps = await reloaded.getCaps()
  assert.equal(caps.mse, true)
  assert.equal(caps.mp4_compat, false)
})
