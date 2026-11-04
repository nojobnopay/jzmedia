// Real Vue/Router with an isolated, progressive directory. No external source or media.
// Run: node scripts/smoke_online_search.mjs
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { createFixture } from './fixtures/uiReviewData.mjs'
import { waitUntil } from './online_fixture/browser.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-search-ui-'))
const dist = path.join(work, 'dist'), output = path.join(root, 'output/playwright/online-search')
const sourceId = 'src_' + '1'.repeat(32), otherId = 'src_' + '2'.repeat(32)
const source = { id: sourceId, name: '隔离搜索目录', provider: 'maccms', configuration_token: 'b'.repeat(64),
  priority: 0, capabilities: { browse: true, search: true, year_filter: true, search_filters: true } }
const other = { ...source, id: otherId, name: '旧接口目录', configuration_token: 'c'.repeat(64),
  capabilities: { ...source.capabilities, search_filters: false } }
const categories = [{ id: '1', name: '电影片', parent_id: '' }, { id: '2', name: '连续剧', parent_id: '' },
  { id: '11', name: '剧情片', parent_id: '1' }, { id: '12', name: '冒险片', parent_id: '1' },
  { id: '21', name: '国产剧', parent_id: '2' }, { id: '22', name: '欧美剧', parent_id: '2' }]
const catalogue = Array.from({ length: 420 }, (_, index) => {
  const category = ['11', '21', '12', '22'][index % 4]
  return { external_id: 'synthetic-' + index, title: `雨中放映室 ${String(index + 1).padStart(3, '0')}`,
    kind: category.startsWith('2') ? 'tv' : 'movie', category_id: category,
    parent_category_id: category.startsWith('2') ? '2' : '1',
    category: categories.find(row => row.id === category).name,
    year: index >= 300 ? '1988' : index % 3 ? '2026' : '2025', poster_path: '', description: '隔离测试中的虚构条目。' }
})
const fixture = createFixture(), sessions = new Map(), requests = [], errors = [], external = [], checks = []
let serial = 0, delayMore = 0, server, browser
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
function selection(params) {
  const values = params instanceof URLSearchParams ? Object.fromEntries(params) : params || {}
  return { category_id: '', category_scope: 'exact', year: '', offset: 0, limit: 20, ...values }
}
function result(session, requested) {
  const filter = selection(requested), offset = Number(filter.offset), limit = Number(filter.limit)
  const matches = catalogue.slice(0, session.scanned).filter(row => (!filter.category_id ||
    row.category_id === filter.category_id || filter.category_scope === 'descendants' && row.parent_category_id === filter.category_id) &&
    (!filter.year || row.year.startsWith(filter.year)))
  const complete = session.scanned === catalogue.length
  return { session_id: session.id, version: session.scanned, source, configuration_token: source.configuration_token,
    query: session.query, status: complete ? 'complete' : session.loading ? 'loading' : 'partial',
    items: matches.slice(offset, offset + limit), categories, scanned: session.scanned, found: matches.length,
    total: complete ? matches.length : null, has_more: offset + limit < matches.length || !complete,
    can_continue: !complete, error_code: '', validated_at: session.created }
}
async function api(url, method, body, response) {
  const key = url.pathname
  requests.push({ path: key, method, params: Object.fromEntries(url.searchParams), body })
  if (key === '/api/online/catalog/sources') return { enabled: true, scope_token: 'a'.repeat(64), items: [source, other] }
  if (key === '/api/online/catalog/health') return { purposes: { api: { state: 'success', mode: 'direct' }, media: { state: 'unknown', mode: 'direct' } } }
  if (key === '/api/online/catalog/browse' || key === '/api/online/catalog/search') {
    return { source, categories, items: catalogue.slice(0, 20), page: 1, page_count: 1, total: 20 }
  }
  if (key === '/api/online/state/library') return { items: [], total: 0, offset: 0, limit: 20, scope_token: 'a'.repeat(64) }
  if (key === '/api/online/catalog/search-sessions') {
    const session = { id: 'srch_' + (++serial).toString(16).padStart(32, '0'), query: body.q.trim(), scanned: 100, loading: false, created: Date.now() }
    sessions.set(session.id, session)
    return result(session, body)
  }
  const sessionPath = /^\/api\/online\/catalog\/search-sessions\/(srch_[a-f0-9]{32})(\/more)?$/.exec(key)
  if (sessionPath) {
    const session = sessions.get(sessionPath[1]); assert.ok(session, 'Known isolated search session')
    if (sessionPath[2]) {
      session.loading = true
      await delay(delayMore || 250)
      if (!response.destroyed) session.scanned = Math.min(catalogue.length, session.scanned + 100)
      session.loading = false
    }
    return result(session, body || url.searchParams)
  }
  const data = fixture.api(url, method, body)
  if (data === undefined) throw new Error(`Unmocked isolated API: ${method} ${key}`)
  return data
}
try {
  await mkdir(output, { recursive: true })
  await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    cacheDir: path.join(work, 'cache'), plugins: [vue()], worker: { format: 'es' }, logLevel: 'error',
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://127.0.0.1')
      if (url.pathname.startsWith('/api/')) {
        const chunks = []; for await (const chunk of request) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString()
        const value = await api(url, request.method, raw ? JSON.parse(raw) : null, response)
        if (!response.destroyed) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)) }
        return
      }
      const file = /^\/assets\/[\w.-]+$/.test(url.pathname) ? path.join(dist, url.pathname)
        : /\.(?:svg|png|ico)$/.test(url.pathname) ? path.join(dist, path.basename(url.pathname)) : path.join(dist, 'index.html')
      const content = await readFile(file)
      response.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' })
      response.end(content)
    } catch (error) { errors.push(String(error)); if (!response.headersSent) response.writeHead(500); response.end('Isolated fixture error') }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: width > 700 ? 1000 : 844 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { external.push(route.request().url()); return route.abort() }
      return route.continue()
    })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    page.on('pageerror', error => errors.push(String(error)))
    const pass = name => { checks.push({ width, name }); console.log(`PASS ${width}px ${name}`) }
    const idle = () => page.waitForFunction(() => document.querySelector('[data-online-browser]')?.getAttribute('aria-busy') === 'false')
    const more = () => page.locator('[data-online-search-more]')
    try {
      await page.goto(base + '/online'); await idle()
      const initialMore = requests.filter(row => row.path.endsWith('/more')).length
      await page.locator('#online-query').fill('雨中放映室')
      await page.locator('#online-query').press('Enter'); await idle()
      assert.equal(await page.locator('[data-online-result]').count(), 20)
      assert.equal(requests.filter(row => row.path.endsWith('/more')).length, initialMore)
      assert.ok((await page.locator('[data-online-browser]').innerText()).includes('尚未检索完'))
      pass('first search renders one window without traversing results')
      const callsBefore = requests.filter(row => row.path.endsWith('/more')).length
      await page.locator('[data-online-channel="2"]').click(); await idle()
      assert.equal(await page.locator('#online-query').inputValue(), '雨中放映室')
      assert.equal(requests.filter(row => row.path.endsWith('/more')).length, callsBefore)
      assert.ok((await page.locator('[data-online-browser]').innerText()).includes('国产剧'))
      assert.ok((await page.locator('[data-online-browser]').innerText()).includes('欧美剧'))
      pass('parent filter includes children and reuses collected data')
      await page.locator('#online-query').fill('尚未提交的另一个片名')
      await page.locator('[data-online-year]').selectOption('2025'); await idle()
      const lastRead = requests.filter(row => row.path.includes('/search-sessions/')).at(-1)
      assert.equal(sessions.get(lastRead.path.split('/')[5]).query, '雨中放映室')
      await page.locator('[data-online-year]').selectOption('custom')
      await page.locator('[data-online-year-custom]').fill('1988')
      await page.locator('[data-online-year-form]').getByRole('button', { name: '应用年份' }).click(); await idle()
      assert.equal(await page.locator('[data-online-result]').count(), 0)
      assert.equal(await page.getByText('没有找到相关内容', { exact: true }).count(), 0)
      assert.ok(await more().isVisible())
      await page.screenshot({ path: path.join(output, `partial-empty-${width}.png`), animations: 'disabled' })
      pass('unsubmitted text is isolated and incomplete zero matches are not final')
      await more().click(); await idle()
      if (await page.locator('[data-online-result]').count() === 0) { await more().click(); await idle() }
      assert.ok(await page.locator('[data-online-result]').count() > 0)
      const titles = await page.locator('[data-online-result]').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')))
      assert.equal(new Set(titles).size, titles.length)
      assert.ok((await page.locator('[data-online-browser]').innerText()).includes('1988'))
      pass('explicit continuation discovers later-page matches without duplicates')
      await page.screenshot({ path: path.join(output, `filtered-${width}.png`), animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      if (await more().count()) {
        // Consume another cached window first. The following action must have
        // an actual upstream batch in flight, rather than a cache-only GET.
        await more().click(); await idle()
        const beforeBatch = requests.filter(row => row.path.endsWith('/more')).length
        delayMore = 3500
        const started = page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/more'))
        await more().click()
        const pending = await started
        const interrupted = sessions.get(new URL(pending.url()).pathname.split('/')[5])
        assert.ok(interrupted)
        await waitUntil(() => requests.filter(row => row.path.endsWith('/more')).length === beforeBatch + 1 && interrupted.loading,
          'A real continuation must be active before navigating away', { timeout: 2000 })
        const scannedBeforeCancel = interrupted.scanned
        await page.locator('[data-online-channel=""]').click()
        await idle()
        await delay(3800)
        assert.equal(requests.filter(row => row.path.endsWith('/more')).length, beforeBatch + 1)
        assert.equal(interrupted.scanned, scannedBeforeCancel, 'Cancelled batch must not append a late source page')
        assert.equal(await page.locator('[data-online-search-progress]').count(), 0)
        pass('navigation cancels unfinished continuation')
      }
      delayMore = 0
    } catch (error) {
      await page.screenshot({ path: path.join(output, `failure-${width}.png`), animations: 'disabled' }).catch(() => {})
      await writeFile(path.join(output, 'failure.json'), JSON.stringify({ width, error: String(error), text: await page.locator('body').innerText(), requests, errors }, null, 2))
      throw error
    } finally { await context.close() }
  }
  assert.deepEqual(errors, [])
  assert.deepEqual(external, [])
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ checks, requests, errors, external }, null, 2) + '\n')
} finally {
  await browser?.close()
  await new Promise(resolve => { if (!server) return resolve(); server.close(resolve); server.closeAllConnections() })
  await rm(work, { recursive: true, force: true })
}
