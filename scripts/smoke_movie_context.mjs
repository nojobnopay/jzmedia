// Run: node scripts/smoke_movie_context.mjs
// Real Vue router/pages, fictional public fixtures, held API responses and no backend/NAS.
// Vite config/env loading is disabled; only loopback assets and explicit mock APIs are served.
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { createFixture, movies } from './fixtures/uiReviewData.mjs'

const project = fileURLToPath(new URL('../', import.meta.url))
const temporary = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-movie-context-'))
const output = path.join(temporary, 'frontend')
const fixture = createFixture(), movieState = new Map()
const requests = [], unexpected = [], errors = [], holds = [], expectedFailures = new Set()
let server, browser, page
const movie = id => ({ ...movies.find(item => item.id === id), needs_review: true, ...movieState.get(id) })
const wait = async predicate => {
  for (let count = 0; count < 100; count++) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 30))
  }
  throw new Error('Expected isolated API request did not arrive')
}
function hold(method, pathname) {
  const value = { method, pathname, seen: false, send: null }
  holds.push(value)
  return value
}
function remember(id, values) { movieState.set(id, { ...movieState.get(id), ...values }) }
async function mock(req, res, url) {
  const key = url.pathname, method = req.method
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > 65536) throw new Error('Oversized isolated fixture request')
    chunks.push(chunk)
  }
  const raw = Buffer.concat(chunks).toString(), body = raw ? JSON.parse(raw) : null
  requests.push({ method, path: key, body })
  let value
  const detailId = Number(key.match(/^\/api\/movies\/(\d+)$/)?.[1])
  const confirmId = Number(key.match(/^\/api\/movies\/(\d+)\/confirm-match$/)?.[1])
  const refreshId = Number(key.match(/^\/api\/movies\/(\d+)\/refresh$/)?.[1])
  if (method === 'POST' && key === '/api/movies/batch') {
    assert.equal(body.ids.length, 1)
    assert.ok([101, 102].includes(body.ids[0]))
    assert.equal(typeof body.ops.watched, 'boolean')
    remember(body.ids[0], { watched: Number(body.ops.watched) })
    value = { ok: true }
  } else if (method === 'POST' && confirmId) {
    remember(confirmId, { needs_review: false })
    value = { ok: true }
  } else if (method === 'POST' && refreshId) value = { changed: true }
  else if (method === 'GET' && detailId && [101, 102].includes(detailId)) value = movie(detailId)
  else if (method === 'GET' || (method === 'POST' && key === '/api/stream/versions')) value = fixture.api(url, method, body)
  else unexpected.push(`${method} ${key}`)
  if (value === undefined) {
    unexpected.push(`${method} ${key}`)
    res.writeHead(501, { 'Content-Type': 'application/json' })
    res.end('{"detail":"Unmocked isolated movie API"}')
    return
  }
  const send = (status = 200, result = value) => {
    if (res.destroyed) return
    res.writeHead(status, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(result))
  }
  const pending = holds.find(item => !item.seen && item.method === method && item.pathname === key)
  if (pending) { pending.seen = true; pending.send = send }
  else send()
}

try {
  await build({ root: path.join(project, 'frontend'), configFile: false, envFile: false,
    cacheDir: path.join(temporary, 'vite-cache'), plugins: [vue()], worker: { format: 'es' }, logLevel: 'error',
    build: { outDir: output, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (key.startsWith('/posters/') || /\/backdrop$|\/poster-orig$/.test(key)) {
        res.writeHead(200, { 'Content-Type': 'image/svg+xml' })
        res.end(fixture.artwork(key))
        return
      }
      if (key.startsWith('/api/')) { await mock(req, res, url); return }
      let file
      if (/^\/assets\/[\w.-]+$/.test(key)) file = path.join(output, key)
      else if (/^\/(?:favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key)) file = path.join(output, key)
      else if (/^\/(?:m\/\d+)?\/?$/.test(key)) file = path.join(output, 'index.html')
      else { unexpected.push('Asset ' + key); res.writeHead(404); res.end('Only isolated movie routes are served'); return }
      const bytes = await readFile(file)
      res.writeHead(200, { 'Content-Type': { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
        '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' }[path.extname(file)] || 'application/octet-stream' })
      res.end(bytes)
    } catch (error) {
      errors.push('Fixture server: ' + error.message)
      if (!res.headersSent) res.writeHead(500)
      res.end('Isolated fixture failure')
    }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== base) {
      unexpected.push('External request blocked: ' + route.request().url())
      return route.abort()
    }
    return route.continue()
  })
  page = await context.newPage()
  page.setDefaultTimeout(10000)
  page.on('pageerror', error => errors.push(String(error)))
  page.on('response', response => {
    const pathname = new URL(response.url()).pathname
    if (response.status() >= 400 && !expectedFailures.has(`${response.status()} ${pathname}`)) errors.push(`${response.status()} ${pathname}`)
  })
  page.on('console', message => {
    if (message.type() !== 'error') return
    const pathname = message.location().url ? new URL(message.location().url).pathname : ''
    if (![...expectedFailures].some(value => value.endsWith(' ' + pathname))) errors.push(message.text())
  })
  const go = async (id = 101) => {
    await page.goto(base + '/m/' + id)
    await page.getByRole('heading', { name: new RegExp('^' + movie(id).title) }).waitFor()
    await page.locator('.similar-card').first().waitFor()
    await page.waitForLoadState('networkidle')
  }
  const next = async () => {
    await page.locator('.similar-card').filter({ hasText: movies[1].title }).click()
    await page.waitForURL('**/m/102')
    await page.getByRole('heading', { name: new RegExp('^' + movies[1].title) }).waitFor()
  }
  const batchCount = () => requests.filter(request => request.method === 'POST' && request.path === '/api/movies/batch').length

  await go()
  const saved = hold('POST', '/api/movies/batch'), count = batchCount()
  await page.getByRole('button', { name: '标记已看', exact: true }).evaluate(element => { element.click(); element.click() })
  await wait(() => saved.seen)
  assert.equal(batchCount(), count + 1, 'An immediate repeated action must send only one write')
  assert.equal(await page.getByRole('button', { name: '标记已看', exact: true }).isDisabled(), true)
  saved.send()
  await page.getByRole('button', { name: '标记未看', exact: true }).waitFor()
  await page.getByText('已保存', { exact: true }).waitFor()
  console.log('PASS 观看状态单次提交与正常写后刷新')

  const oldRead = hold('GET', '/api/movies/101')
  await page.getByRole('button', { name: '标记未看', exact: true }).click()
  await wait(() => oldRead.seen)
  await next()
  oldRead.send(200, { ...movie(101), title: '不应覆盖新片的迟回资料' })
  await page.waitForLoadState('networkidle')
  assert.match(await page.locator('h1').innerText(), new RegExp(movies[1].title))
  assert.equal(await page.getByText('不应覆盖新片的迟回资料', { exact: true }).count(), 0)
  assert.equal(await page.getByText('已保存', { exact: true }).count(), 0)
  console.log('PASS 写后旧 GET 迟回不能覆盖同组件下一部影片')

  remember(101, { needs_review: true }); remember(102, { needs_review: true })
  await go()
  const oldConfirm = hold('POST', '/api/movies/101/confirm-match')
  await page.getByRole('button', { name: '匹配正确', exact: true }).click()
  await wait(() => oldConfirm.seen)
  await next()
  const newConfirm = hold('POST', '/api/movies/102/confirm-match')
  await page.getByRole('button', { name: '匹配正确', exact: true }).click()
  await wait(() => newConfirm.seen)
  expectedFailures.add('503 /api/movies/101/confirm-match')
  const rejected = page.waitForResponse(response => response.status() === 503 && new URL(response.url()).pathname === '/api/movies/101/confirm-match')
  oldConfirm.send(503, { detail: '上一影片的模拟保存错误' })
  await rejected
  assert.equal(await page.getByRole('button', { name: '匹配正确', exact: true }).isDisabled(), true)
  assert.equal(await page.getByText(/上一影片的模拟保存错误/).count(), 0)
  newConfirm.send()
  await page.getByText('已保存', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '匹配正确', exact: true }).count(), 0)
  console.log('PASS 旧影片写入错误不覆盖新片反馈或释放新片忙碌状态')

  await go()
  const leaving = hold('POST', '/api/movies/batch')
  await page.getByRole('button', { name: '标记已看', exact: true }).click()
  await wait(() => leaving.seen)
  await page.locator('.back-link').click()
  await page.waitForURL(url => url.pathname === '/')
  await page.getByRole('heading', { name: '电影', exact: true }).waitFor()
  const detailReads = requests.filter(request => request.method === 'GET' && request.path === '/api/movies/101').length
  const completed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/movies/batch')
  leaving.send()
  await completed
  await page.waitForLoadState('networkidle')
  assert.equal(requests.filter(request => request.method === 'GET' && request.path === '/api/movies/101').length, detailReads)
  assert.ok(requests.every(request => !request.path.includes('undefined')))
  console.log('PASS 离开详情后已提交写入继续完成，页面停止后续读取')

  remember(101, { watched: 0 })
  await page.setViewportSize({ width: 390, height: 844 })
  await go()
  await page.getByRole('button', { name: '标记已看', exact: true }).click()
  await page.getByRole('button', { name: '标记未看', exact: true }).waitFor()
  await page.locator('img[title="查看大图"]').click()
  await page.getByRole('dialog').waitFor()
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Movie detail must fit a 390px viewport')
  assert.deepEqual([...unexpected, ...fixture.state.unexpected], [], 'Every API and external request must be explicitly isolated')
  assert.deepEqual(errors, [], 'Unexpected browser or fixture errors')
  console.log('PASS 390px 正常保存、海报开关与布局；全部请求为隔离模拟')
} catch (error) {
  const report = path.join(project, 'output/playwright/movie-context')
  await mkdir(report, { recursive: true })
  await writeFile(path.join(report, 'failure.json'), JSON.stringify({ mocked: true, message: String(error),
    unexpected: [...unexpected, ...fixture.state.unexpected], errors, requests }, null, 2) + '\n')
  if (page && !page.isClosed()) {
    try { await page.screenshot({ path: path.join(report, 'failure.png'), fullPage: true }) } catch { /* Preserve the original failure. */ }
  }
  throw error
} finally {
  await browser?.close()
  // Resolve held mocks before closing the isolated listener, including failure paths.
  for (const pending of holds) pending.send?.()
  if (server) await new Promise(resolve => server.close(resolve))
  await rm(temporary, { recursive: true, force: true })
}
