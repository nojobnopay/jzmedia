// 播放器生命周期回归网（2026-09 用户实测）：关窗时若建会话 POST 还挂在 await，
// onUnmounted 的 closeSession/destroyHls 都是 no-op；晚到的响应会 startPing 并把 hls
// 挂到已脱离文档的 <video autoplay> 上 → 海报墙只闻其声，必须强刷。node --test 没有
// DOM 环境（项目无 jsdom），这里按 2026-09 既有约定做 SFC 源码静态断言（同
// templateBindings.test.js 的静态检查思路），防止后续重构把守卫删掉/绕过。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const COMPONENT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '../src/components/PlayerModal.vue')
const API = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/api.js')
const src = readFileSync(COMPONENT, 'utf-8')
const apiSrc = readFileSync(API, 'utf-8')

// 取函数体（首个 { 到配对 }）：跳过字符串/注释，够了——本组件相关函数不用模板字面量嵌 {}。
function fnBody(marker) {
  const start = src.indexOf(marker)
  assert.ok(start >= 0, `PlayerModal 缺少 ${marker}`)
  const open = src.indexOf('{', start)
  let depth = 0
  let inStr = null
  let inLine = false
  let inBlock = false
  for (let i = open; i < src.length; i++) {
    const c = src[i]
    const n = src[i + 1]
    if (inLine) { if (c === '\n') inLine = false; continue }
    if (inBlock) { if (c === '*' && n === '/') { inBlock = false; i++ } continue }
    if (inStr) {
      if (c === '\\') { i++; continue }
      if (c === inStr) inStr = null
      continue
    }
    if (c === '/' && n === '/') { inLine = true; i++; continue }
    if (c === '/' && n === '*') { inBlock = true; i++; continue }
    if (c === '"' || c === "'" || c === '`') { inStr = c; continue }
    if (c === '{') depth++
    else if (c === '}') { depth--; if (depth === 0) return src.slice(open, i + 1) }
  }
  assert.fail(`无法定位 ${marker} 的函数体`)
}

test('onUnmounted 作废在飞 reload 并中止请求（关窗后不再挂 hls）', () => {
  const body = fnBody('onUnmounted(')
  assert.match(body, /disposed\s*=\s*true/, 'onUnmounted 必须置 disposed')
  assert.match(body, /reloadGen\s*\+=\s*1/, 'onUnmounted 必须作废 reload 代际')
  assert.match(body, /bootCtrl[\s\S]{0,80}abort\(\)/, 'onUnmounted 必须中止在飞请求')
})

test('onBeforeUnmount 显式停掉媒体元素（不依赖 DOM 移除语义）', () => {
  const body = fnBody('onBeforeUnmount(')
  assert.match(body, /pause\(\)/)
  assert.match(body, /removeAttribute\('src'\)/)
  assert.match(body, /load\(\)/)
})

test('mountHls 在 await 后校验元素/代际，拒绝挂到过期 <video> 上', () => {
  const body = fnBody('async function mountHls(')
  assert.match(body, /videoEl\.value\s*!==\s*v/, 'mountHls 必须做元素一致性校验')
  assert.match(body, /disposed/, 'mountHls 必须做卸载校验')
  assert.match(body, /opts\.gen[\s\S]{0,40}reloadGen|gen\s*!==\s*reloadGen/,
    'mountHls 必须做代际校验')
})

test('reload 每个 await 后都有 disposed/代际守卫且可被取消', () => {
  const body = fnBody('async function reload(')
  const guards = body.match(/disposed\s*\|\|\s*gen\s*!==\s*reloadGen/g) || []
  assert.ok(guards.length >= 3, `reload 的 await 后守卫不足（${guards.length} < 3）`)
  assert.match(body, /signal:\s*ctrl\.signal/, 'decide/sessions 必须带取消信号')
  assert.match(body, /session_id[\s\S]{0,200}DELETE/, '晚到会话必须立即 DELETE 回收')
})

test('startPing 受 disposed 保护（卸载后心跳不再泄漏）', () => {
  const body = fnBody('function startPing(')
  assert.match(body, /disposed/, 'startPing 必须检查 disposed')
})

test('api() 支持外部 signal：外部取消与超时区分', () => {
  assert.match(apiSrc, /signal:\s*outer/, 'api 必须接收外部 AbortSignal')
  assert.match(apiSrc, /outer\.aborted/, 'api 必须区分外部取消与超时')
  assert.match(apiSrc, /addEventListener\('abort'/)
})
