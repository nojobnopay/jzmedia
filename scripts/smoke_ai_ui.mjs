// Run: node scripts/smoke_ai_ui.mjs [--capture-docs | --demo]
// Build the real Vue pages into /tmp, serve local static assets and mock every API.
// --capture-docs also saves documented screenshots; --demo keeps the loopback UI open.
// No backend, .env, data directory, NAS, or model service is opened. Vite config/env
// loading is disabled, and a restrictive CSP blocks external requests in ordinary browsers.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createServer as createHttpServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const project = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
assert.ok(args.every(arg => ['--capture-docs', '--demo'].includes(arg)), 'Usage: node scripts/smoke_ai_ui.mjs [--capture-docs | --demo]')
assert.ok(args.length <= 1, 'Choose one mode per run')
const capture = args.includes('--capture-docs'), demo = args.includes('--demo')
const simulatedConnection = '模拟响应：未连接 OpenCode Go 或其他模型服务'
const desktop = { width: 1440, height: 1000 }
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
let httpServer, browser, page
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
    return response(route, { ok: true, code: 'ok', message: simulatedConnection, usage: settings.usage })
  }
  if (key === '/api/ai/search') {
    if (body.q === '旧库晚到' && delayedResolve) { delayedSearch = route; delayedResolve(); return }
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
  if (/^\/api\/libraries\/\d+$/.test(key) && method === 'PATCH') {
    const library = libraries.find(item => item.id === Number(key.split('/').at(-1)))
    assert.ok(library, 'Mock update must name an existing fixture library')
    assert.deepEqual(Object.keys(body), ['metadata_providers'])
    library.metadata_providers = body.metadata_providers
    return response(route, library)
  }
  if (key === '/api/metadata/test-search') {
    const library = libraries.find(item => item.id === Number(url.searchParams.get('library')))
    assert.ok(library, 'Mock search must name an existing fixture library')
    const items = url.searchParams.get('q') === '无结果' ? [] : [{ title: '模拟匹配候选', year: 2020, source: 'local' }]
    return response(route, { items, source: items.length ? 'local' : null, kind: library.kind,
      library_id: library.id, chain: JSON.parse(library.metadata_providers), elapsed_ms: 12 })
  }
  if (key === '/api/onboarding') return response(route, { show_welcome: false, status: 'completed' })
  if (key === '/api/settings') return response(route, { tmdb_configured: false, tmdb_language: 'zh-CN', libraries: [] })
  if (key === '/api/jobs/stats') return response(route, { total: 1, by_library: [] })
  if (key === '/api/tv/stats') return response(route, { shows: 1, seasons: 0, episodes: 0, by_library: [] })
  if (key === '/api/metadata/providers') return response(route, { providers: [] })
  if (key === '/api/health') return response(route, { ffmpeg: true })
  if (key === '/api/facets' || key === '/api/tv/facets') return response(route, facets)
  if (key === '/api/search') return response(route, list([movie]))
  if (key === '/api/tv/shows') return response(route, list([show]))
  if (key === '/api/collections/suggest') return response(route, { items: [], topups: [], coverage: { unchecked: 0, standalone: 0 } })
  if (key === '/api/collections/suggest/backfill/status') return response(route, { state: 'idle', done: 0, total: 0, failed: [] })
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

// HTTP mocks also serve ordinary browsers in --demo. No request is proxied.
async function serveMock(req, res, base) {
  const pathname = new URL(req.url, base).pathname
  if (pathname.startsWith('/posters/')) {
    res.writeHead(200, { 'Content-Type': 'image/svg+xml' })
    res.end('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="30"/>')
    return true
  }
  if (!pathname.startsWith('/api/')) return false
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > 65536) { res.writeHead(413); res.end(); return true }
    chunks.push(chunk)
  }
  const body = Buffer.concat(chunks).toString('utf8')
  const adapter = {
    request: () => ({ url: () => new URL(req.url, base).href, method: () => req.method,
      postData: () => body, postDataJSON: () => JSON.parse(body) }),
    fulfill: async ({ status, contentType, body: result }) => {
      if (!res.destroyed) { res.writeHead(status, { 'Content-Type': contentType }); res.end(result) }
    },
  }
  await mockApi(adapter)
  return true
}

async function ffmpegBinary() {
  try { execFileSync('ffmpeg', ['-version'], { stdio: 'ignore' }); return 'ffmpeg' } catch { /* Try the existing app dependency next. */ }
  const lib = path.join(project, '.venv/lib')
  for (const python of await readdir(lib)) {
    const binaries = path.join(lib, python, 'site-packages/static_ffmpeg/bin')
    let platforms
    try { platforms = await readdir(binaries) } catch { continue }
    for (const platform of platforms) {
      const binary = path.join(binaries, platform, 'ffmpeg')
      try { execFileSync(binary, ['-version'], { stdio: 'ignore' }); return binary } catch { /* Try another installed platform. */ }
    }
  }
  throw new Error('Install FFmpeg or prepare the existing static-ffmpeg app dependency before --capture-docs')
}

async function captureDocs(go) {
  const binary = await ffmpegBinary(), assets = path.join(project, 'docs/assets')
  await mkdir(path.join(assets, 'screenshots'), { recursive: true })
  const appVersion = JSON.parse(await readFile(path.join(project, 'frontend/package.json'), 'utf8')).version
  const commit = execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: project, encoding: 'utf8' }).trim()
  const manifestFile = path.join(assets, 'manifest.json')
  const manifest = JSON.parse(await readFile(manifestFile, 'utf8'))
  const entries = []
  async function screenshot(name, doc, scene, target) {
    await target.evaluate(element => window.scrollTo(0, Math.max(0, element.getBoundingClientRect().top + window.scrollY - 90)))
    await page.evaluate(() => document.fonts.ready)
    const file = `screenshots/${name}.webp`, png = path.join(temporary, `${name}.png`)
    await page.screenshot({ path: png, animations: 'disabled' })
    execFileSync(binary, ['-hide_banner', '-loglevel', 'error', '-y', '-i', png, '-quality', '86', path.join(assets, file)])
    const bytes = await readFile(path.join(assets, file))
    entries.push({ file, page: doc, scene, verified_at: new Date().toISOString().slice(0, 10),
      app_version: appVersion, source_commit: commit, source_state: '当前工作区 OpenCode Go 与智能辅助界面',
      source: '真实 Vue 界面 + 全 API 模拟；无真实 Key、数据库或媒体；不证明 Go 连通或影视效果',
      viewport: { width: desktop.width, height: desktop.height, device_scale_factor: 1 },
      recording_script: 'scripts/smoke_ai_ui.mjs --capture-docs', fixtures: 'scripts/smoke_ai_ui.mjs 内置虚构资料与模拟响应',
      format: 'WebP q86', bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') })
    console.log('CAPTURE ' + file)
  }
  await page.setViewportSize(desktop)
  Object.assign(settings, { provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash',
    enabled: false, api_key_set: false, api_key_source: 'unset', api_key_masked: '',
    usage: { date: new Date().toISOString().slice(0, 10), requests: 0, input_tokens: 0, output_tokens: 0 } })
  Object.assign(movie, { title: '模拟电影', tmdb_id: null })
  Object.assign(show, { title: '模拟剧集', tmdb_id: null, poster_path: null })
  await go('/settings?sec=sec-ai')
  const config = page.locator('.ai-settings')
  await config.getByRole('button', { name: '配置与测试服务' }).click()
  await config.getByLabel(/^服务商/).selectOption('opencode_go')
  assert.equal(await config.getByLabel('API 基础地址', { exact: true }).inputValue(), 'https://opencode.ai/zen/go/v1')
  assert.equal(await config.getByLabel('模型名称', { exact: true }).inputValue(), 'glm-5.3-flash')
  await config.getByText(/OpenCode Go 官方面向编码代理/).waitFor()
  await config.getByLabel('API Key', { exact: true }).fill('mock-docs-not-a-real-key-demo')
  await config.getByLabel('启用智能辅助', { exact: true }).check()
  await config.getByRole('button', { name: '保存智能辅助配置' }).click()
  await config.getByText('配置已保存，智能辅助已开启', { exact: true }).waitFor()
  await config.getByRole('button', { name: '测试已保存连接' }).click()
  await config.getByText(simulatedConnection, { exact: true }).waitFor()
  await screenshot('ai-settings-go', 'user-guide/settings.md', 'OpenCode Go 预设、用途限制与明确标注的模拟测试响应；密钥是虚构值', config)
  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  const search = page.getByRole('region', { name: '智能搜索', exact: true })
  await search.getByLabel('想看什么').fill('没看过的 90 年代香港喜剧，TMDB 7 分以上')
  await search.getByRole('button', { name: '解析条件', exact: true }).click()
  await search.getByRole('button', { name: '确认应用条件' }).waitFor()
  await screenshot('ai-search-preview', 'user-guide/find-movies.md', '模拟解析结果可编辑；确认前未应用到当前库', search)
  for (const kind of ['movie', 'tv']) {
    if (kind === 'tv') show.tmdb_id = 777 // A directory-locked show can only refresh its current TMDB identity.
    await go(kind === 'movie' ? '/m/101' : '/tv/201')
    if (kind === 'tv') {
      await page.locator('summary').filter({ hasText: '更多操作' }).click()
      await page.getByRole('button', { name: '重新匹配剧集', exact: true }).click()
    }
    const suggestions = page.getByRole('region', { name: 'AI 匹配建议', exact: true })
    await suggestions.getByRole('button', { name: 'AI 匹配建议', exact: true }).click()
    await suggestions.getByRole('button', { name: '选择此候选', exact: true }).click()
    await suggestions.getByRole('button', { name: '确认绑定此候选' }).waitFor()
    await screenshot(`ai-match-${kind}`, kind === 'tv' ? 'user-guide/tv-matching.md' : 'user-guide/metadata.md',
      kind === 'tv' ? '模拟整剧建议与二次确认；展示目录归属限制' : '模拟电影候选与二次确认；未执行绑定', suggestions)
  }
  assert.deepEqual(unexpected, [], 'Capture must remain entirely mocked')
  assert.deepEqual(errors, [], 'Capture browser errors')
  // Preserve every historic batch field and asset; new entries carry their own provenance.
  const captured = new Set(entries.map(entry => entry.file))
  manifest.assets = [...manifest.assets.filter(entry => !captured.has(entry.file)), ...entries]
  await writeFile(manifestFile, JSON.stringify(manifest, null, 2) + '\n')
  console.log('Updated AI entries only: docs/assets/manifest.json; raw PNGs: ' + temporary)
}

try {
  const demoLabel = { name: 'isolated-demo-label', transformIndexHtml: () => [{ tag: 'div',
    attrs: { id: 'isolated-demo-label', style: 'position:fixed;bottom:0;left:0;right:0;z-index:99999;padding:9px 16px;background:#25384a;color:#e5efff;text-align:center;font:14px/1.4 sans-serif;border-top:1px solid #698baa;pointer-events:none' },
    children: '隔离演示 · 真实界面 + 模拟 API · 无真实 Key · 不代表 OpenCode Go 连通或影视效果', injectTo: 'body' }] }
  const output = path.join(temporary, 'frontend')
  // Use a fresh production build so ordinary demo browsers need no HMR/WebSocket connection.
  await build({ root: path.join(project, 'frontend'), configFile: false, envFile: false,
    cacheDir: path.join(temporary, 'vite-cache'), plugins: [vue(), ...(capture || demo ? [demoLabel] : [])], worker: { format: 'es' },
    logLevel: 'error', build: { outDir: output, emptyOutDir: true, reportCompressedSize: false } })
  httpServer = createHttpServer(async (req, res) => {
    res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
    try {
      const base = `http://127.0.0.1:${httpServer.address().port}`
      if (await serveMock(req, res, base)) return
      const pathname = decodeURIComponent(new URL(req.url, base).pathname)
      let file
      if (/^\/assets\/[\w.-]+$/.test(pathname)) file = path.join(output, pathname)
      else if (['/favicon.svg', '/favicon.ico', '/favicon-16x16.png', '/favicon-32x32.png',
        '/apple-touch-icon.png', '/logo.svg', '/icon-512.png'].includes(pathname)) file = path.join(output, pathname)
      else if (/^\/(?:settings|tv(?:\/\d+)?|m\/\d+)?\/?$/.test(pathname)) file = path.join(output, 'index.html')
      else { res.writeHead(404); res.end('This isolated demo only serves its UI fixtures'); return }
      const contentType = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
        '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.woff2': 'font/woff2' }[path.extname(file)] || 'application/octet-stream'
      try { const content = await readFile(file); res.writeHead(200, { 'Content-Type': contentType }); res.end(content) }
      catch { res.writeHead(404); res.end('Missing fixture asset') }
    } catch (error) { console.error('Mock request failed:', error.message); res.writeHead(500); res.end('Isolated mock failed') }
  })
  await new Promise((resolve, reject) => {
    httpServer.once('error', reject)
    httpServer.listen(0, '127.0.0.1', resolve)
  })
  const base = `http://127.0.0.1:${httpServer.address().port}`
  if (demo) {
    Object.assign(settings, { enabled: true, provider: 'opencode_go', base_url: 'https://opencode.ai/zen/go/v1',
      model: 'glm-5.3-flash', api_key_set: true, api_key_source: 'db', api_key_masked: '****demo' })
    show.tmdb_id = 777
    console.log(`隔离演示（全 API 模拟，无真实 Key）：${base}/settings?sec=sec-ai`)
    console.log(`搜索：${base}/?media=1\n电影匹配：${base}/m/101\n剧集匹配：${base}/tv/201`)
    console.log('不加载 .env，不连接后端、NAS 或模型；仅演示这些页面。Ctrl+C 停止并丢弃内存状态。')
    await new Promise(resolve => { process.once('SIGINT', resolve); process.once('SIGTERM', resolve) })
  } else {
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: desktop, serviceWorkers: 'block' })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin !== base) { unexpected.push('External request blocked: ' + url.origin); return route.abort() }
    return route.continue()
  })
  page = await context.newPage()
  page.setDefaultTimeout(15000)
  page.on('pageerror', e => errors.push(String(e)))
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${new URL(r.url()).pathname}`) })
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  const go = async pathname => {
    await page.goto(base + pathname)
    await page.getByRole('combobox', { name: '切换媒体库' }).waitFor()
  }

  await go('/settings?sec=sec-ai')
  const config = page.locator('.ai-settings')
  await config.getByRole('button', { name: '配置与测试服务' }).click()
  await config.getByRole('button', { name: '保存智能辅助配置' }).waitFor()
  const desktopGroups = await page.locator('.desktop-categories > section').evaluateAll(groups => groups.map(group => {
    const box = group.getBoundingClientRect(), heading = group.querySelector('.nav-group').getBoundingClientRect()
    return { x: box.x, y: box.y, width: box.width, height: heading.height }
  }))
  assert.equal(desktopGroups.length, 3)
  assert.ok(desktopGroups.every((group, index) => group.width >= 150 && group.height < 35
    && (!index || (group.y > desktopGroups[index - 1].y && group.x === desktopGroups[index - 1].x))),
  'Desktop navigation groups must stack vertically with single-line headings')
  assert.equal(paid().length, 0, 'Opening settings must not make a paid call')
  await config.getByLabel('API Key', { exact: true }).fill('mock-secret-never-a-real-key')
  await config.getByLabel('启用智能辅助', { exact: true }).check()
  assert.equal(await config.getByRole('button', { name: '测试已保存连接' }).isDisabled(), true)
  await config.getByRole('button', { name: '保存智能辅助配置' }).click()
  await config.getByText('配置已保存，智能辅助已开启', { exact: true }).waitFor()
  assert.equal(await config.getByLabel('API Key', { exact: true }).inputValue(), '')
  assert.equal(paid().length, 0, 'Saving configuration must not test the model implicitly')
  await config.getByRole('button', { name: '测试已保存连接' }).click()
  await config.getByText(simulatedConnection, { exact: true }).waitFor()
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
  await page.getByRole('combobox', { name: '搜索电影', exact: true }).fill('普通查找')
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

  await go('/settings?sec=sec-matching')
  const rules = page.locator('#sec-matching')
  await rules.getByLabel('视频库', { exact: true }).selectOption('1')
  await rules.getByLabel('TMDB', { exact: true }).check()
  await rules.getByRole('button', { name: '上移 TMDB', exact: true }).click()
  await rules.getByLabel('视频库', { exact: true }).selectOption('2')
  await rules.getByLabel('TVmaze', { exact: true }).check()
  await rules.getByLabel('视频库', { exact: true }).selectOption('1')
  assert.match(await rules.getByRole('list', { name: '已启用来源的匹配顺序' }).innerText(), /^1\. TMDB/)
  await rules.getByRole('button', { name: '保存匹配规则', exact: true }).click()
  await rules.getByText('已保存：TMDB → 本地索引', { exact: true }).waitFor()
  await rules.getByLabel('电影名称', { exact: true }).fill('无结果')
  await rules.getByRole('button', { name: '测试已保存规则', exact: true }).click()
  await rules.getByText(/未找到候选/).waitFor()
  await rules.getByLabel('视频库', { exact: true }).selectOption('2')
  assert.equal(await rules.getByLabel('TVmaze', { exact: true }).isChecked(), true)
  await rules.getByRole('button', { name: '保存匹配规则', exact: true }).click()
  await rules.getByText('已保存：本地索引 → TVmaze', { exact: true }).waitFor()
  await rules.getByLabel('剧集名称', { exact: true }).fill('模拟剧集')
  await rules.getByRole('button', { name: '测试已保存规则', exact: true }).click()
  await rules.getByText(/找到 1 个候选/).waitFor()
  const matchingRequest = requests.filter(r => r.path === '/api/metadata/test-search').at(-1)
  assert.equal(matchingRequest.query.library, '2')
  assert.equal(matchingRequest.query.kind, 'tv')
  console.log('PASS 设置匹配规则按库保留草稿、优先级保存、电影空结果和剧集作用域测试')

  await page.setViewportSize({ width: 390, height: 844 })
  await go('/settings?sec=sec-ai')
  await config.getByRole('button', { name: '保存智能辅助配置' }).waitFor()
  await config.scrollIntoViewIfNeeded()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Settings overflow on a 390px viewport')
  await page.getByLabel('设置分类', { exact: true }).selectOption('sec-offline')
  await page.locator('.settings-heading h2').filter({ hasText: '离线资料' }).waitFor()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Settings category selector must fit the viewport')
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
  if (capture) await captureDocs(go)
  }
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
  // Keep failure screenshots for inspection; generated Vite state is disposable.
  await rm(path.join(temporary, 'vite-cache'), { recursive: true, force: true })
  await rm(path.join(temporary, 'frontend'), { recursive: true, force: true })
}
