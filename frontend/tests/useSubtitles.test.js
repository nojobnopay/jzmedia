// 字幕 composable 初始化回归网（2026-09-21）：PlayerModal 抽离后 compatSub 的 IIFE
// 括号写错（ref(fn)() 调用了 ref 对象）导致 setup 抛 "ref(...) is not a function"、
// 详情页点播放无反应。本用例以 stub ctx 直接执行 useSubtitles 初始化 + 关键状态机，
// 任何 setup 异常都会在 node --test 阶段失败（lint/build 不会执行 setup，测不出这类问题）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ref } from 'vue'

import { useSubtitles, withTimeout, firstUrlReachable, ASS_READY_TIMEOUT_MS } from '../src/useSubtitles.js'

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

test('withTimeout 正常 resolve 透传结果', async () => {
  assert.equal(await withTimeout(Promise.resolve('ok'), 50), 'ok')
})

test('withTimeout 挂起超期拒绝（ASS ready 卡死兜底）', async () => {
  await assert.rejects(withTimeout(new Promise(() => {}), 20), /timeout/)
  assert.ok(ASS_READY_TIMEOUT_MS >= 10000, 'ASS ready 超时应留足字体加载时间')
})

test('firstUrlReachable 按 GET 结果判定（字体预检）', async () => {
  assert.equal(await firstUrlReachable(async () => ({ ok: true }), ['/f/1.ttf']), true)
  assert.equal(await firstUrlReachable(async () => ({ ok: false, status: 404 }), ['/f/1.ttf']), false)
  assert.equal(await firstUrlReachable(async () => { throw new Error('down') }, ['/f/1.ttf']), false)
  assert.equal(await firstUrlReachable(async () => ({ ok: true }), []), false)
})

test('firstUrlReachable 用 GET + abort（本栈路由仅 GET，HEAD 会 405）', async () => {
  let seen = null
  const fake = async (u, init) => { seen = { u, init }; return { ok: true } }
  assert.equal(await firstUrlReachable(fake, ['/f/1.ttf']), true)
  assert.equal(seen.u, '/f/1.ttf')
  assert.equal(seen.init.method, 'GET')
  assert.ok(seen.init.signal, '应传 signal 以便拿到响应头即 abort')
})

test('PlayerModal 解构的 useSubtitles 键全部存在（防命名错位回归）', () => {
  // 2026-09 线上事故：PlayerModal 解构了 attachSubtitleVideo/detachSubtitleVideo/
  // subtitleDebugInfo，而 composable 返回 attachVideo/detachVideo/debugInfo（undefined），
  // bindVideo 首行调用即抛 TypeError → onMounted 中断（全屏 isFull 监听/settings 实例、
  // 看门狗、调试入口全部失效）。lint/build/templateBindings 都测不出，必须靠本用例。
  const src = readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/components/PlayerModal.vue'),
    'utf8')
  const m = src.match(/const\s*\{([\s\S]*?)\}\s*=\s*useSubtitles\(/)
  assert.ok(m, 'PlayerModal 应解构 useSubtitles()')
  const names = []
  for (const part of m[1].split(',')) {
    const p = part.trim()
    if (!p) continue
    names.push(p.includes(':') ? p.split(':')[0].trim() : p)   // 取源名（别名左侧）
  }
  assert.ok(names.length >= 20, '解构块应包含全部字幕后端')
  const api = useSubtitles(makeCtx())
  for (const n of names) {
    assert.ok(n in api, `useSubtitles() 返回缺少键：${n}`)
  }
  assert.equal(typeof api.attachVideo, 'function')
  assert.equal(typeof api.detachVideo, 'function')
  assert.equal(typeof api.debugInfo, 'function')
})
