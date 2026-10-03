// Real Vue pages, synthetic posters, and an isolated in-memory API. No backend or real media.
// Run: node scripts/smoke_browse_filters.mjs
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { art, createFixture, facets, libraries, movies, shows } from './fixtures/uiReviewData.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const temp = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-filter-ui-'))
const dist = path.join(temp, 'dist'), output = path.join(root, 'output/playwright/browse-filters')
const fixture = createFixture(), requests = [], unexpected = [], external = [], results = []
const libraryItems = [...libraries, ...libraries.map(item => ({ ...item, id: item.id + 10, media_library_id: 2, media_name: '卧室影库' }))]
const longFacets = {
  ...facets,
  regions: [...facets.regions, { value: '欧美', count: 2 }],
  countries: [...facets.countries, { code: 'JP', name: '日本', count: 2 }, ...Array.from({ length: 13 }, (_, i) => ({ code: 'X' + i, name: '模拟国家 ' + i, count: 1 }))],
  years: Array.from({ length: 16 }, (_, i) => ({ value: 2010 + i, count: 1 })),
  tags: [...facets.tags, ...Array.from({ length: 13 }, (_, i) => ({ value: '模拟标签 ' + i, count: 1 }))],
}
let server, browser
const listRequests = (kind = 'movie') => requests.filter(r => r.path === (kind === 'tv' ? '/api/tv/shows' : '/api/search'))
const listCount = kind => listRequests(kind).length
function matching(items, params, tv) {
  const some = (key, values) => !params.has(key) || params.getAll(key).some(value => values.includes(value))
  return items.filter(item => {
    if (params.has('q') && !item.title.includes(params.get('q'))) return false
    if (!some('genre', item.genres || []) || !some('region', [item.region])) return false
    if (!some('country', item.origin_countries || ['CN'])) return false
    if (!some('year', [String(item.year)]) || !some('decade', [String(Math.floor(item.year / 10) * 10)])) return false
    if (!params.getAll('tag').every(tag => (item.tags || []).includes(tag))) return false
    if (params.has('watched')) {
      const watched = tv ? item.watched_count === item.episode_count : !!item.watched
      if (watched !== (params.get('watched') === '1')) return false
    }
    if (params.has('min_rating')) {
      const key = ({ tmdb: 'tmdb_rating', douban: 'douban_rating', custom: 'custom_rating' })[params.get('rating_source') || 'tmdb']
      if (item[key] == null || item[key] < Number(params.get('min_rating'))) return false
    }
    if (tv && !some('status', [item.status === 'Ended' ? 'ended' : 'continuing'])) return false
    return true
  })
}
function mockApi(url, method, body) {
  const key = url.pathname
  requests.push({ path: key, method, query: Object.fromEntries(url.searchParams), entries: [...url.searchParams] })
  assert.ok(method === 'GET' || method === 'POST' && key === '/api/stream/versions', 'Filter browsing must not write to the API')
  if (key === '/api/libraries') return { items: libraryItems, default_id: 1 }
  if (key === '/api/media-libraries') return { items: [1, 2].map(id => ({ id, name: id === 1 ? '客厅影库' : '卧室影库', source: 'local', enabled: true, read_only: true, movie_count: 13, episode_count: 96, video_libraries: libraryItems.filter(item => item.media_library_id === id) })), smb_driver: 'direct' }
  if (key === '/api/facets' || key === '/api/tv/facets') return { ...longFacets, ...(url.searchParams.get('media_library') === '2' ? { genres: [{ value: '科幻', count: 2 }, { value: '剧情', count: 3 }] } : {}) }
  if (key === '/api/search' || key === '/api/tv/shows') {
    const items = matching(key === '/api/search' ? movies : shows, url.searchParams, key === '/api/tv/shows')
    return { items, total: items.length, has_more: false }
  }
  const value = fixture.api(url, method, body)
  if (value === undefined) unexpected.push(method + ' ' + key)
  return value
}
try {
  await mkdir(output, { recursive: true })
  await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(temp, 'cache'), plugins: [vue()], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (key.startsWith('/posters/') || /\/(backdrop|still)$/.test(key)) { res.writeHead(200, { 'Content-Type': 'image/svg+xml' }); res.end(art(Number(key.match(/-(\d+)/)?.[1] || 0), /backdrop|still/.test(key))); return }
      if (key.startsWith('/api/')) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString()
        const data = mockApi(url, req.method, raw ? JSON.parse(raw) : undefined)
        res.writeHead(data === undefined ? 501 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data ?? { detail: 'Unmocked isolated API' })); return
      }
      const file = /^\/assets\/[\w.-]+$/.test(key) || /^\/(favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key) ? path.join(dist, key) : /^\/(?:tv(?:\/\d+)?|m\/\d+)?\/?$/.test(key) ? path.join(dist, 'index.html') : null
      if (!file) { res.writeHead(404); res.end('Only isolated routes are served'); return }
      const content = await readFile(file)
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' }); res.end(content)
    } catch (error) { unexpected.push(String(error)); if (!res.headersSent) res.writeHead(500); res.end('Isolated fixture failure') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 375, height: 812 }]) {
    const context = await browser.newContext({ viewport, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await context.route('**/*', route => { if (new URL(route.request().url()).origin !== base) { external.push(route.request().url()); return route.abort() } return route.continue() })
    const page = await context.newPage(), errors = [], checks = []
    page.setDefaultTimeout(8000)
    page.on('pageerror', error => errors.push(String(error)))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    const pass = name => { checks.push(name); console.log(`PASS ${viewport.width}px ${name}`) }
    const go = async route => { await page.goto(base + route); await page.getByRole('navigation', { name: '主导航' }).waitFor(); await page.waitForLoadState('networkidle') }
    const dialog = () => page.getByRole('dialog')
    const choice = (field, value) => dialog().locator(`[data-filter-field="${field}"][data-filter-value="${value}"]`)
    const open = async key => { await page.locator(`[data-filter-trigger="${key}"]`).click(); await dialog().waitFor() }
    const expand = async key => { const button = dialog().locator(`[data-filter-group="${key}"]`); if (await button.getAttribute('aria-expanded') !== 'true') await button.click() }
    const apply = async (all = false) => {
      const endpoint = new URL(page.url()).pathname === '/tv' ? '/api/tv/shows' : '/api/search'
      const response = page.waitForResponse(res => new URL(res.url()).pathname === endpoint)
      await dialog().getByRole('button', { name: all ? '应用筛选' : '应用', exact: true }).click()
      await (await response).finished(); await dialog().waitFor({ state: 'hidden' })
      await page.waitForFunction(() => document.querySelector('.grid')?.getAttribute('aria-busy') === 'false')
    }
    const cancel = async () => { await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'hidden' }) }
    const summary = () => page.getByRole('group', { name: '已生效的搜索与筛选' })
    const query = () => new URL(page.url()).searchParams
    const remove = async label => { await summary().getByRole('button', { name: '移除筛选：' + label, exact: true }).click(); await page.waitForLoadState('networkidle') }
    const snap = async name => { await page.screenshot({ path: path.join(output, `${name}-${viewport.width}.png`), animations: 'disabled' }) }
    const fit = async () => {
      const metrics = await page.evaluate(() => {
        const el = document.querySelector('[role="dialog"]'), rect = el?.getBoundingClientRect(), footer = el?.querySelector('.jz-dialog-footer')?.getBoundingClientRect()
        return { overflow: document.documentElement.scrollWidth > innerWidth + 1, rect: rect ? { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, width: rect.width } : null, footer: footer?.bottom, focusInside: !!el?.contains(document.activeElement) }
      })
      assert.equal(metrics.overflow, false, 'Page must not overflow horizontally')
      if (metrics.rect) {
        assert.ok(metrics.rect.x >= -1 && metrics.rect.y >= -1 && metrics.rect.right <= viewport.width + 1 && metrics.rect.bottom <= viewport.height + 1, 'Filter overlay must fit viewport: ' + JSON.stringify(metrics))
        assert.ok(metrics.footer <= viewport.height + 1, 'Apply footer remains visible')
        assert.equal(metrics.focusInside, true, 'Open filter owns keyboard focus')
      }
      return metrics
    }
    try {
      await go('/?media=1&q=' + encodeURIComponent('远山') + '&sort=year&order=asc')
      await fit(); await snap('default')
      await open('genres')
      const before = listCount()
      await choice('genres', '剧情').click()
      await page.waitForTimeout(100)
      assert.equal(listCount(), before, 'Draft selection must not request results')
      assert.equal(query().has('genre'), false, 'Draft must not change URL')
      await fit(); await snap('quick')
      await cancel()
      assert.equal(await page.locator('[data-filter-trigger="genres"]').evaluate(el => el === document.activeElement), true, 'Escape restores trigger focus')
      await open('genres')
      assert.equal(await choice('genres', '剧情').getAttribute('aria-pressed'), 'false', 'Escape discards draft')
      await choice('genres', '剧情').click()
      await page.locator('.jz-dialog-mask').click({ position: { x: 5, y: 5 } })
      await dialog().waitFor({ state: 'hidden' })
      assert.equal(listCount(), before, 'Backdrop also cancels without requesting results')
      await open('genres')
      assert.equal(await choice('genres', '剧情').getAttribute('aria-pressed'), 'false')
      await choice('genres', '剧情').click(); await apply()
      assert.equal(query().get('genre'), '剧情')
      assert.equal(listRequests().at(-1).query.genre, '剧情')
      assert.equal(listRequests().at(-1).query.q, '远山')
      await snap('applied'); pass('quick draft, Apply, Escape and focus return')

      await open('all')
      const resetBefore = listCount()
      await dialog().getByRole('button', { name: '重置', exact: true }).click()
      assert.equal(query().get('genre'), '剧情')
      assert.equal(listCount(), resetBefore, 'Reset only changes draft')
      await cancel()
      assert.equal(await summary().getByRole('button', { name: '移除筛选：剧情', exact: true }).count(), 1)
      await open('all')
      await dialog().getByRole('button', { name: '重置', exact: true }).click(); await apply(true)
      assert.equal(query().has('genre'), false)
      assert.equal(query().get('q'), '远山')
      assert.equal(query().get('sort'), 'year')
      assert.equal(query().get('order'), 'asc')
      pass('drawer Reset cancels or applies while preserving query and sort')

      await open('genres'); await choice('genres', '剧情').click(); await apply()
      await remove('剧情')
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '清除搜索')
      assert.equal(query().has('genre'), false)
      assert.equal(query().get('q'), '远山')
      await open('genres'); await choice('genres', '剧情').click(); await apply()
      await summary().getByRole('button', { name: '清空筛选', exact: true }).click(); await page.waitForLoadState('networkidle')
      assert.equal(query().has('genre'), false); assert.equal(query().get('q'), '远山'); assert.equal(query().get('sort'), 'year')
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '清除搜索')
      await summary().getByRole('button', { name: '清除搜索', exact: true }).click()
      await page.waitForURL(url => !url.searchParams.has('q'))
      await page.waitForFunction(() => document.activeElement?.id === 'movie-browse-input')
      assert.equal(await summary().count(), 0)
      pass('chip removal, clear and focus recovery preserve search and sorting')

      await go('/?media=1&region=' + encodeURIComponent('华语') + '&country=CN')
      assert.equal(listRequests().at(-1).query.region, undefined)
      assert.equal(await summary().getByRole('button', { name: '移除筛选：华语', exact: true }).count(), 0)
      await remove('中国')
      assert.equal(query().has('region'), false)
      assert.equal(listRequests().at(-1).query.region, undefined, 'Removing country cannot revive hidden region')
      await open('all'); await expand('location')
      await dialog().getByRole('button', { name: '按国家', exact: true }).click()
      await dialog().getByLabel('搜索国家或地区', { exact: true }).fill('日本')
      await choice('countries', 'JP').click(); await apply(true)
      assert.equal(query().get('country'), 'JP'); assert.equal(query().has('region'), false)
      await page.getByText('没有符合条件的影片', { exact: true }).waitFor()
      pass('country precedence, searchable options and real no-results state')

      await go('/?media=1')
      await open('all'); await expand('period')
      await choice('decades', '2010').click()
      await dialog().getByLabel('搜索年份', { exact: true }).fill('2023')
      await choice('years', '2023').click()
      await dialog().getByText(/同时满足|交集/).first().waitFor()
      await apply(true)
      assert.equal(listRequests().at(-1).query.decade, '2010')
      assert.equal(listRequests().at(-1).query.year, '2023')
      await page.getByText('没有符合条件的影片', { exact: true }).waitFor()
      pass('decade and year retain intersection semantics')

      await go('/?media=1&genre=' + encodeURIComponent('自定义类型') + '&min_rating=7.5&rating_source=douban')
      await open('all')
      assert.equal(await choice('genres', '自定义类型').getAttribute('aria-pressed'), 'true', 'URL selections absent from facets stay removable')
      await expand('rating')
      assert.equal(await choice('rating', '7.5').getAttribute('aria-pressed'), 'true', 'An exact URL or AI threshold is not rounded to a preset')
      assert.equal(await dialog().getByRole('button', { name: '豆瓣', exact: true }).getAttribute('aria-pressed'), 'true')
      await cancel(); await remove('自定义类型')
      assert.equal(query().get('min_rating'), '7.5'); assert.equal(query().get('rating_source'), 'douban')
      pass('missing facet selections and exact rating thresholds survive URL restoration')

      await go('/?media=1')
      await open('all'); await expand('tags')
      await dialog().getByLabel('搜索标签', { exact: true }).fill('周末')
      await choice('tags', '周末片单').click()
      await dialog().getByLabel('搜索标签', { exact: true }).fill('值得')
      await choice('tags', '值得重看').click()
      await apply(true)
      assert.deepEqual(listRequests().at(-1).entries.filter(([key]) => key === 'tag').map(([, value]) => value).sort(), ['值得重看', '周末片单'])
      pass('tag group search preserves multi-selection and API values')

      await open('all')
      await expand('rating')
      await fit(); await snap('drawer')
      for (const key of ['Tab', 'Shift+Tab']) {
        for (let i = 0; i < 22; i++) {
          await page.keyboard.press(key)
          assert.equal(await dialog().evaluate(el => el.contains(document.activeElement)), true, key + ' stays within dialog for a full cycle')
        }
      }
      await cancel()
      assert.equal(await page.locator('[data-filter-trigger="all"]').evaluate(el => el === document.activeElement), true)
      pass('drawer viewport, sticky footer and keyboard containment')

      await go('/?media=1')
      await open('genres'); await choice('genres', '悬疑').click()
      // Simulate a library change from another app control while a draft is open.
      const scopeResponse = page.waitForResponse(res => new URL(res.url()).pathname === '/api/search' && new URL(res.url()).searchParams.get('media_library') === '2')
      await page.getByRole('combobox', { name: '切换媒体库', exact: true }).selectOption('2', { force: true })
      await (await scopeResponse).finished(); await dialog().waitFor({ state: 'hidden' }); await page.waitForLoadState('networkidle')
      assert.equal(listRequests().at(-1).query.media_library, '2')
      assert.equal(listRequests().at(-1).query.genre, undefined)
      await open('genres')
      await choice('genres', '科幻').waitFor()
      assert.equal(await choice('genres', '科幻').getAttribute('aria-pressed'), 'false')
      await cancel(); pass('library change discards old draft and refreshes options')

      await go('/?media=1&genre=' + encodeURIComponent('剧情'))
      await page.locator('.grid > .card').first().click(); await page.waitForURL(/\/m\/101/)
      await page.locator('.movie-detail').waitFor(); await page.waitForLoadState('networkidle')
      await page.goBack(); await page.waitForLoadState('networkidle')
      assert.equal(query().get('genre'), '剧情')
      assert.equal(await summary().getByRole('button', { name: '移除筛选：剧情', exact: true }).count(), 1)
      assert.equal(await dialog().count(), 0, 'Back navigation restores applied filters, not an old overlay')
      pass('detail return restores filter URL and applied summary')

      await go('/tv?media=1')
      await open('all'); await expand('status')
      await choice('status', 'ended').click()
      await expand('watched')
      await choice('watched', '0').click()
      await expand('rating')
      assert.equal(await dialog().getByRole('button', { name: '豆瓣', exact: true }).count(), 0, 'TV cannot offer unsupported Douban rating')
      await fit(); await snap('tv-drawer')
      const tvBefore = listCount('tv')
      assert.equal(query().has('status'), false)
      await apply(true)
      assert.equal(listCount('tv'), tvBefore + 1, 'TV Apply performs one new result request')
      assert.equal(listRequests('tv').at(-1).query.status, 'ended')
      assert.equal(listRequests('tv').at(-1).query.watched, '0')
      assert.equal(await page.locator('.grid > .card').count(), 3)
      await page.reload(); await page.waitForLoadState('networkidle')
      assert.equal(query().get('status'), 'ended'); assert.equal(query().get('watched'), '0')
      await fit(); pass('TV status, watched semantics, supported rating sources and reload')
      assert.deepEqual(errors, [], 'Browser errors must be empty')
      results.push({ viewport, passed: true, checks, errors })
    } catch (error) {
      await snap('failure').catch(() => {})
      results.push({ viewport, passed: false, checks, errors, failure: String(error) })
      console.error(`FAIL ${viewport.width}px ${error.stack}`)
    } finally { await context.close() }
  }
  await writeFile(path.join(output, 'checks.json'), JSON.stringify({ all_api_mocked: true, real_media_accessed: false, results, unexpected, external }, null, 2) + '\n')
  assert.deepEqual(unexpected, [], 'Every API must be explicitly mocked')
  assert.deepEqual(external, [], 'No external connections permitted')
  assert.equal(results.filter(result => !result.passed).length, 0, 'Browse filters smoke failed; see output/playwright/browse-filters/checks.json')
  console.log(`Browse filter checks passed at ${results.length} viewports. Artifacts: ${output}`)
} finally {
  if (browser) await browser.close()
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) }
  await rm(temp, { recursive: true, force: true })
}
