// Real Vue pages with synthetic artwork and in-memory TV metadata only.
// node scripts/smoke_tv_airing.mjs [--capture-docs]
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { createFixture, episodes, libraries, shows, tvCatalogFixture, tvCollectionFixture } from './fixtures/uiReviewData.mjs'

const root = fileURLToPath(new URL('../', import.meta.url)), args = process.argv.slice(2)
assert.ok(args.every(arg => arg === '--capture-docs'), 'Only --capture-docs is supported')
const capture = args.includes('--capture-docs'), fixture = createFixture()
const temp = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-tv-airing-ui-'))
const dist = path.join(temp, 'dist'), output = path.join(root, 'output/playwright/tv-airing')
const requests = [], unexpected = [], external = [], results = [], captures = []
let server, browser, errorCode = '', catalogError = '', collectionScenario = '', eventGeneration = 1, checkedAt = 1790985600, maintenanceRemaining = 0
const demoLibraries = [...libraries, { ...libraries[1], id: 4, name: '修复版' }]
const sourceShow = { ...shows[0], id: 901, library_id: 4, library_name: '修复版' }
const localEpisodes = episodes.slice(0, 2).map(episode => ({ ...episode, watched: 0, progress: null }))
function collection(id) {
  const data = tvCollectionFixture(201)
  data.show_id = id; data.checked_at = checkedAt; data.error = errorCode
  data.missing_seasons = [2]
  data.latest_episode = { season: 1, episode: 8, title: '我们终将再次相遇', air_date: '2026-10-02', airing_state: 'aired', collection_state: 'uncollected' }
  data.seasons = data.seasons.map(season => ({ ...season,
    collected_count: season.season === 1 ? 3 : 0, local_count: season.season === 1 ? 3 : 0,
    collection_state: season.season === 1 ? 'collected' : 'uncollected',
    sources: season.season === 1 ? [{ show_id: 201, library_id: 2, library_name: '剧集', season: 1, count: 2 },
      { show_id: 901, library_id: 4, library_name: '修复版', season: 1, count: 1 }] : [],
  }))
  if (collectionScenario) {
    const number = collectionScenario === 'source_only' ? 2 : 1
    const season = data.seasons.find(item => item.season === number)
    const reason = collectionScenario === 'show_review' ? 'show_unconfirmed'
      : collectionScenario === 'episode_review' ? 'match_review'
        : collectionScenario === 'partial_coverage' ? 'numbering_unresolved' : 'catalog_missing'
    Object.assign(season, { collection_state: 'uncertain', collection_reason: reason,
      local_count: number === 1 ? 3 : 0, collected_count: number === 1 ? 1 : 2 })
    if (number === 2) season.sources = [{ show_id: 901, library_id: 4, library_name: '修复版', season: 1, count: 2 }]
    data.missing_seasons = data.missing_seasons.filter(value => value !== number)
    if (number === 1) Object.assign(data.latest_episode, { collection_state: 'uncertain', collection_reason: reason })
    if (collectionScenario === 'partial_coverage') {
      Object.assign(data.seasons.find(item => item.season === 2), { collection_state: 'uncertain', collection_reason: 'coverage_incomplete' })
      data.missing_seasons = []
    }
    if (collectionScenario === 'show_review') {
      data.confirmed = false; data.collection_reason = 'show_unconfirmed'; data.missing_seasons = []
      data.seasons.forEach(item => Object.assign(item, { collection_state: 'uncertain', collection_reason: 'show_unconfirmed' }))
    }
  }
  return data
}
function catalog(id, season) {
  const data = tvCatalogFixture(201, season)
  if (collectionScenario === 'source_only' && season === 2) return { ...data,
    ...collection(id).seasons.find(item => item.season === season), show_id: id,
    items: [], checked_at: 0, stale: true, refreshing: false, error: '' }
  return { ...data, ...collection(id).seasons.find(item => item.season === season), show_id: id,
    stale: !!catalogError, error: catalogError, items: data.items.map((episode, index) => ({ ...episode,
      collection_state: season === 1 && index < 3 ? 'collected' : 'uncollected',
      sources: season === 1 && index < 3 ? [{ show_id: index === 2 ? 901 : 201,
        library_id: index === 2 ? 4 : 2, library_name: index === 2 ? '修复版' : '剧集',
        season: 1, episode_id: index === 2 ? 603 : 401 + index, episode: index + 1 }] : [],
    })) }
}
function status(running = false) {
  return { configured: true, total: 6, checked: 6, pending: running ? 1 : 0, failed: errorCode ? 1 : 0,
    running, current: running ? 9001 : 0, last_checked_at: checkedAt, next_check_at: checkedAt + 604800, error: errorCode }
}
function mockApi(url, method, body) {
  const key = url.pathname
  requests.push({ path: key, method, body, query: Object.fromEntries(url.searchParams) })
  assert.ok(method === 'GET' || method === 'POST' && key === '/api/tv/airing/check', 'This smoke must not start playback or mutate media')
  if (key === '/api/libraries') return { items: demoLibraries, default_id: 1 }
  if (key === '/api/media-libraries') return { items: [{ id: 1, name: '客厅影库', source: 'local', enabled: true, read_only: true, video_libraries: demoLibraries }], smb_driver: 'direct' }
  if (key === '/api/tv/updates') return { media_library_id: 1, items: [{ ...shows[0], show_id: 201,
    events: [{ event_id: 'demo-new-' + eventGeneration, tmdb_episode_id: 9800 + eventGeneration, season: 1, episode: 8,
      title: '我们终将再次相遇', air_date: '2026-10-02' }] }], error: '' }
  if (key === '/api/tv/airing/check') { checkedAt++; errorCode = ''; maintenanceRemaining = body?.show_id ? 0 : 1; return status(!!maintenanceRemaining) }
  if (key === '/api/tv/airing/status') { const running = maintenanceRemaining-- > 0; return status(running) }
  let match = key.match(/^\/api\/tv\/shows\/(\d+)\/collection$/)
  if (match) return collection(Number(match[1]))
  match = key.match(/^\/api\/tv\/shows\/(\d+)\/seasons\/(\d+)\/catalog$/)
  if (match) return catalog(Number(match[1]), Number(match[2]))
  if (key === '/api/tv/shows/201' || key === '/api/tv/shows/901') {
    const show = key.endsWith('/901') ? sourceShow : shows[0]
    return { ...show, needs_review: collectionScenario === 'show_review' ? 1 : 0,
      episode_count: 2, watched_count: 0, next_episode: null, episodes: localEpisodes,
      seasons: [1, 2].map(season => ({ season, name: `第 ${season} 季`, poster_path: `posters/show-0-s${season}.svg`,
        total: season === 1 ? 2 : 8, distinct: season === 1 ? 2 : 0, watched_count: 0, versions: season === 1 ? 1 : 0 })) }
  }
  match = key.match(/^\/api\/tv\/shows\/(\d+)\/seasons\/([012])$/)
  if (match) {
    const id = Number(match[1]), season = Number(match[2]), alternate = id === 901
    const seasonEpisodes = season === 1 ? (alternate ? [{ ...episodes[2], id: 603, show_id: 901 }] : localEpisodes) : []
    return { show_id: id, show_title: shows[0].title, show_year: 2023, show_backdrop_path: shows[0].backdrop_path,
      season, name: season === 0 ? '特别篇（SP）' : `第 ${season} 季`, poster_path: `posters/show-0-s${season}.svg`, overview: shows[0].overview,
      episode_count: seasonEpisodes.length, distinct_count: seasonEpisodes.length, watched_count: 0,
      total: seasonEpisodes.length, episodes: seasonEpisodes, has_more: false, next_episode: null, cast: [],
      versions: seasonEpisodes.length ? [{ version: 1, distinct: seasonEpisodes.length, count: seasonEpisodes.length }] : [] }
  }
  if (key === '/api/tv/episodes/603') return { ...episodes[2], id: 603, show_id: 901, library_id: 4, progress: null }
  const data = fixture.api(url, method, body)
  if (data === undefined) unexpected.push(method + ' ' + key)
  return data
}
async function ffmpegBinary() {
  try { execFileSync('ffmpeg', ['-version'], { stdio: 'ignore' }); return 'ffmpeg' } catch { /* Existing isolated dependency follows. */ }
  for (const python of await readdir(path.join(root, '.venv/lib'))) {
    const directory = path.join(root, '.venv/lib', python, 'site-packages/static_ffmpeg/bin')
    for (const platform of await readdir(directory).catch(() => [])) {
      const binary = path.join(directory, platform, 'ffmpeg')
      try { execFileSync(binary, ['-version'], { stdio: 'ignore' }); return binary } catch { /* Another installed platform. */ }
    }
  }
  throw new Error('Documentation capture needs the existing FFmpeg dependency')
}
async function captureDocs() {
  const binary = await ffmpegBinary(), assets = path.join(root, 'docs/assets')
  const manifestPath = path.join(assets, 'manifest.json'), manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const version = JSON.parse(await readFile(path.join(root, 'frontend/package.json'), 'utf8')).version
  const commit = execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim()
  const entries = []
  for (const entry of captures) {
    const file = `screenshots/${entry.name}.webp`, destination = path.join(assets, file)
    execFileSync(binary, ['-hide_banner', '-loglevel', 'error', '-y', '-i', entry.png, '-quality', '86', destination])
    const bytes = await readFile(destination)
    entries.push({ file, page: 'user-guide/watch-tv.md', scene: entry.scene, verified_at: new Date().toISOString().slice(0, 10), app_version: version,
      source_commit: commit, source_state: '当前工作区剧集播出与收藏界面',
      source: '真实 Vue 界面与全模拟 API；虚构剧集及原创 SVG 海报，不连接真实媒体、数据库或外网；不证明 TMDB 实际可用性',
      viewport: { width: 1440, height: 1000, device_scale_factor: 1 }, capture_mode: 'full-page',
      recording_script: 'node scripts/smoke_tv_airing.mjs --capture-docs', fixtures: 'scripts/smoke_tv_airing.mjs + scripts/fixtures/uiReviewData.mjs',
      fixture_sha256: createHash('sha256').update(await readFile(fileURLToPath(import.meta.url))).update(await readFile(path.join(root, 'scripts/fixtures/uiReviewData.mjs'))).digest('hex'),
      format: 'WebP q86', bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') })
    console.log('CAPTURE ' + file)
  }
  const replaced = new Set(entries.map(entry => entry.file))
  manifest.assets = [...manifest.assets.filter(entry => !replaced.has(entry.file)), ...entries]
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n')
}
try {
  await mkdir(output, { recursive: true })
  await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(temp, 'cache'),
    plugins: [vue()], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (key.startsWith('/posters/') || /\/(backdrop|still|poster)$/.test(key)) { res.writeHead(200, { 'Content-Type': 'image/svg+xml' }); res.end(fixture.artwork(key)); return }
      if (/^\/api\/tv\/shows\/\d+\/seasons\/3$/.test(key)) { res.writeHead(404, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: 'season not found' })); return }
      if (key.startsWith('/api/')) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString(), data = mockApi(url, req.method, raw ? JSON.parse(raw) : undefined)
        res.writeHead(data === undefined ? 501 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data ?? { detail: 'Unmocked isolated API' })); return
      }
      const file = /^\/assets\/[\w.-]+$/.test(key) || /^\/(favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key)
        ? path.join(dist, key) : /^\/(?:tv(?:\/\d+(?:\/s\/\d+(?:\/e\/\d+)?)?)?|settings)?\/?$/.test(key) ? path.join(dist, 'index.html') : null
      if (!file) { res.writeHead(404); res.end('Only isolated routes are served'); return }
      const bytes = await readFile(file)
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' }); res.end(bytes)
    } catch (error) { unexpected.push(String(error)); if (!res.headersSent) res.writeHead(500); res.end('Isolated fixture failure') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 375, height: 812 }]) {
    errorCode = ''; catalogError = ''; collectionScenario = ''; eventGeneration = 1; maintenanceRemaining = 0
    const context = await browser.newContext({ viewport, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { external.push(route.request().url()); return route.abort() }
      return route.continue()
    })
    const page = await context.newPage(), errors = [], checks = []
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(String(error)))
    page.on('console', message => { if (message.type() === 'error' && !message.text().includes('404 (Not Found)')) errors.push(message.text()) })
    const pass = name => { checks.push(name); console.log(`PASS ${viewport.width}px ${name}`) }
    const go = async route => { await page.goto(base + route); await page.getByRole('navigation', { name: '主导航' }).waitFor(); await page.waitForLoadState('networkidle') }
    const fit = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'Page must not overflow horizontally')
    const screenshot = async (name, docsName, scene) => {
      await fit()
      const png = path.join(output, `${name}-${viewport.width}.png`)
      await page.screenshot({ path: png, fullPage: true, animations: 'disabled' })
      if (docsName && viewport.width === 1440) captures.push({ name: docsName, png, scene })
    }
    await go('/tv/201')
    assert.equal(await page.locator('.season-card').count(), 4)
    assert.ok(await page.getByText('特别篇（SP）', { exact: true }).count())
    assert.equal(await page.locator('.season-collection-badge').filter({ hasText: '未收藏' }).count(), 3)
    await page.getByText('已收藏 3 / 8 集', { exact: true }).waitFor()
    const gray = await page.locator('.season-uncollected-image').first().evaluate(el => getComputedStyle(el).filter)
    assert.match(gray, /grayscale/)
    await screenshot('show', 'tv-show', '虚构剧集详情：SP、部分收藏、未收藏正季与未播出新季；收藏范围为当前媒体库')
    pass('SP and uncollected season cards retain artwork, labels and library scope')

    await page.locator('.season-card-main[href="/tv/201/s/3"]').focus()
    await page.keyboard.press('Enter')
    await page.waitForURL('**/tv/201/s/3'); await page.waitForLoadState('networkidle')
    await page.getByRole('heading', { name: '全部分集', exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: /标记本季/ }).count(), 0)
    assert.equal(await page.locator('.poster-play').count(), 0)
    assert.equal(await page.getByRole('button', { name: '检查文件是否可用', exact: true }).count(), 0)
    assert.equal(await page.locator('.catalog-episode').count(), 8)
    await screenshot('new-season', 'tv-airing-catalog', '未收藏新季的只读官方分集目录：尚未播出与未收藏分别标注，无播放或已看操作')
    pass('keyboard-opened new season survives old API 404 and remains read-only')

    const beforeCatalog = requests.filter(request => request.path === '/api/tv/shows/201/seasons/1/catalog').length
    await go('/tv/201/s/1')
    assert.equal(await page.getByRole('button', { name: '已收藏', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(requests.filter(request => request.path === '/api/tv/shows/201/seasons/1/catalog').length, beforeCatalog)
    await screenshot('local-season', 'tv-season', '季页保留本地分集与播放作用域，可切换全部分集查看缺集及其他视频库来源')
    await page.getByRole('button', { name: '全部分集', exact: true }).click()
    await page.locator('.catalog-episode').last().waitFor()
    assert.equal(await page.locator('.catalog-episode').count(), 8)
    assert.equal(await page.locator('.catalog-uncollected').count(), 5)
    const source = page.locator('.catalog-sources a[href="/tv/901/s/1/e/603"]')
    await source.click(); await page.waitForURL('**/tv/901/s/1/e/603'); await page.waitForLoadState('networkidle')
    await page.locator('.episode-detail').waitFor()
    assert.ok(requests.some(request => request.path === '/api/tv/episodes/603'))
    pass('partial collection has an on-demand full catalog and real source episode links')

    const collectionRequestsBefore = requests.length
    collectionScenario = 'catalog_missing'
    await go('/tv/201')
    const firstSeason = page.locator('.season-card:has(.season-card-main[href="/tv/201/s/1"])')
    await firstSeason.getByText('已收藏 3 集', { exact: true }).waitFor()
    assert.equal(await firstSeason.locator('.season-collection-badge').count(), 0, 'Missing catalog must not claim a matching problem')
    assert.doesNotMatch(await firstSeason.innerText(), /已收藏\s+\d+\s*\/|待核对|需确认|正在|处理中/)
    await page.getByText(/进入「全部分集」可补充目录，无需重新匹配/).waitFor()
    assert.equal(await page.locator('.review-notice').count(), 0, 'Existing confirmed TMDB match remains confirmed')
    await screenshot('catalog-missing')
    await go('/tv/201/s/1')
    await page.locator('.hero-heading').getByText('已收藏 3 集', { exact: true }).waitFor()
    await page.locator('.collection-explanation').getByText(/无需重新匹配/).waitFor()
    assert.equal(await page.getByRole('button', { name: '已收藏', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(requests.slice(collectionRequestsBefore).filter(request => request.path.endsWith('/seasons/1/catalog')).length, 0,
      'Viewing an existing local season must not fetch its full catalog automatically')
    await screenshot('local-catalog-missing')
    pass('missing official catalog preserves known local collection without a review badge or fake progress')

    collectionScenario = 'source_only'
    await go('/tv/201')
    const secondSeason = page.locator('.season-card:has(.season-card-main[href="/tv/201/s/2"])')
    await secondSeason.getByText('已确认收藏 2 集', { exact: true }).waitFor()
    assert.equal(await secondSeason.locator('.season-collection-badge').count(), 0)
    assert.doesNotMatch(await secondSeason.innerText(), /已收藏\s+\d+\s*\/|待核对|需确认/)
    assert.equal(await secondSeason.locator('.season-sources a[href="/tv/901/s/1"]').count(), 1)
    await go('/tv/201/s/2')
    await page.locator('.hero-heading').getByText('已确认收藏 2 集', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: /标记本季/ }).count(), 0)
    await screenshot('source-only-catalog-missing')
    pass('confirmed collection in another video library keeps its count and real local source')

    collectionScenario = 'episode_review'
    await go('/tv/201')
    await firstSeason.getByText('已收藏 3 集', { exact: true }).waitFor()
    await firstSeason.locator('.season-collection-badge').getByText('分集匹配需确认', { exact: true }).waitFor()
    await secondSeason.getByText('已收藏 0 / 8 集', { exact: true }).waitFor()
    assert.equal(await secondSeason.locator('.season-collection-badge').innerText(), '未收藏')
    assert.doesNotMatch(await secondSeason.innerText(), /待核对|需确认|待对照/)
    assert.equal(await page.locator('.review-notice').count(), 0, 'One episode needing review must not unconfirm the whole show')
    await screenshot('episode-matching-review')
    await go('/tv/201/s/1')
    await page.locator('.collection-explanation').getByText(/部分分集匹配尚未确认/).waitFor()
    await page.locator('.hero-heading').getByText('已收藏 3 集', { exact: true }).waitFor()
    await fit()
    collectionScenario = 'partial_coverage'
    await go('/tv/201')
    await firstSeason.locator('.season-collection-badge').getByText('分集编号待对照', { exact: true }).waitFor()
    assert.equal(await secondSeason.locator('.season-collection-badge').count(), 0,
      'Incomplete coverage must not ask the user to recheck an unaffected season')
    assert.doesNotMatch(await secondSeason.innerText(), /待核对|需确认|待对照/)
    await screenshot('partial-numbering-coverage')
    pass('uncertain cross-season coverage keeps numbering warnings on directly affected seasons')

    collectionScenario = 'show_review'
    await go('/tv/201')
    await page.locator('.review-notice').getByText('请确认剧集是否匹配正确', { exact: true }).waitFor()
    await page.locator('.review-notice').getByRole('button', { name: '匹配正确', exact: true }).waitFor()
    assert.equal(requests.slice(collectionRequestsBefore).filter(request => request.path === '/api/tv/airing/check').length, 0,
      'Collection explanations must not start extra background checks')
    await fit(); collectionScenario = ''
    pass('real episode matching review stays scoped while actual show review remains actionable')

    catalogError = 'timeout'
    await go('/tv/201/s/2')
    await page.getByText(/资料服务请求超时/).waitFor()
    assert.equal(await page.locator('.catalog-episode').count(), 8)
    await fit(); catalogError = ''
    errorCode = 'rate_limited'
    await go('/tv/201')
    await page.getByText(/资料服务暂时限制请求/).waitFor()
    await page.getByRole('button', { name: '检查更新', exact: true }).click()
    await page.waitForFunction(() => !document.querySelector('.collection-error'))
    pass('stale metadata stays usable and service errors are human-readable')

    await go('/tv?media=1')
    await page.locator('.update-card').first().waitFor()
    await page.locator('.update-card').first().scrollIntoViewIfNeeded()
    await page.waitForFunction(() => Object.keys(JSON.parse(localStorage.getItem('jzmedia.tvUpdates.v1.1') || '{}').seen || {}).length > 0)
    await screenshot('wall-updates')
    await page.locator('.update-card').first().click(); await page.waitForURL('**/tv/201')
    await page.goBack(); await page.waitForLoadState('networkidle')
    assert.equal(await page.locator('.update-card').count(), 1, 'return from detail restores the same recommendation batch')
    eventGeneration = 2
    await page.reload(); await page.waitForLoadState('networkidle')
    assert.equal(await page.locator('.update-card').count(), 0, 'new events in the same week remain collapsed')
    await page.getByRole('button', { name: '查看更新', exact: true }).waitFor()
    pass('weekly recommendation is acknowledged only when visible and restored after detail')

    await go('/settings?sec=sec-display')
    await page.getByLabel('剧集更新推荐', { exact: true }).selectOption('off')
    const updatesBefore = requests.filter(request => request.path === '/api/tv/updates').length
    await go('/tv?media=1')
    assert.equal(requests.filter(request => request.path === '/api/tv/updates').length, updatesBefore)
    assert.equal(await page.locator('.tv-updates').count(), 0)
    await go('/tv/201'); assert.equal(await page.locator('.season-card').count(), 4)
    pass('turning recommendations off preserves detail information without wall requests')

    await go('/settings?sec=sec-tmdb')
    const maintenance = page.locator('.airing-maintenance')
    await maintenance.getByRole('button', { name: '立即检查播出资料', exact: true }).click()
    await maintenance.getByText('正在检查剧集播出资料…', { exact: true }).waitFor()
    assert.ok(!(await maintenance.innerText()).includes('9001'))
    await maintenance.getByRole('button', { name: '立即检查播出资料', exact: true }).waitFor()
    await screenshot('maintenance')
    pass('settings maintenance shows progress and readable status without media changes')
    assert.deepEqual(errors, [], 'Browser errors')
    results.push({ viewport, checks, errors })
    await context.close()
  }
  assert.deepEqual(external, [], 'All browser traffic stays in the isolated server')
  assert.deepEqual(unexpected, [], 'All APIs are explicitly mocked')
  await writeFile(path.join(output, 'results.json'), JSON.stringify({ results, requests, external, unexpected }, null, 2) + '\n')
  if (capture) await captureDocs()
  console.log('PASS isolated TV airing UI; output/playwright/tv-airing/results.json')
} finally {
  await browser?.close()
  if (server) await new Promise(resolve => server.close(resolve))
  await rm(temp, { recursive: true, force: true })
}
