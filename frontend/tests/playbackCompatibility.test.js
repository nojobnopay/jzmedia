import test from 'node:test'
import assert from 'node:assert/strict'
import { compatibilityCaps, blockedPlaybackMessage, mediaErrorMessage } from '../src/playbackCompatibility.js'

test('组件失效时原生回退采用 file 能力，不修改基础缓存和文件直发矩阵', () => {
  const source = { video: { hevc: true }, audio: { dts: true }, probes: { hevc: true },
    native_hls: false, mse: true, file: { video: { h264: true }, audio: { aac: true }, probes: { h264: true } } }
  const before = structuredClone(source)
  const caps = compatibilityCaps(source, { native: true })
  assert.deepEqual(caps.video, { h264: true })
  assert.deepEqual(caps.probes, { h264: true })
  assert.equal(caps.native_hls, true)
  assert.equal(caps.hls_compat, true)
  assert.equal(caps.force_hls, true)
  assert.deepEqual(source, before)
})

test('直发解码失败后撤销源编码支持，强制重编；文件能力仍保留用于诊断', () => {
  const base = { video: { h264: true }, audio: { aac: true }, probes: { h264: true },
    hdr: true, hdr_decode: true, file: { video: { h264: true } } }
  const caps = compatibilityCaps(base, { transcode: true })
  assert.deepEqual(caps.video, {})
  assert.deepEqual(caps.probes, { h264: false })
  assert.equal(caps.hdr_decode, false)
  assert.deepEqual(caps.file, base.file)
})

test('网络、解码和播放格式不可用有不同恢复提示，不误报文件损坏', () => {
  assert.match(mediaErrorMessage(2), /网络/)
  assert.match(mediaErrorMessage(4), /解码/)
  assert.match(blockedPlaybackMessage(['no_mse']), /播放格式/)
  for (const message of [mediaErrorMessage(3), mediaErrorMessage(4), blockedPlaybackMessage()]) {
    assert.doesNotMatch(message, /文件.*损坏/)
  }
})
