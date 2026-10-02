// Run: node scripts/smoke_ai_ui.mjs
// Browser integration smoke using the real Vue pages and entirely mocked APIs.
// No backend, .env, data directory, NAS, or model service is opened. Vite's config
// and env-file loading are disabled; every API request and external URL is routed.
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createHttpServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const project = fileURLToPath(new URL('../', import.meta.url))
const temporary = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-ai-ui-'))
const libraries = [
  { id: 1, kind: 'movie', name: '电影', media_library_id: 1, media_name: '模拟媒体库甲' },
  { id: 2, kind: 'tv', name: '剧集', media_library_id: 1, media_name: '模拟媒体库甲' },
  { id: 3, kind: 'movie', name: '电影', media_library_id: 2, media_name: '模拟媒体库乙' },
  { id: 4, kind: 'tv', name: '剧集', media_library_id: 2, media_name: '模拟媒体库乙' },
].map(lib => ({ ...lib, source: 'local', enabled: true, effective_enabled: true,
  media_enabled: true, read_only: true, metadata_providers: '["local"]' }))
const facets = {
  genres: [{ value: '喜剧', count: 1 }, { value: '剧情', count: 1 }],
  regions: [{ value: '华语', count: 1 }], countries: [{ code: 'HK', name: '香港', count: 1 }],
  years: [{ value: 1995, count: 1 }], decades: [{ value: 1990, count: 1 }],
  tags: [], collections: [], watched: { watched: 0, unwatched: 1 },
  ratings: { tmdb: [{ min: 7, count: 1 }], douban: [], custom: [] },
  status: [{ value: 'ended', count: 1 }],
}
const movie = { id: 101, title: '模拟电影', year: 1995, tmdb_id: null,
  library_id: 1, media_library_id: 1, file_path: '模拟电影.mkv', genres: ['喜剧'],
  tags: [], persons: [], collections: [], versions: [], watched: 0,
  origin_countries: ['HK'], overview_display: '仅用于浏览器模拟验收。' }
const show = { id: 201, title: '模拟剧集', year: 2020, tmdb_id: null,
  library_id: 2, media_library_id: 1, genres: ['剧情'], tags: [], seasons: [],
  episodes: [], cast: [], extras: [], watched_count: 0, episode_count: 0,
  overview: '仅用于浏览器模拟验收。' }
const settings = { enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com',
  model: 'simulated-model', timeout_seconds: 12, daily_limit: 100,
  api_key_set: false, api_key_source: 'unset', api_key_masked: '',
  usage: { date: '2026-10-02', requests: 0, input_tokens: 0, output_tokens: 0 } }
const requests = [], unexpected = [], errors = []
let searchFailure = null, matchFailure = null, delayedSearch = null
let delayedResolve
let server, httpServer, browser, page
const list = items => ({ items, has_more: false })
const response = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
const paid = () => requests.filter(r => /^\/api\/ai\/(check|search|match)$/.test(r.path))
const binds = () => requests.filter(r => r.method === 'POST' && /\/(?:match|bind-external)$/.test(r.path) && !r.path.startsWith('/api/ai/'))
const proposal = (kind, q = '') => ({ ok: true, query: q, summary: '模拟解析：请核对条件', warnings: [],
  filters: { q: '', genre: [kind === 'tv' ? '剧情' : '喜剧'], country: ['HK'], decade: [1990],
    watched: 0, min_rating: 7, rating_source: 'tmdb', sort: 'rating', order: 'desc',
    ...(kind === 'tv' ? { status: ['ended'] } : {}) } })

async function mockApi(route) {
  const request = route.request(), url = new URL(request.url())
  const method = request.method(), body = request.postData() ? request.postDataJSON() : undefined
  const key = url.pathname
  requests.push({ path: key, method, body, query: Object.fromEntries(url.searchParams) })
  if (key === '/api/ai/settings') {
    if (method === 'PATCH') {
      const { api_key: secret, clear_api_key: clear, ...config } = body
      Object.assign(settings, config)
      if (secret) Object.assign(settings, { api_key_set: true, api_key_masked: '****' + secret.slice(-4), api_key_source: 'db' })
      if (clear) Object.assign(settings, { api_key_set: true, api_key_masked: '****nkey', api_key_source: 'env' })
    }
    return response(route, settings)
  }
  if (key === '/api/ai/check') {
    settings.usage.requests++
    return response(route, { ok: true, code: 'ok', message: '模拟连接成功', usage: settings.usage })
  }
  if (key === '/api/ai/search') {
    if (body.q === '旧库晚到') { delayedSearch = route; delayedResolve(); return }
    if (searchFailure) return response(route, { ok: false, code: searchFailure, message: '模拟智能搜索不可用' })
    return response(route, proposal(body.kind, body.q))
  }
  if (key === '/api/ai/match') {
    if (matchFailure) return response(route, { ok: false, code: matchFailure, message: '模拟匹配服务超时' })
    return response(route, { ok: true, query: '模拟候选', summary: '模拟建议，请核对真实内容', warnings: [],
      candidates: [{ title: body.kind === 'tv' ? '模拟候选剧' : '模拟候选电影', year: 1995,
        tmdb_id: body.kind === 'tv' ? 777 : 555, source: 'tmdb', bindable: true, reason: '模拟标题线索，需人工确认。' },
      { title: '仅索引候选', year: 1995, source: 'nfo', source_id: 'fixture-only', bindable: false, reason: '没有可绑定详情。' },
      ...(body.kind === 'tv' ? [{ title: '目录规则限制候选', tmdb_id: 999, source: 'tmdb', bindable: false,
        bind_reason: '已有目录归属，请通过“归属与季号”预览并调整', reason: '须保留已有目录归属。' }] : [])] })
  }
  if (key === '/api/libraries') return response(route, { items: libraries, default_id: 1 })
  if (key === '/api/onboarding') return response(route, { show_welcome: false, status: 'completed' })
  if (key === '/api/settings') return response(route, { tmdb_configured: false, tmdb_language: 'zh-CN', libraries: [] })
  if (key === '/api/jobs/stats') return response(route, { total: 1, by_library: [] })
  if (key === '/api/tv/stats') return response(route, { shows: 1, seasons: 0, episodes: 0, by_library: [] })
  if (key === '/api/metadata/providers') return response(route, { providers: [] })
  if (key === '/api/health') return response(route, { ffmpeg: true })
  if (key === '/api/facets' || key === '/api/tv/facets') return response(route, facets)
  if (key === '/api/search') return response(route, list([movie]))
  if (key === '/api/tv/shows') return response(route, list([show]))
  if (key === '/api/collections' || /\/(recent-played|similar)$/.test(key)) return response(route, list([]))
  if (key === '/api/search/suggest' || key === '/api/tv/suggest') return response(route, { items: [], persons: [] })
  if (key === '/api/movies/101') return response(route, movie)
  if (key === '/api/tv/shows/201') return response(route, show)
  if (/\/(collection-hint|organize-hint)$/.test(key)) return response(route, { needs: false })
  if (key === '/api/movies/101/files') return response(route, { items: [] })
  if (key === '/api/stream/versions') return response(route, { versions: [], best_version_id: 101 })
  if (key === '/api/stream/progress') return response(route, { position: 0 })
  if (key === '/api/tmdb/search' || key === '/api/tv/search') return response(route, list([
    { title: '普通搜索候选', name: '普通搜索候选', year: 1995, tmdb_id: 123, source: 'tmdb' },
  ]))
  if (key === '/api/movies/101/match') {
    movie.tmdb_id = body.tmdb_id
    movie.title = '模拟候选电影'
    return response(route, { ok: true, background: {} })
  }
  if (key === '/api/tv/shows/201/match') {
    show.tmdb_id = body.tmdb_id
    show.title = '模拟候选剧'
    show.poster_path = 'fixture.svg'
    return response(route, { ok: true, media: { artwork: { ok: true }, nfo: { ok: true } } })
  }
  if (/\/backdrop$/.test(key)) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>' })
  unexpected.push(`${method} ${key}`)
  return route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Unmocked API in isolated smoke"}' })
}

try {
  server = await createServer({ root: path.join(project, 'frontend'), configFile: false, envFile: false,
    cacheDir: path.join(temporary, 'vite-cache'), plugins: [vue()], worker: { format: 'es' },
    logLevel: 'error', server: { middlewareMode: true, hmr: false, proxy: {} } })
  httpServer = createHttpServer(server.middlewares)
  await new Promise((resolve, reject) => {
    httpServer.once('error', reject)
    httpServer.listen(0, '127.0.0.1', resolve)
  })
  const base = `http://127.0.0.1:${httpServer.address().port}`
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin !== base) { unexpected.push('External request blocked: ' + url.origin); return route.abort() }
    if (url.pathname.startsWith('/api/')) return mockApi(route)
    if (url.pathname.startsWith('/posters/')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="30"/>' })
    return route.continue()
  })
  page = await context.newPage()
  page.setDefaultTimeout(15000)
  page.on('pageerror', e => errors.push(String(e)))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  const go = async pathname => {
    await page.goto(base + pathname)
    await page.getByRole('combobox', { name: '切换媒体库' }).waitFor()
  }

  await go('/settings?sec=sec-tmdb')
  const config = page.locator('.ai-settings')
  await config.getByRole('button', { name: '保存智能辅助配置' }).waitFor()
  assert.equal(paid().length, 0, 'Opening settings must not make a paid call')
  await config.getByLabel('API Key', { exact: true }).fill('mock-secret-never-a-real-key')
  await config.getByLabel('启用智能辅助', { exact: true }).check()
  assert.equal(await config.getByRole('button', { name: '测试已保存连接' }).isDisabled(), true)
  await config.getByRole('button', { name: '保存智能辅助配置' }).click()
  await config.getByText('配置已保存，智能辅助已开启', { exact: true }).waitFor()
  assert.equal(await config.getByLabel('API Key', { exact: true }).inputValue(), '')
  assert.equal(paid().length, 0, 'Saving configuration must not test the model implicitly')
  await config.getByRole('button', { name: '测试已保存连接' }).click()
  await config.getByText('模拟连接成功', { exact: true }).waitFor()
  assert.equal(paid().length, 1)
  const writesBeforeClear = requests.filter(r => r.path === '/api/ai/settings' && r.method === 'PATCH').length
  await config.getByRole('button', { name: '移除已保存密钥', exact: true }).click()
  assert.equal(requests.filter(r => r.path === '/api/ai/settings' && r.method === 'PATCH').length, writesBeforeClear)
  await config.getByRole('button', { name: '确认移除已保存密钥', exact: true }).click()
  await config.getByText('已移除设置页密钥，现使用服务器环境密钥', { exact: true }).waitFor()
  console.log('PASS 模拟设置保存、独立连接测试、密钥确认移除和环境回退')

  for (const kind of ['movie', 'tv']) {
    await go(kind === 'movie' ? '/?media=1' : '/tv?media=1')
    await page.getByRole('button', { name: '智能搜索', exact: true }).click()
    const panel = page.getByRole('region', { name: '智能搜索', exact: true })
    await panel.getByLabel('想看什么').fill('未看的90年代香港作品，7分以上')
    const count = paid().length
    await panel.getByRole('button', { name: '解析条件', exact: true }).click()
    await panel.getByRole('button', { name: '确认应用条件' }).waitFor()
    assert.equal(paid().length, count + 1)
    assert.equal(new URL(page.url()).searchParams.has('min_rating'), false, 'Preview cannot apply filters')
    await panel.getByLabel('最低评分', { exact: true }).fill('8')
    const endpoint = kind === 'movie' ? '/api/search' : '/api/tv/shows'
    const applied = page.waitForRequest(r => {
      const url = new URL(r.url())
      return url.pathname === endpoint && url.searchParams.get('min_rating') === '8'
    })
    await panel.getByRole('button', { name: '确认应用条件' }).click()
    const actual = new URL((await applied).url())
    assert.equal(actual.searchParams.get('media_library'), '1')
    assert.equal(actual.searchParams.get('country'), 'HK')
    assert.equal(actual.searchParams.get('watched'), '0')
    if (kind === 'tv') assert.equal(actual.searchParams.get('status'), 'ended')
    await panel.waitFor({ state: 'hidden' })
  }
  console.log('PASS 电影与剧集搜索先预览、可修改、确认后复用原查询并保留库范围')

  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  const panel = page.getByRole('region', { name: '智能搜索', exact: true })
  await panel.getByLabel('想看什么').fill('旧库晚到')
  const delayedSeen = new Promise(resolve => { delayedResolve = resolve })
  await panel.getByRole('button', { name: '解析条件', exact: true }).click()
  await delayedSeen
  await page.getByRole('combobox', { name: '切换媒体库' }).selectOption('2')
  await page.waitForURL(url => url.searchParams.get('media') === '2')
  await response(delayedSearch, { ...proposal('movie'), summary: '过期建议不应出现' })
  delayedSearch = null
  await page.waitForFunction(() => document.querySelector('[aria-label="切换媒体库"]')?.value === '2')
  assert.equal(await page.getByText('过期建议不应出现', { exact: true }).count(), 0)
  assert.equal(await page.getByRole('button', { name: '确认应用条件' }).count(), 0)
  await panel.getByLabel('想看什么').fill('新库搜索')
  await panel.getByRole('button', { name: '解析条件', exact: true }).click()
  await panel.getByRole('button', { name: '确认应用条件' }).waitFor()
  assert.equal(paid().at(-1).body.media_library_id, 2)
  console.log('PASS 切库取消旧解析，晚到建议不能覆盖新范围')

  searchFailure = 'disabled'
  await panel.getByLabel('想看什么').fill('停用后的请求')
  await panel.getByRole('button', { name: '解析条件', exact: true }).click()
  await panel.getByText(/模拟智能搜索不可用/).waitFor()
  assert.equal(await panel.getByRole('button', { name: '确认应用条件' }).count(), 0)
  const localSearch = page.waitForRequest(r => new URL(r.url()).pathname === '/api/search' && new URL(r.url()).searchParams.get('q') === '普通查找')
  await page.getByRole('textbox', { name: '搜索电影', exact: true }).fill('普通查找')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await localSearch
  searchFailure = null
  console.log('PASS 智能搜索停用或失败时原搜索仍可用')

  for (const kind of ['movie', 'tv']) {
    await go(kind === 'movie' ? '/m/101' : '/tv/201')
    const suggestions = page.getByRole('region', { name: 'AI 匹配建议', exact: true })
    await suggestions.getByRole('button', { name: 'AI 匹配建议', exact: true }).waitFor()
    matchFailure = 'timeout'
    await suggestions.getByRole('button', { name: 'AI 匹配建议', exact: true }).click()
    await suggestions.getByText(/模拟匹配服务超时/).waitFor()
    await (kind === 'movie'
      ? page.getByRole('textbox', { name: '搜索匹配', exact: true })
      : page.getByPlaceholder('输入剧名', { exact: true })).fill('普通搜索')
    const lookup = kind === 'movie' ? '/api/tmdb/search' : '/api/tv/search'
    const normal = page.waitForRequest(r => new URL(r.url()).pathname === lookup)
    await (kind === 'movie'
      ? page.getByRole('button', { name: '搜索匹配', exact: true })
      : page.locator('.match.card-block').getByRole('button', { name: '搜索', exact: true })).click()
    await normal
    matchFailure = null
    const before = binds().length
    await suggestions.getByRole('button', { name: 'AI 匹配建议', exact: true }).click()
    await suggestions.getByRole('button', { name: '选择此候选' }).waitFor()
    assert.equal(binds().length, before)
    assert.equal(await suggestions.getByRole('button', { name: '选择此候选' }).count(), 1, 'Index-only candidates cannot bind')
    if (kind === 'tv') await suggestions.getByText('已有目录归属，请通过“归属与季号”预览并调整', { exact: true }).waitFor()
    await suggestions.getByRole('button', { name: '选择此候选' }).click()
    await suggestions.getByRole('button', { name: '确认绑定此候选' }).waitFor()
    assert.equal(binds().length, before, 'Selection alone must not bind')
    const bindPath = kind === 'movie' ? '/api/movies/101/match' : '/api/tv/shows/201/match'
    const bound = page.waitForRequest(r => new URL(r.url()).pathname === bindPath)
    await suggestions.getByRole('button', { name: '确认绑定此候选' }).click()
    assert.deepEqual((await bound).postDataJSON(), { tmdb_id: kind === 'movie' ? 555 : 777 })
  }
  console.log('PASS 电影与剧集匹配先选择再确认、仅绑定真实候选，失败仍可手动搜索')

  await page.setViewportSize({ width: 390, height: 844 })
  await go('/settings?sec=sec-tmdb')
  await config.getByRole('button', { name: '保存智能辅助配置' }).waitFor()
  await config.scrollIntoViewIfNeeded()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Settings overflow on a 390px viewport')
  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  await page.getByRole('region', { name: '智能搜索', exact: true }).getByLabel('想看什么').fill('手机搜索')
  await page.getByRole('button', { name: '解析条件', exact: true }).click()
  await page.getByRole('button', { name: '确认应用条件' }).waitFor()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'AI search overflow on a 390px viewport')
  assert.deepEqual(unexpected, [], 'All requests must remain in explicitly mocked APIs and local frontend assets')
  assert.deepEqual(errors, [], 'Browser runtime or console errors')
  assert.equal(requests.some(r => r.method !== 'GET' && /organize|scan|delete/.test(r.path)), false)
  console.log('PASS 390px 布局与运行时；所有 API/模型响应均为模拟，未访问真实后端、媒体或云服务')
} catch (error) {
  if (page && !page.isClosed()) {
    const screenshot = path.join(temporary, 'failure.png')
    try {
      await page.screenshot({ path: screenshot, fullPage: true })
      console.error('Failure screenshot (isolated fixture only): ' + screenshot)
    } catch (captureError) { console.error('Could not capture fixture screenshot:', captureError.message) }
  }
  if (unexpected.length) console.error('Unmocked requests:', unexpected)
  if (errors.length) console.error('Browser errors:', errors)
  throw error
} finally {
  await browser?.close()
  if (httpServer) await new Promise(resolve => httpServer.close(resolve))
  await server?.close()
  // Keep failure screenshots for inspection; generated Vite state is disposable.
  await rm(path.join(temporary, 'vite-cache'), { recursive: true, force: true })
}
