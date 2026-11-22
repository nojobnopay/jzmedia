// Real Vue App and Router, in-memory API and local demo artwork only.
// Run one check at a time under the repository's bounded validation wrapper.
// node scripts/smoke_online_ui.mjs [--baseline|--settings-only|--alternatives-only] [--width 1440|430|390|375|320] [--capture-docs|--capture-preview]
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { artworkFingerprint, createFixture, movies, shows } from './fixtures/uiReviewData.mjs'
import { ONLINE_ARTWORK_LIMIT } from '../frontend/src/onlineArtwork.js'

const root = fileURLToPath(new URL('../', import.meta.url)), args = process.argv.slice(2)
let alternativesOnly = false, baseline = false, settingsOnly = false, captureDocs = false, capturePreview = false, widths = [1440, 430, 390, 375, 320]
for (let index = 0; index < args.length; index++) {
  if (args[index] === '--baseline') baseline = true
  else if (args[index] === '--alternatives-only') alternativesOnly = true
  else if (args[index] === '--settings-only') settingsOnly = true
  else if (args[index] === '--capture-docs' || args[index] === '--capture-preview') { captureDocs = true; capturePreview = args[index] === '--capture-preview'; widths = [1440, 390] }
  else if (args[index] === '--width') { const width = Number(args[++index]); assert.ok(widths.includes(width)); widths = [width] }
  else if (args[index] === '--help') { console.log('node scripts/smoke_online_ui.mjs [--baseline|--settings-only|--alternatives-only] [--width 1440|430|390|375|320] [--capture-docs|--capture-preview]'); process.exit(0) }
  else throw new Error('Unknown argument: ' + args[index])
}
assert.ok(!(settingsOnly && (baseline || captureDocs)), 'Settings checks use current source and isolated fixtures')
assert.ok(!(baseline && captureDocs), 'Documentation captures require current source')
assert.ok(!alternativesOnly || (!baseline && !captureDocs && !settingsOnly))
if (alternativesOnly && !args.includes('--width')) widths = [1440, 390]
const label = alternativesOnly ? 'playback-alternatives' : settingsOnly ? 'settings-after' : baseline ? 'before' : 'after'
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-ui-'))
const output = path.join(root, 'output/playwright/online-ui', label), dist = path.join(work, 'dist')
const fixture = createFixture(), sourceA = 'src_' + '1'.repeat(32), sourceB = 'src_' + '2'.repeat(32)
const sources = [
  { id: sourceA, name: '光影片库', provider: 'maccms', priority: 0, configuration_token: 'b'.repeat(64), capabilities: { browse: true, search: true, year_filter: true } },
  { id: sourceB, name: '远方放映室', provider: 'maccms', priority: 1, configuration_token: 'c'.repeat(64), capabilities: { browse: true, search: true, year_filter: true } },
]
const categories = [{ id: '1', name: '电影' }, { id: '2', name: '剧集' }, { id: '11', parent_id: '1', name: '剧情片' }, { id: '12', parent_id: '1', name: '冒险片' }, { id: '21', parent_id: '2', name: '国产剧' }]
const hex = number => number.toString(16).padStart(32, '0')
const itemId = index => 'itm_' + hex(index + 1)
const lineId = index => 'ln_' + hex(index + 1)
const episodeId = (index, episode) => 'ep_' + hex((index + 1) * 100 + episode)
const catalogue = JSON.parse(await readFile(path.join(root, 'scripts/fixtures/generated/demo-artwork.json'), 'utf8')).assets
const assets = new Map()
const artworkPath = (index, source) => `/api/online/catalog/artwork/${source}/${hex(index + 1)}`
const metadata = Array.from({ length: 24 }, (_, index) => {
  const tv = index % 3 === 1, media = tv ? shows[Math.floor(index / 3) % shows.length] : movies[index % movies.length]
  const source = index === 1 ? sources[1] : sources[0]
  const asset = catalogue[(tv ? 'show-' : 'movie-') + (tv ? Math.floor(index / 3) % 6 : index % 12)]
  const poster = index === 7 || index === 20 ? '' : artworkPath(index, source.id)
  if (poster) assets.set(poster, asset)
  return { index, source, item: { id: itemId(index), source_id: source.id, external_id: `fixture-${index + 1}`, kind: tv ? 'tv' : 'movie', title: asset.title },
    year: String(media.year), poster_path: poster, category: tv ? '国产剧' : '剧情片', remarks: tv ? (captureDocs ? '更新至第 76 集' : '更新至第 28 集') : '高清',
    description: '最后一班渡轮即将离开海边小城。在旧车站、雨后的街巷与熟悉的放映室之间，人们重新发现被日常掩藏的温柔。这是一段关于相遇、告别与重新出发的故事。',
    genres: tv ? ['剧情', '悬疑'] : ['剧情', '冒险'], actors: ['陈予安', '林知夏', '周远山'], directors: ['程望'], area: '中国大陆', language: '国语' }
})
const identity = (entry, episode = 6) => ({ source_id: entry.source.id, item_id: entry.item.id, line_id: lineId(entry.index),
  kind: entry.item.kind === 'tv' ? 'episode' : 'movie', episode_id: entry.item.kind === 'tv' ? episodeId(entry.index, episode) : null })
const state = { rows: [], epoch: 0, delay: '', requests: [], unexpected: [], expectedFailures: [], failNextBrowse: false }
const settingsProfileId = 'np_' + '3'.repeat(32)
const settingsData = { enabled: true, sources: [], profiles: [], policies: {} }
function resetSettings() {
  settingsData.enabled = true
  settingsData.sources = sources.map(source => ({ ...source, enabled: true,
    api_base_url: 'https://catalog.example/api', allowed_hosts: ['catalog.example'], provider_options: {} }))
  settingsData.profiles = [{ id: settingsProfileId, name: '共享演示代理', mode: 'proxy', proxy_url: 'http://proxy.example:7890', has_credentials: false,
    connect_timeout_seconds: 10, read_timeout_seconds: 20, max_connections: 4, ca_file: '', ca_dir: '' }]
  settingsData.policies = Object.fromEntries(sources.map(source => [source.id, Object.fromEntries(
    ['default', 'api', 'media', 'image', 'subtitle', 'subscription'].map(purpose => [purpose, purpose === 'api' ? { mode: 'direct' }
      : purpose === 'media' ? { mode: 'proxy', profile_id: settingsProfileId } : { mode: 'inherit' }]))]))
}
resetSettings()

const scope = () => (16 + state.epoch).toString(16).padStart(64, '0')
function reset() {
  state.epoch = 0; state.delay = ''; state.failNextBrowse = false
  state.rows = metadata.map((entry, index) => ({ item: entry.item, source: entry.source, poster_path: entry.poster_path,
    episode_label: entry.item.kind === 'tv' ? '第 6 集 · 雨落在旧码头' : '', favorite: index < 8 || index > 20,
    progress: index <= 20 ? { identity: identity(entry), position: [2180, 820, 1536][index % 3], duration: index === 5 ? null : entry.item.kind === 'tv' ? 2700 : 6900,
      paused: true, completed: index === 4 || index === 9, updated_at: 1793322000 - index * 4200, writer_revision: 1 } : null,
    available: index !== 20, reason: index === 20 ? 'online_source_disabled' : '' }))
}
reset()
function api(url, method, body) {
  const key = url.pathname, query = url.searchParams
  state.requests.push({ path: key, method, query: Object.fromEntries(query), ...(body ? { body } : {}) })
  if (key === '/api/ai/assistant' && method === 'POST') {
    assert.deepEqual(body, { channel: 'online', question: '等待开播时可以问你吗？', history: [] })
    return { mode: 'local', answer: '可以，开播前也能问我。', reaction: null }
  }
  if (settingsOnly) {
    if (key === '/api/online/settings') { if (method === 'PATCH') settingsData.enabled = body.enabled; return { enabled: settingsData.enabled } }
    if (key === '/api/online/sources') return { items: settingsData.sources }
    const saved = settingsData.sources.find(source => source.api_base_url === 'https://fixture.example/api')
    const discoverId = saved?.id || 'fixture-discovery', receipt = saved ? 'saved-fixture-receipt' : 'initial-fixture-receipt'
    if (key === '/api/online/discovery/refresh') return { items: [{ id: discoverId, name: '新片放映室', origin: '演示目录',
      api_base_url: 'https://fixture.example/api', source_id: saved?.id || null, enabled: !!saved?.enabled }] }
    if (key === '/api/online/discovery/' + discoverId + '/probe') return { id: discoverId, status: 'measured',
      catalog_ms: 70, media_ms: 225, sample_bytes: 131072, sample_kbps: 4660, reason: 'ok',
      allowed_hosts: saved ? [] : ['media.fixture.example'], probe_token: receipt }
    if (key === '/api/online/discovery/' + discoverId + '/enable') {
      assert.deepEqual(body, { probe_token: receipt })
      if (saved) { saved.enabled = true; return saved }
      const source = { ...settingsData.sources[0], id: 'src_' + '4'.repeat(32), name: '新片放映室',
        api_base_url: 'https://fixture.example/api', allowed_hosts: ['fixture.example', 'media.fixture.example'] }
      settingsData.sources.push(source)
      return source
    }
    if (key === '/api/online/network/profiles') return { items: settingsData.profiles }
    if (key === '/api/online/network/default') return { rule: { mode: 'direct' } }
    if (key === '/api/online/source-templates') return { items: [] }
    if (key === '/api/online/network/profiles/' + settingsProfileId) {
      Object.assign(settingsData.profiles[0], body); return settingsData.profiles[0]
    }
    const sourceMatch = /^\/api\/online\/sources\/(src_[a-f0-9]{32})(?:\/(network|host-check|allow-hosts|host-migration-preview))?$/.exec(key)
    if (sourceMatch) {
      const source = settingsData.sources.find(item => item.id === sourceMatch[1]); assert.ok(source)
      if (!sourceMatch[2] && method === 'GET') return { ...source, configuration_fingerprint: 'a'.repeat(64) }
      if (sourceMatch[2] === 'host-migration-preview') {
        assert.equal(body.api_base_url, source.api_base_url)
        return { expected_fingerprint: 'b'.repeat(64), changes: [] }
      }
      if (sourceMatch[2] === 'network') {
        if (method === 'PATCH') settingsData.policies[source.id] = body
        return { policy: settingsData.policies[source.id] }
      }
      if (sourceMatch[2] === 'host-check') return { source_id: source.id, external_id: body.external_id,
        configuration_fingerprint: 'd'.repeat(64), missing_hosts: ['media.example.com', 'images.example.com'],
        allowed_hosts: source.allowed_hosts, rejected_count: 0 }
      if (sourceMatch[2] === 'allow-hosts') source.allowed_hosts = [...new Set([...source.allowed_hosts, ...body.hosts])]
      else if (method === 'PATCH') Object.assign(source, body)
      return source
    }
  }
  if (captureDocs && key === '/api/online/settings') return { enabled: true }
  if (captureDocs && key === '/api/online/sources') return { items: sources.map(source => ({ ...source, enabled: true })) }
  if (captureDocs && key === '/api/online/network/profiles') return { items: [] }
  if (captureDocs && key === '/api/online/network/default') return { rule: { mode: 'direct' } }
  if (captureDocs && key === '/api/online/source-templates') return { items: [] }
  if (captureDocs && key.endsWith('/host-check')) return { source_id: sourceB, external_id: 'fixture-2', configuration_fingerprint: 'd'.repeat(64), missing_hosts: ['media.example.com', 'images.example.com'], rejected_count: 0 }
  const browseSources = settingsOnly ? settingsData.sources : sources
  if (key === '/api/online/catalog/sources') return { enabled: true, items: browseSources, scope_token: 'a'.repeat(64) }
  if (key === '/api/online/catalog/health') return { purposes: { api: { state: 'success', mode: 'direct' }, media: { state: 'unknown', mode: 'direct' } } }
  if (key === '/api/online/catalog/browse' || key === '/api/online/catalog/search') {
    const page = Number(query.get('page') || 1), source = browseSources.find(source => source.id === query.get('source_id')) || sources[0]
    const items = metadata.filter(entry => entry.source.id === source.id).slice(0, 18).map(entry => ({ ...entry, ...entry.item, title: entry.item.title }))
    return { source, items: page === 1 ? items : [...items.slice(3), ...items.slice(0, 3)], page, page_count: 2, total: 36, categories,
      ...(query.get('priority') === 'background' ? { status: 'ready', configuration_token: source.configuration_token } : {}) }
  }
  if (key === '/api/online/catalog/detail') {
    const entry = metadata.find(entry => entry.item.external_id === query.get('external_id'))
    assert.ok(entry, 'Known isolated detail')
    const record = state.rows.find(row => row.item.id === entry.item.id)
    if (record) record.poster_path = entry.poster_path
    const count = entry.item.kind === 'tv' ? 76 : 1
    return { ...entry, lines: [{ id: lineId(entry.index), label: '高清线路', entries: Array.from({ length: count }, (_, index) => ({
      label: count === 1 ? '正片' : `第${index + 1}集`, identity: identity(entry, index + 1), supported: !(captureDocs && state.hostScene), transport: 'mp4', reason: captureDocs && state.hostScene ? 'host_not_allowed' : '',
    })) }, { id: 'ln_' + hex(200 + entry.index), label: '备用线路', entries: [] }], attribution: [] }
  }
  if (key === '/api/online/catalog/related') return { source: sources.find(source => source.id === query.get('source_id')), external_id: query.get('external_id'), status: 'unsupported' }
  if (key === '/api/online/state/library') {
    const section = query.get('section'), offset = Number(query.get('offset') || 0), limit = Number(query.get('limit') || 20)
    const rows = state.rows.filter(row => section === 'favorites' ? row.favorite : section === 'history' ? row.progress : row.progress && !row.progress.completed)
    return { items: rows.slice(offset, offset + limit), total: rows.length, offset, limit, scope_token: scope(), generation: 0 }
  }
  const item = /^\/api\/online\/state\/items\/(itm_[a-f0-9]{32})(?:\/(favorite|history))?$/.exec(key)
  if (item) {
    const row = state.rows.find(row => row.item.id === item[1]); assert.ok(row)
    if (item[2] === 'favorite' && method === 'PUT') row.favorite = !!body.favorite
    if (item[2] === 'history' && method === 'DELETE') { row.progress = null; state.epoch++; return { deleted: 1, generation: 0, scope_token: scope() } }
    return { item_id: row.item.id, favorite: row.favorite, last_progress: row.progress, selected_progress: row.progress,
      available: row.available, reason: row.reason, scope_token: scope(), generation: 0 }
  }
  // Session preparation is held for the waiting screenshot. No fixture media is served.
  if (key === '/api/online/sessions') return { detail: 'The waiting scene must close before requesting media' }
  if (/^\/api\/online\/sessions\/[A-Za-z0-9_-]+$/.test(key) && method === 'DELETE') return {}
  const result = fixture.api(url, method, body)
  if (result === undefined) state.unexpected.push(method + ' ' + key)
  return result
}

const report = { label, generated_at: new Date().toISOString(), fixture: 'online-ui-v1', artwork_sha256: artworkFingerprint,
  scope: alternativesOnly ? 'Real Vue player and router, mocked online APIs and a local tutorial MP4 streamed over loopback with controlled stalls.'
    : 'Real Vue App and Router, isolated in-memory API and existing fictional demo artwork; no production backend, external source, real library or media decoding.',
  screenshots: [], checks: [], errors: [], external: [], touchTargets: [] }
let server, browser, currentPage, currentWidth, source = root
async function fingerprint(folder) {
  const digest = createHash('sha256')
  async function visit(directory) {
    for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) await visit(file)
      else digest.update(path.relative(folder, file)).update(await readFile(file))
    }
  }
  await visit(folder)
  return digest.digest('hex')
}
async function delayResponse(res) {
  await new Promise(resolve => {
    const timer = setTimeout(resolve, 15000)
    res.once('close', () => { clearTimeout(timer); resolve() })
  })
}
async function capture(page, name, width) {
  await page.evaluate(() => document.fonts.ready)
  if (!['initial-loading', 'loading', 'player-waiting'].includes(name)) {
    // Image fetches resolve before Vue applies the blob URL and the decoder
    // reports load. Wait for the actual visible component state, not networkidle.
    await page.waitForFunction(() => [...document.querySelectorAll('[data-online-artwork-state]')].every(element => {
      const rect = element.getBoundingClientRect()
      if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) return true
      if (element.getAttribute('aria-label')?.endsWith('暂无海报')) return true
      const image = element.querySelector('img')
      return element.dataset.onlineArtworkState === 'ready' && image?.complete && image.naturalWidth > 0
    }))
  }
  await paint(page)
  const file = `${name}-${width}.png`
  await page.screenshot({ path: path.join(output, file), animations: 'disabled' })
  const metrics = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
    scrollY,
    visibleArtwork: [...document.querySelectorAll('[data-online-artwork-state]')].filter(element => {
      const rect = element.getBoundingClientRect()
      return rect.width && rect.height && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth
    }).map(element => ({ state: element.dataset.onlineArtworkState, label: element.getAttribute('aria-label') || element.querySelector('img')?.alt })),
    brokenImages: [...document.images].filter(image => image.complete && !image.naturalWidth).map(image => image.alt) }))
  if (name === 'home') {
    const card = page.locator('.online-results .card').first()
    const poster = await card.locator('[data-online-result]').boundingBox()
    const title = await card.locator('.card-title').boundingBox()
    Object.assign(metrics, { firstPosterTop: poster.y, firstTitleTop: title.y, firstTitleBottom: title.y + title.height })
    if (!baseline && width <= 700) assert.ok(metrics.firstTitleBottom <= page.viewportSize().height, 'First result title fits in the phone viewport')
  }
  report.screenshots.push({ name, width, file, metrics, framing: baseline && name.startsWith('records-') ? 'Original records section below the poster wall' : 'Viewport' })
  assert.ok(metrics.scrollWidth <= metrics.width + 1, `${name} ${width}px has horizontal overflow`)
  assert.deepEqual(metrics.brokenImages, [], `${name} has broken rendered images`)
  if (!baseline && name.startsWith('records-')) assert.equal(metrics.scrollY, 0, 'Independent records screenshots include the application navigation')
  console.log(`CAPTURE ${label} ${name} ${width}px`)
}
async function settle(page) { await page.waitForLoadState('networkidle') }
async function checkPhoneFilters(page) {
  const trigger = page.locator('[data-online-categories-open]')
  const dialog = page.getByRole('dialog', { name: '筛选内容' })
  const year = page.getByLabel('年份', { exact: true })
  const draft = '尚未提交的片名'
  const reads = () => state.requests.filter(entry => ['/api/online/catalog/browse', '/api/online/catalog/search'].includes(entry.path))
  await page.locator('#online-query').fill(draft)
  assert.equal(await year.isVisible(), false)
  const initial = reads().length
  await trigger.click()
  await dialog.getByLabel('查找分类').fill('冒险')
  await dialog.getByRole('button', { name: '冒险片' }).waitFor()
  await year.selectOption('custom')
  await dialog.getByLabel('指定年份').fill('1988')
  await page.keyboard.press('Escape')
  assert.equal(await trigger.evaluate(el => document.activeElement === el), true)
  await trigger.click()
  assert.equal(await dialog.getByLabel('查找分类').inputValue(), '')
  assert.equal(await year.inputValue(), '')
  assert.equal(reads().length, initial, 'Filtering the category list and cancelling a custom year does not fetch a catalogue page')
  const selected = await year.locator('option').nth(1).getAttribute('value')
  await year.selectOption(selected)
  await dialog.waitFor({ state: 'hidden' })
  await settle(page)
  assert.equal(await trigger.locator('[data-online-selected-year]').textContent(), ' · ' + selected)
  assert.equal(reads().length, initial + 1)
  assert.equal(reads().at(-1).query.year, selected)
  assert.equal(await page.locator('#online-query').inputValue(), draft)
  await page.locator('[data-online-refresh]').click()
  await settle(page)
  assert.equal(reads().length, initial + 2, 'One refresh sends one catalogue request')
  assert.equal(reads().at(-1).query.year, selected)
  assert.ok(await page.locator('[data-online-refresh] [data-online-checked-at]').isVisible())
}
async function paint(page) { await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))) }
async function go(page, base, route = '/online') { await page.goto(base + route); await page.locator('.online-page').waitFor(); await settle(page) }
async function records(page, base, tab) {
  await go(page, base, baseline ? '/online' : `/online?view=records&tab=${tab}`)
  const root = page.locator(baseline ? '.online-library' : '[data-online-records]')
  const updated = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.pathname === '/api/online/state/library' && url.searchParams.get('section') === tab && url.searchParams.get('limit') === '20'
  })
  await root.locator(`[data-online-library-section="${tab}"]`).click()
  await (await updated).finished()
  if (baseline) await root.locator('.jz-empty--loading').waitFor({ state: 'hidden' })
  else await root.locator('.online-records-grid[aria-busy="false"]').waitFor()
  await root.locator('[data-online-state-item]').first().waitFor()
  await paint(page)
  if (baseline) await root.evaluate(element => element.scrollIntoView({ block: 'start' }))
  else await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  await paint(page)
  return root
}
async function touchTargets(page, width) {
  if (width > 700) return
  const small = await page.locator('.online-page button:visible, .online-page summary:visible, .online-page select:visible').evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect()
    return { name: element.getAttribute('aria-label') || element.textContent.trim(), width: rect.width, height: rect.height }
  }).filter(rect => rect.width < 43.5 || rect.height < 43.5))
  report.touchTargets.push({ width, small })
  if (!baseline) assert.deepEqual(small, [], 'Phone controls must retain 44px targets')
}

// Serve an existing fictional tutorial clip slowly, so Chromium decodes frames and
// then really exhausts its buffer. API fixtures expose the same episode at a second source.
async function checkPlaybackAlternatives(page, base) {
  const original = identity(metadata[1], 6), alternate = identity(metadata[4], 6)
  const clip = await readFile(path.join(root, 'docs/assets/videos/organizing.mp4'))
  const attempts = [], requests = []
  const taskPath = '/api/online/playback/alternative-tasks', taskId = 'alt_' + 'c'.repeat(32)
  const token = 'p'.repeat(32), probeStarted = Promise.withResolvers()
  let holdMedia = true, probeResponse = null
  let mediaGate = Promise.withResolvers()
  state.serveTestMedia = async (req, res) => {
    const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '')
    const start = range ? Number(range[1]) : 0, end = range?.[2] ? Number(range[2]) + 1 : clip.length
    res.writeHead(range ? 206 : 200, { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
      'Cache-Control': 'no-store', 'Content-Length': end - start,
      ...(range ? { 'Content-Range': `bytes ${start}-${end - 1}/${clip.length}` } : {}) })
    const boundary = holdMedia ? Math.min(end, Math.max(start, 202000)) : end
    res.write(clip.subarray(start, boundary))
    if (boundary < end) await mediaGate.promise
    if (!res.destroyed) res.end(clip.subarray(boundary, end))
  }
  const result = { identity: alternate, external_id: metadata[4].item.external_id, source_name: sources[0].name,
    title: metadata[1].item.title, entry_label: '第6集',
    line_label: '高清线路', throughput_kbps: 7200, recommendation_token: token }
  const originalTitle = metadata[1].item.title
  await page.route('**/api/stream/client-info', route => route.fulfill({ json: { features: ['online_alternatives_v2'] } }))
  await page.route('**/api/online/**', async route => {
    const req = route.request(), url = new URL(req.url()), key = url.pathname
    if (key === '/api/online/sessions') {
      const requested = req.postDataJSON()
      if (requested.source_id === alternate.source_id) assert.equal(req.headers()['x-online-recommendation'], token)
      attempts.push(requested)
      const sid = hex(attempts.length)
      return route.fulfill({ json: { identity: requested, session_id: sid, media_url: '/api/online/sessions/' + sid + '/media',
        transport: 'mp4', processing: 'passthrough', seekable: true, duration_seconds: 31.95,
        time_offset_seconds: 0, expires_at: Math.floor(Date.now() / 1000) + 3600 } })
    }
    if (key === taskPath) {
      requests.push(req.postDataJSON())
      return route.fulfill({ json: { task_id: taskId, status: 'running', items: [], retry_after_ms: 0, partial: false } })
    }
    if (key === taskPath + '/' + taskId && req.method() === 'DELETE') {
      return route.fulfill({ status: 204 })
    }
    if (key === taskPath + '/' + taskId + '/advance') {
      probeStarted.resolve()
      if (probeResponse) await probeResponse.promise
      return route.fulfill({ json: { task_id: taskId, status: 'done', items: [result], retry_after_ms: 0, partial: false } }).catch(() => {})
    }
    if (key.startsWith('/api/online/state/progress')) {
      const body = req.postDataJSON()
      const response = key.endsWith('/claim') ? { identity: attempts.at(-1), writer_id: body.session_id,
        writer_revision: body.expected_revision + 1 } : { progress: { ...body, identity: attempts.at(-1) } }
      return route.fulfill({ json: { ...response, scope_token: scope() } })
    }
    if (key === '/api/online/catalog/detail' && url.searchParams.get('external_id') === result.external_id) {
      const detail = api(url, 'GET')
      detail.item = { ...detail.item, title: originalTitle }
      return route.fulfill({ json: detail })
    }
    return route.fallback()
  })
  const dialog = page.locator('.online-player-dlg')
  const choice = dialog.getByRole('button', { name: '切换到 光影片库 · 高清线路' })
  state.rows.find(row => row.item.id === original.item_id).progress.position = 0
  async function stall() {
    await go(page, base, `/online?source=${sourceB}&item=fixture-2`)
    await page.locator('[data-online-primary-play]').click()
    await page.waitForFunction(() => document.querySelector('.online-player-video')?.currentTime > .2)
    await dialog.getByText('正在缓冲', { exact: true }).waitFor({ timeout: 15000 })
  }
  try {
    // Recover after the probe starts and deliver a stale result. No offer survives.
    probeResponse = Promise.withResolvers()
    await stall()
    await dialog.getByText('正在探测同一节目的备用线路…').waitFor()
    await probeStarted.promise
    assert.deepEqual(requests, [original])
    const cancelled = page.waitForRequest(req => req.method() === 'DELETE' && new URL(req.url()).pathname === taskPath + '/' + taskId)
    holdMedia = false; mediaGate.resolve()
    await page.waitForFunction(() => document.querySelector('.online-player-video')?.currentTime > 4)
    await dialog.locator('.online-alternative').waitFor({ state: 'hidden' })
    probeResponse.resolve(); probeResponse = null
    await cancelled
    assert.equal(await choice.count(), 0, 'A late response cannot reopen an offer after playback recovers')
    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'hidden' })
    assert.deepEqual(attempts, [original])

    // Stall again, leave bytes blocked until a viewer selects the offer.
    mediaGate = Promise.withResolvers(); holdMedia = true
    await stall()
    await choice.waitFor({ timeout: 15000 })
    await capture(page, 'alternate-line', page.viewportSize().width)
    assert.deepEqual(requests, [original, original])
    assert.deepEqual(attempts, [original, original], 'A recommendation alone never creates the alternate session')
    const switched = page.waitForRequest(req => new URL(req.url()).pathname === '/api/online/sessions' && req.postDataJSON().source_id === sourceA)
    await choice.click(); await switched
    holdMedia = false; mediaGate.resolve()
    await page.waitForURL(url => url.searchParams.get('source') === sourceA && url.searchParams.get('item') === result.external_id)
    await page.waitForFunction(() => document.querySelector('.online-player-video')?.currentTime > .2)
    assert.deepEqual(attempts, [original, original, alternate], 'The player adopts the one prepared session for the verified target episode')
    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'hidden' })
    report.checks.push({ width: page.viewportSize().width, checks: ['decoded frames before real MP4 starvation',
      'recovery cancels pending recommendation', 'explicit alternate choice opens the same episode on another source'] })
  } finally {
    holdMedia = false; mediaGate.resolve(); probeResponse?.resolve()
    await page.unrouteAll({ behavior: 'wait' })
    delete state.serveTestMedia
  }
}

async function checkSourceSettings(page, base, width) {
  await page.goto(base + '/settings?sec=sec-online')
  const sourceRow = page.locator(`[data-online-source-row="${sourceA}"]`)
  await sourceRow.waitFor()
  const discover = page.getByRole('button', { name: '发现并探测来源', exact: true })
  await discover.click()
  const result = page.locator('[data-discovery-candidate="fixture-discovery"]')
  await result.getByText(/本轮优先试用/).waitFor()
  await result.getByText(/允许访问 media.fixture.example/).waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
  await result.getByRole('button', { name: '启用来源 新片放映室' }).click()
  await result.getByText('已启用', { exact: true }).waitFor()
  await page.getByRole('button', { name: '返回视频源列表', exact: true }).click()
  await page.getByRole('heading', { name: '新片放映室', exact: true }).waitFor()
  assert.equal(await discover.evaluate(button => document.activeElement === button), true)
  const chosenSource = 'src_' + '4'.repeat(32)
  settingsData.sources.find(source => source.id === chosenSource).enabled = false
  await discover.click()
  const existing = page.locator(`[data-discovery-candidate="${chosenSource}"]`)
  await existing.getByRole('button', { name: '启用来源 新片放映室' }).click()
  await existing.getByRole('link', { name: '使用此源 新片放映室' }).click()
  await page.waitForURL(url => url.pathname === '/online' && url.searchParams.get('source') === chosenSource)
  await page.waitForFunction(id => document.querySelector('[data-online-source]')?.value === id, chosenSource)
  await page.goto(base + '/settings?sec=sec-online')
  await sourceRow.waitFor()
  const edit = sourceRow.getByRole('button', { name: '编辑 光影片库', exact: true })
  await edit.click()
  const form = page.locator('[data-online-source-form]')
  await form.waitFor()
  assert.equal(await sourceRow.isVisible(), false, 'Editing is a separate task, not a form after the list')
  const field = page.getByLabel('来源名称', { exact: true })
  const box = await field.boundingBox()
  if (width <= 700) assert.ok(box.y < 360, 'The first source field is immediately visible')
  if (width === 1440) {
    const originalFont = await page.evaluate(() => document.documentElement.style.fontSize)
    const alignment = []
    for (const viewportWidth of [1440, 1024, 900, 768, 701, 390, 320]) {
      await page.setViewportSize({ width: viewportWidth, height: 1000 })
      await page.evaluate(() => { document.documentElement.style.fontSize = '20px' })
      const layout = await form.evaluate(element => {
        const hint = element.querySelector('#online-source-priority-description').getBoundingClientRect()
        const option = element.querySelector('.online-source-check').getBoundingClientRect()
        return { hintBottom: hint.bottom, optionTop: option.top, optionHeight: option.height,
          overflow: document.documentElement.scrollWidth > innerWidth + 1 }
      })
      assert.ok(layout.optionTop >= layout.hintBottom, 'Source enabled option stays below priority help when it wraps')
      assert.ok(layout.optionHeight >= 44, 'Source enabled option retains its touch target')
      assert.equal(layout.overflow, false, 'Source fields fit at large text sizes')
      alignment.push({ width: viewportWidth, font: 20, ...layout })
      await capture(page, 'source-alignment', viewportWidth)
    }
    await writeFile(path.join(root, 'output/playwright/online-ui', label, 'source-alignment.json'), JSON.stringify(alignment, null, 2) + '\n')
    await page.evaluate(font => { document.documentElement.style.fontSize = font }, originalFont)
    await page.setViewportSize({ width, height: 1000 })
  }
  await field.fill('尚未保存的来源名称')
  await page.getByRole('button', { name: '返回视频源列表', exact: true }).click()
  await page.getByRole('button', { name: '继续编辑', exact: true }).click()
  assert.equal(await field.inputValue(), '尚未保存的来源名称')
  await page.getByRole('button', { name: '返回视频源列表', exact: true }).click()
  await page.getByRole('button', { name: '放弃修改并离开', exact: true }).click()
  await sourceRow.waitFor()
  assert.equal(await edit.evaluate(element => element === document.activeElement), true, 'Returning restores the source entry focus')
  await edit.click(); await field.fill('已保存的来源名称')
  await page.getByRole('button', { name: '保存来源', exact: true }).click()
  await page.getByText('来源已保存；尚未验证上游连通性。', { exact: true }).waitFor()
  assert.equal(settingsData.sources[0].name, '已保存的来源名称')
  await capture(page, 'source-list', width)

  const network = sourceRow.getByRole('button', { name: '网络 已保存的来源名称', exact: true })
  state.failSettingsNetwork = true
  await network.click()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  const networkForm = page.locator('[data-online-network-form]')
  await networkForm.waitFor()
  await networkForm.locator('summary').filter({ hasText: '高级：按用途分别配置' }).click()
  assert.equal(await page.locator('#online-network-media').inputValue(), 'profile:' + settingsProfileId)
  await page.locator('#online-network-default').selectOption('direct')
  await page.getByRole('button', { name: '保存网络规则', exact: true }).click()
  await sourceRow.waitFor()
  assert.equal(settingsData.policies[sourceA].api.mode, 'direct', 'Explicit API direct routing survives saving')
  assert.equal(settingsData.policies[sourceA].media.profile_id, settingsProfileId, 'Media override survives saving')
  await network.click(); await networkForm.waitFor()
  await page.locator('#online-network-mode').selectOption('proxy')
  await capture(page, 'source-network', width)
  await page.getByRole('button', { name: '返回视频源列表', exact: true }).click()
  await page.getByRole('button', { name: '放弃修改并离开', exact: true }).click()

  await page.locator('.online-shared-profiles > summary').click()
  await page.getByRole('button', { name: '编辑代理 共享演示代理', exact: true }).click()
  await page.getByLabel('配置名称', { exact: true }).fill('共享代理草稿')
  assert.equal(await network.isDisabled(), true, 'A shared proxy draft protects source tasks')
  await page.getByRole('button', { name: '取消编辑', exact: true }).click()
  assert.equal(await network.isEnabled(), true)

  await page.goto(base + `/settings?sec=sec-online&source=${sourceA}&item=fixture-1`)
  await page.locator('[data-online-host-check]').waitFor()
  await page.getByRole('button', { name: '检查媒体主机', exact: true }).click()
  await page.getByRole('checkbox', { name: 'images.example.com', exact: true }).uncheck()
  await page.getByRole('button', { name: '加入允许列表', exact: true }).click()
  await page.getByText('媒体主机已加入允许列表。返回影片详情后会重新读取播放线路。', { exact: true }).waitFor()
  assert.deepEqual(settingsData.sources[0].allowed_hosts, ['catalog.example', 'media.example.com'])
  await capture(page, 'source-hosts', width)
  await page.getByRole('button', { name: '关闭检查', exact: true }).click()
  await sourceRow.waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'Settings fit the viewport')
  report.checks.push({ width, checks: ['source edit visible immediately', 'draft keep/discard', 'focus return', 'source save semantics', 'network failure/retry',
    'per-purpose network preservation', 'shared proxy draft locking', 'explicit host permission', 'task cancel', 'no horizontal overflow'] })
}

try {
  await mkdir(output, { recursive: true })
  report.commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim()
  if (baseline) {
    source = path.join(work, 'source'); await mkdir(source)
    const archive = execFileSync('git', ['archive', 'HEAD', 'frontend'], { cwd: root, maxBuffer: 32 * 1024 ** 2 })
    execFileSync('tar', ['-x', '-C', source], { input: archive })
    await symlink(path.join(root, 'frontend/node_modules'), path.join(source, 'frontend/node_modules'))
  }
  report.source_sha256 = await fingerprint(path.join(source, 'frontend/src'))
  report.fixture_sha256 = createHash('sha256').update(JSON.stringify(metadata)).update(artworkFingerprint).digest('hex')
  if (!baseline && !captureDocs && !settingsOnly && !alternativesOnly) {
    const previous = await readFile(path.join(root, 'output/playwright/online-ui/before/report.json'), 'utf8').then(JSON.parse).catch(() => null)
    if (previous) assert.equal(previous.fixture_sha256, report.fixture_sha256, 'Recapture baseline after changing the comparison fixture')
  }
  await build({ root: path.join(source, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(work, 'cache'),
    plugins: [vue()], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (alternativesOnly && key.endsWith('/media') && state.serveTestMedia) {
        await state.serveTestMedia(req, res)
        return
      }
      if (assets.has(key)) {
        const asset = assets.get(key), bytes = await readFile(path.join(root, asset.master))
        assert.equal(createHash('sha256').update(bytes).digest('hex'), asset.sha256)
        assert.ok(bytes.length <= ONLINE_ARTWORK_LIMIT)
        res.writeHead(200, { 'Content-Type': 'image/webp' }); res.end(bytes); return
      }
      if (key.startsWith('/api/')) {
        if (key === '/api/online/cache/lookup') { res.writeHead(404, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ code: 'online_cache_not_found' })); return }
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString(), body = raw ? JSON.parse(raw) : undefined
        if (state.delay === 'browse' && /\/catalog\/(browse|search)$/.test(key) || state.delay === 'session' && key === '/api/online/sessions') await delayResponse(res)
        if (res.destroyed) return
        if (state.failNextBrowse && /\/catalog\/(browse|search)$/.test(key)) {
          state.failNextBrowse = false; state.expectedFailures.push(key)
          res.writeHead(503, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ code: 'provider_timeout', detail: '隔离目录暂时不可用' })); return
        }
        if (settingsOnly && state.failSettingsNetwork && /\/sources\/[^/]+\/network$/.test(key) && req.method === 'GET') {
          state.failSettingsNetwork = false; state.expectedFailures.push(key)
          res.writeHead(503, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: '隔离网络配置暂不可用' })); return
        }
        const data = api(url, req.method, body)
        res.writeHead(data === undefined ? 501 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data ?? { detail: 'Unmocked isolated API' })); return
      }
      const file = /^\/assets\/[\w.-]+$/.test(key) || /^\/(?:favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key)
        ? path.join(dist, key) : key === '/online' || key === '/settings' || key === '/' ? path.join(dist, 'index.html') : null
      if (!file) { state.unexpected.push(req.method + ' ' + key); res.writeHead(404); res.end('Only isolated routes are served'); return }
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' })
      res.end(await readFile(file))
    } catch (error) { state.unexpected.push(String(error)); if (!res.headersSent) res.writeHead(500); res.end('Isolated fixture failure') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const width of widths) {
    reset(); resetSettings()
    const context = await browser.newContext({ viewport: { width, height: width > 700 ? 1000 : 844 }, hasTouch: width <= 700,
      serviceWorkers: 'block', reducedMotion: 'reduce', timezoneId: 'Asia/Shanghai', locale: 'zh-CN' })
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { report.external.push(route.request().url()); return route.abort() }
      return route.continue()
    })
    const page = currentPage = await context.newPage(); currentWidth = width; page.setDefaultTimeout(10000)
    page.on('pageerror', error => report.errors.push(String(error)))
    page.on('console', message => { if (message.type() === 'error' && !/(?:503 \(Service Unavailable\)|404 \(Not Found\))/.test(message.text())) report.errors.push(message.text()) })
    if (alternativesOnly) { await checkPlaybackAlternatives(page, base); await context.close(); continue }
    if (settingsOnly) { await checkSourceSettings(page, base, width); await context.close(); continue }
    await go(page, base)
    await page.locator('[data-online-result]').first().waitFor()
    if (!baseline && width <= 700) {
      const nav = page.getByRole('navigation', { name: '主导航' })
      assert.equal(await nav.locator('.nav-library, .nav-single-library').count(), 0)
      const brand = await nav.locator('.brand').boundingBox()
      const mode = await nav.locator('.nav-viewing button').first().boundingBox()
      const tops = await nav.locator('.nav-channels a, .nav-secondary summary').evaluateAll(elements => elements.map(el => el.getBoundingClientRect().top))
      assert.ok(Math.abs(brand.y + brand.height / 2 - mode.y - mode.height / 2) <= 2, 'Online brand and viewing mode share a row')
      assert.equal(tops.length, 5)
      assert.ok(tops.every(top => top === tops[0] && top >= mode.y + mode.height - 1), 'Online channels and More share the next row')
    }
    await capture(page, 'home', width)
    if (captureDocs) {
      if (width === 1440) {
        await records(page, base, 'favorites')
        await capture(page, 'docs-records', width)
        await go(page, base, `/online?source=${sourceB}&item=fixture-2`)
        await page.locator('[data-online-detail]').waitFor()
        await page.locator('[data-online-entry-group]').waitFor()
        await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
        await capture(page, 'docs-detail', width)
        await page.locator('[data-online-episodes]').evaluate(element => element.scrollIntoView({ block: 'start', behavior: 'instant' }))
        await capture(page, 'docs-episodes', width)
        state.hostScene = true
        await go(page, base)
        await go(page, base, `/online?source=${sourceB}&item=fixture-2`)
        await page.locator('[data-online-host-candidate]').first().waitFor()
        await capture(page, 'docs-hosts', width)
        state.hostScene = false
        await page.goto(base + '/settings?sec=sec-online')
        await page.getByRole('button', { name: '添加来源', exact: true }).waitFor()
        await capture(page, 'docs-sources', width)
      }
      await context.close()
      continue
    }
    await touchTargets(page, width)
    if (!baseline && width <= 700) await checkPhoneFilters(page)
    if (!baseline) assert.equal(await page.locator('[data-online-continue-row] [data-online-state-item]').count(), 12)
    state.delay = 'browse'
    await page.goto(base + '/online')
    await page.locator('[data-online-search]').waitFor()
    await page.locator('.online-skeleton, .jz-empty--loading').first().waitFor()
    await capture(page, 'initial-loading', width)
    await page.goto('about:blank'); state.delay = ''
    for (const tab of ['continue', 'favorites', 'history']) {
      await records(page, base, tab)
      await capture(page, `records-${tab}`, width)
      if (tab === 'continue') await touchTargets(page, width)
    }
    await go(page, base, `/online?source=${sourceB}&item=fixture-2`)
    await page.locator('[data-online-detail]').waitFor()
    await capture(page, 'detail', width)
    await touchTargets(page, width)
    state.delay = 'session'
    await page.locator('[data-online-primary-play]').click()
    await page.locator('.online-player-state').waitFor()
    await capture(page, 'player-waiting', width)
    const animation = await page.locator('.jz-spinner').last().evaluate(element => getComputedStyle(element).animationName)
    assert.equal(animation, 'none', 'Reduced motion keeps a static waiting symbol')
    if (!baseline) {
      const player = page.locator('.online-player-dlg')
      const trigger = player.getByRole('button', { name: '问小映', exact: true })
      const chat = page.getByRole('dialog', { name: '小映', exact: true })
      await trigger.click()
      await chat.getByRole('textbox').fill('等待开播时可以问你吗？')
      await chat.getByRole('button', { name: '发送', exact: true }).click()
      await chat.getByText('可以，开播前也能问我。', { exact: true }).waitFor()
      await chat.getByRole('textbox').press('Escape')
      await chat.waitFor({ state: 'hidden' })
      await page.waitForFunction(el => el === document.activeElement, await trigger.elementHandle())
      assert.ok(await player.isVisible(), 'Closing 小映 returns to the original online player')
    }
    await page.keyboard.press('Escape'); await page.locator('.online-player-dlg').waitFor({ state: 'hidden' }); state.delay = ''
    await go(page, base)
    state.delay = 'browse'
    await page.locator('[data-online-page-next]').click()
    await page.locator('[data-online-loading-feedback]').waitFor()
    await capture(page, 'loading', width)
    if (!baseline) {
      const feedback = await page.locator('[data-online-loading-feedback]').evaluate(element => {
        const rect = element.getBoundingClientRect(); return { bottom: rect.bottom, top: rect.top, height: innerHeight, pointerEvents: getComputedStyle(element).pointerEvents }
      })
      assert.ok(feedback.top > feedback.height / 2 && feedback.bottom <= feedback.height, 'Waiting feedback belongs near the viewport bottom')
      assert.equal(feedback.pointerEvents, 'none')
    }
    await page.goto('about:blank'); state.delay = ''
    if (!baseline) {
      state.rows[0].poster_path = ''
      await go(page, base)
      const savedCard = page.locator(`[data-online-continue-row] [data-online-state-item="${itemId(0)}"]`)
      await savedCard.getByText('暂无海报', { exact: true }).waitFor()
      await savedCard.locator('[data-online-library-detail]').click()
      await page.locator('[data-online-detail]').waitFor()
      await page.getByRole('button', { name: '返回搜索结果', exact: true }).click()
      await savedCard.locator('[data-online-artwork-state="ready"] img').waitFor()
      const poster = page.locator('[data-online-result]').nth(12)
      await poster.scrollIntoViewIfNeeded()
      const scroll = await page.evaluate(() => scrollY)
      const posterKey = await poster.getAttribute('data-online-result')
      await poster.click(); await page.locator('[data-online-detail]').waitFor(); await settle(page)
      await page.getByRole('button', { name: '返回搜索结果', exact: true }).click()
      await page.waitForFunction(y => Math.abs(scrollY - y) < 4, scroll)
      assert.equal(await page.locator(`[data-online-result="${posterKey}"]`).evaluate(element => document.activeElement === element), true,
        'Returning from detail restores the original poster focus')
      const heading = page.locator('[data-online-records-open]').first()
      await heading.click(); await page.locator('[data-online-records]').waitFor(); await settle(page)
      assert.equal(new URL(page.url()).searchParams.get('view'), 'records')
      await page.goBack(); await page.locator('[data-online-continue-row]').waitFor()
      await page.goForward(); await page.locator('[data-online-records]').waitFor()
      await settle(page)
      const crossSource = page.locator(`[data-online-records] [data-online-library-detail="${itemId(1)}"]`)
      await crossSource.click(); await page.locator('[data-online-detail]').waitFor(); await settle(page)
      await page.getByRole('button', { name: '返回我的记录', exact: true }).click(); await page.locator('[data-online-records]').waitFor()
      await page.locator('[data-online-records-back]').click(); await page.locator('[data-online-source]').waitFor()
      assert.equal(await page.locator('[data-online-source]').inputValue(), sourceA, 'Opening a saved item from another source preserves original browsing source')
      const recordRoot = await records(page, base, 'favorites')
      const favorite = recordRoot.locator(`[data-online-state-item="${itemId(0)}"]`)
      const menu = favorite.locator('summary')
      await menu.focus(); await page.keyboard.press('Enter')
      assert.equal(await favorite.locator('details').evaluate(element => element.open), true)
      await page.keyboard.press('Escape')
      assert.equal(await menu.evaluate(element => document.activeElement === element), true)
      await menu.click(); await favorite.locator('[data-online-favorite]').click(); await settle(page)
      await favorite.waitFor({ state: 'detached' })
      assert.equal(state.rows[0].favorite, false, 'Cancel favorite reaches the isolated API')
      assert.equal(await favorite.count(), 0, 'Cancel favorite removes the card from favorites')
      await records(page, base, 'history')
      const pager = page.getByRole('navigation', { name: '在线记录分页' })
      await pager.getByRole('button', { name: '下一页', exact: true }).click(); await settle(page)
      const last = page.locator('[data-online-records] [data-online-state-item]')
      await page.waitForFunction(() => document.querySelectorAll('[data-online-records] [data-online-state-item]').length === 1)
      assert.equal(await last.count(), 1)
      await page.locator('[data-online-records-back]').click(); await page.locator('[data-online-continue-row]').waitFor()
      await page.goBack(); await page.locator('[data-online-records] .online-records-grid[aria-busy="false"]').waitFor()
      await page.waitForFunction(() => document.querySelector('[data-online-records] .online-records-pagination')?.textContent.includes('第 2 页'))
      assert.equal(await last.count(), 1, 'Browser Back restores the original history page')
      await last.locator('summary').click(); await last.locator('[data-online-history-delete]').click(); await settle(page)
      await page.waitForFunction(() => document.querySelectorAll('[data-online-records] [data-online-state-item]').length === 20)
      assert.equal(await page.locator('[data-online-records] [data-online-state-item]').count(), 20, 'Deleting the final page item returns to a valid page')
      await go(page, base)
      const before = await page.locator('[data-online-result]').evaluateAll(elements => elements.map(element => element.getAttribute('data-online-result')))
      state.failNextBrowse = true
      const failedRefresh = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/browse' && response.status() === 503)
      await page.locator('[data-online-refresh]').click(); await failedRefresh
      await page.locator('.online-refresh-error').waitFor()
      assert.deepEqual(await page.locator('[data-online-result]').evaluateAll(elements => elements.map(element => element.getAttribute('data-online-result'))), before,
        'A failed refresh retains the previous poster wall')
      report.checks.push({ width, checks: ['continue shelf', 'continue poster recovers after opening detail', 'initial skeleton', 'independent records view', 'browse scroll/focus return', 'browser back/forward', 'cross-source return', 'keyboard action menu', 'cancel favorite', 'browser back restores records page', 'delete last-page history', 'failed refresh preserves content', 'reduced motion', '44px touch targets', 'ask 小映 while preparing playback and return to player'] })
    }
    await context.close()
  }
  assert.deepEqual(report.errors, [], 'No browser errors')
  assert.deepEqual(report.external, [], 'No requests leave the isolated origin')
  assert.deepEqual(state.unexpected, [], 'Every API and asset is explicitly mocked')
  report.completed = true
  if (captureDocs && !capturePreview) {
    let ffmpeg = 'ffmpeg'
    try { execFileSync(ffmpeg, ['-version'], { stdio: 'ignore' }) } catch {
      ffmpeg = null
      for (const python of await readdir(path.join(root, '.venv/lib')).catch(() => [])) {
        const bins = path.join(root, '.venv/lib', python, 'site-packages/static_ffmpeg/bin')
        for (const platform of await readdir(bins).catch(() => [])) {
          const candidate = path.join(bins, platform, 'ffmpeg')
          try { execFileSync(candidate, ['-version'], { stdio: 'ignore' }); ffmpeg = candidate; break } catch { /* Try another platform. */ }
        }
        if (ffmpeg) break
      }
      assert.ok(ffmpeg, 'FFmpeg is required for documentation capture')
    }
    const samples = [
      ['online-browse', 'home', 1440, '在线视频源、分类、年份和海报墙'],
      ['online-mobile', 'home', 390, '手机宽度下的在线视频浏览'],
      ['online-records', 'docs-records', 1440, '在线收藏及继续观看、历史入口'],
      ['online-detail', 'docs-detail', 1440, '在线详情的续播、线路与分集'],
      ['online-episodes', 'docs-episodes', 1440, '长剧集范围、排序与上次观看定位'],
      ['online-hosts', 'docs-hosts', 1440, '媒体主机检查与确认对话框'],
      ['online-sources', 'docs-sources', 1440, '在线点播开关与已启用来源列表'],
    ]
    const assetsDir = path.join(root, 'docs/assets'), entries = []
    const appVersion = (await readFile(path.join(root, 'version.properties'), 'utf8')).match(/^versionName=(.+)$/m)[1]
    for (const [name, scene, width, description] of samples) {
      if (!widths.includes(width)) continue
      const shot = report.screenshots.find(item => item.name === scene && item.width === width)
      assert.ok(shot, 'Missing documentation screenshot ' + name)
      const file = `screenshots/${name}.webp`, destination = path.join(assetsDir, file)
      execFileSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y', '-i', path.join(output, shot.file), '-quality', '86', destination])
      const bytes = await readFile(destination)
      entries.push({ file, page: 'user-guide/watch-online.md', scene: description, verified_at: new Date().toISOString().slice(0, 10),
        app_version: appVersion, source_commit: report.commit, source_state: '当前工作区；前端源码及演示数据摘要见下', source_sha256: report.source_sha256,
        source_digest_scope: ['frontend/src/**/*'], source: '真实 Vue 界面与隔离内存 API；虚构资料及 AI 原创海报，不连接真实来源或媒体库',
        viewport: { width, height: width > 700 ? 1000 : 844, device_scale_factor: 1 }, recording_script: 'node scripts/smoke_online_ui.mjs --capture-docs' + (widths.length === 1 ? ' --width ' + width : ''),
        fixtures: 'scripts/smoke_online_ui.mjs + scripts/fixtures/uiReviewData.mjs + design/demo-artwork/', fixture_sha256: createHash('sha256').update(await readFile(fileURLToPath(import.meta.url))).update(report.fixture_sha256).digest('hex'),
        artwork_sha256: artworkFingerprint, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') })
    }
    const manifestFile = path.join(assetsDir, 'manifest.json'), manifest = JSON.parse(await readFile(manifestFile, 'utf8'))
    const replaced = new Set(entries.map(entry => entry.file))
    manifest.assets = [...manifest.assets.filter(entry => !replaced.has(entry.file)), ...entries]
    await writeFile(manifestFile, JSON.stringify(manifest, null, 2) + '\n')
  }
} catch (error) {
  report.failure = String(error.stack || error)
  if (currentPage && !currentPage.isClosed()) {
    report.failure_state = { width: currentWidth, url: currentPage.url(), text: await currentPage.locator('.online-page').innerText().catch(() => '') }
    await currentPage.screenshot({ path: path.join(output, `failure-${currentWidth}.png`) }).catch(() => {})
  }
  throw error
} finally {
  report.unexpected = state.unexpected; report.expectedFailures = state.expectedFailures; report.api_request_count = state.requests.length
  if (report.failure) report.last_requests = state.requests.slice(-20)
  await mkdir(output, { recursive: true })
  await writeFile(path.join(output, widths.length === 1 ? `report-${widths[0]}.json` : 'report.json'), JSON.stringify(report, null, 2) + '\n')
  await browser?.close()
  if (server?.listening) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) }
  await rm(work, { recursive: true, force: true })
}
