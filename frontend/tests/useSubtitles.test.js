// 字幕 composable 初始化回归网（2026-09-21）：PlayerModal 抽离后 compatSub 的 IIFE
// 括号写错（ref(fn)() 调用了 ref 对象）导致 setup 抛 "ref(...) is not a function"、
// 详情页点播放无反应。本用例以 stub ctx 直接执行 useSubtitles 初始化 + 关键状态机，
// 任何 setup 异常都会在 node --test 阶段失败（lint/build 不会执行 setup，测不出这类问题）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'

import { useSubtitles } from '../src/useSubtitles.js'

function makeCtx() {
  const videoEl = ref(null)
  const videoKey = ref(0)
  const state = { method: 'remux', mediaStart: 0, burn: false }
  return {
    videoEl, videoKey,
    versionId: () => 401,
    kindParam: () => '',
    isEpisode: () => false,
    getMethod: () => state.method,
    getMediaStart: () => state.mediaStart,
    getBurnOn: () => state.burn,
    reload: () => { state.reloads = (state.reloads || 0) + 1 },
    toast: () => {},
    logEvt: () => {},
    state,
  }
}

test('useSubtitles 初始化不抛异常（compatSub/subStyle/延迟恢复）', () => {
  const s = useSubtitles(makeCtx())
  assert.equal(s.subs.value.length, 0)
  assert.equal(s.subIdx.value, -1)
  assert.equal(s.subIsVtt.value, false)
  assert.equal(s.subIsAss.value, false)
  assert.equal(s.subDelayVisible.value, false)
  assert.equal(s.undoDegradeDisabled.value, true)
  assert.equal(s.subDelayText.value, '0.0s')
  assert.equal(s.selectedSub(), null)
  assert.equal(s.imageSubSelected(), false)
  assert.equal(s.isLocalSub(null), false)
})

test('syncForSession 合并服务端轨并自动选默认轨（中文优先于外语 default）', () => {
  const s = useSubtitles(makeCtx())
  s.syncForSession([
    { index: 0, codec: 'subrip', lang: 'eng', default: 1, image: 0 },
    { index: 1, codec: 'subrip', lang: 'chi', default: 0, image: 0 },
  ])
  assert.equal(s.subs.value.length, 2)
  assert.equal(s.subIdx.value, 1)
  assert.equal(s.subIsVtt.value, true)   // subrip → 文本自绘层
})

test('applySubs 无视频元素时安全返回（不抛）', async () => {
  const s = useSubtitles(makeCtx())
  await s.applySubs(true)
  await s.applySubs()
})

test('dispose 清理本地字幕引用不抛', () => {
  const s = useSubtitles(makeCtx())
  s.dispose()
  assert.deepEqual(s.localSubs.value, [])
})
