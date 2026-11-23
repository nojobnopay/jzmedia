// Run: node scripts/smoke_ai_ui.mjs [--capture-docs | --capture-search | --settings-assistant-only | --demo]
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
import { art, artworkFingerprint } from './fixtures/uiReviewData.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const project = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
assert.ok(args.every(arg => ['--capture-docs', '--capture-search', '--settings-assistant-only', '--demo'].includes(arg)), 'Unknown AI UI smoke mode')
assert.ok(args.length <= 1, 'Choose one mode per run')
const searchCapture = args.includes('--capture-search')
const assistantOnly = args.includes('--settings-assistant-only')
const capture = args.includes('--capture-docs') || searchCapture, demo = args.includes('--demo')
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
const episodeQuestion = '找《模拟剧集》第二季第三集的国语版'
const dubbedEpisode = { id: 307, show_id: show.id, show_title: show.title, season: 2, episode: 3,
  title: '回到渡口', version: 2, exists: true, watched: 0, cast: [], directors: [] }
const settings = { enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com',
  model: 'simulated-model', timeout_seconds: 12, daily_limit: 100,
  api_key_set: false, api_key_source: 'unset', api_key_masked: '',
  usage: { date: '2026-10-02', requests: 0, input_tokens: 0, output_tokens: 0 } }
const requests = [], unexpected = [], errors = []
const viewingSession = { mode: 'normal', enabled: false, management_unlocked: false, generation: 0, expires_at: 0 }
const assistantTopics = JSON.parse(await readFile(path.join(project, 'docs/settings-assistant-topics.json'), 'utf8'))
const assistantEndpoint = '/api/ai/settings-assistant'
const browseAssistantEndpoint = '/api/ai/assistant'
const ruleWrites = () => requests.filter(r => r.method === 'PUT' && r.path.endsWith('/matching-rules'))
let beforeMatchingWrite, expectedConflictPath, beforeAssistantReply
let suggestedSeries = []
const socialExamples = [
  { question: '你叫什么名字？', answer: '我叫小映，是 jzmedia 的小放映机。', reaction: 'wave' },
  { question: '你好啊', answer: '你好呀，我是小映！', reaction: 'wave' },
  { question: '给我笑一笑', answer: '嘿嘿，给你一个大大的笑容！', reaction: 'smile' },
  { question: '给我哭一个', answer: '呜，放映机的小泪珠掉下来了。', reaction: 'cry' },
  { question: '跳个舞', answer: '来啦，左摇一下，右摆一下！', reaction: 'dance' },
  { question: '我有点难过', answer: '我在这儿。你想跟我说说发生了什么吗？', reaction: null },
]

function assistantReply(body) {
  assert.deepEqual(Object.keys(body).sort(), ['history', 'question', 'section'])
  const social = socialExamples.find(example => example.question === body.question)
  if (social) return { mode: 'social', answer: social.answer, reaction: social.reaction, topics: [], suggestion: null }
  const font = body.question.includes('字体')
  const protection = body.question.includes('密码')
  const local = !settings.enabled || (!settings.api_key_set && settings.provider !== 'compatible')
  const topic = assistantTopics.find(item => item.id === (protection ? 'protection' : font ? 'display' : 'matching'))
  const { id, title, page, anchor, section } = topic
  const suggestion = font ? { kind: 'display', field: 'fontSize', delta: 2 }
    : body.question.includes('停用本地索引') ? { kind: 'matching', operation: 'disable', providers: ['local'] }
    : { kind: 'matching', operation: 'prioritize', providers: ['tmdb'] }
  const answer = protection ? '打开访问保护填写密码。'
    : font && body.question.includes('帮我') ? '可以，字体大小只影响当前浏览器。'
    : font ? '在「界面显示」里拖动「字体大小」滑块即可。字号会保存在当前浏览器。'
    : body.question.includes('其他视频库') ? '只对选中的视频库生效，其他库会继续使用自己的匹配顺序。'
    : '打开「匹配规则」，选中电影库，再将 TMDB 移到第一位。保存后，新匹配会优先用它查找资料。'
  return { mode: local ? 'local' : 'ai', topics: [{ id, title, page, anchor, section }],
    answer,
    suggestion: !local && body.question.includes('帮我') ? suggestion : null }
}
let searchFailure = null, matchFailure = null, delayedSearch = null
let delayedResolve
let httpServer, browser, page
const list = items => ({ items, has_more: false })
const response = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
const paid = () => requests.filter(r => /^\/api\/ai\/(check|search\/results|match)$/.test(r.path))
const binds = () => requests.filter(r => r.method === 'POST' && /\/(?:match|bind-external)$/.test(r.path) && !r.path.startsWith('/api/ai/'))
const sampleWorks = [
  ['远山来信', '一封旧信让返乡的父子重新走过山路，日常相处里的沉默是这部片的重心。', 2014],
  ['最后一班夜航', '故事跟随航班停飞后的一名夜班工人，在旅客短暂交会的片刻观察城市生活。', 2015],
  ['沿着海岸慢慢走', '女孩沿海步行重访旧友，几次短暂交谈带出关系的变化，叙事节奏舒缓。', 2016],
]
let nextTurn = 0
const turns = new Map()
function discovery(body) {
  const previous = turns.get(body.session_id)
  const kind = body.kinds?.[0] || body.kind
  const source = body.source || 'library'
  const ranking = body.q.includes('最高')
  const items = sampleWorks.map(([title, reason, year], i) => ({
    key: source + '-' + kind + '-' + i, kind, id: kind === 'tv' ? 201 + i : 101 + i, title, year, reason,
    evidence: [], rating: [8.8, 8.3, 7.8][i], rating_source: 'tmdb', poster_path: 'ai-demo-' + i + '.webp',
    in_library: source === 'library', detail_path: kind === 'tv' ? '/tv/' + (201 + i) : '/m/' + (101 + i),
    ...(source === 'public' ? { external_url: 'https://www.themoviedb.org/movie/123' } : {}),
  }))
  const result = { ok: true, query: body.q, intent: ranking ? 'ranking' : 'recommend', source,
    status: 'results', warnings: [], items, total: items.length, excluded_unknown: 0,
    summary: ranking ? '当前媒体库 · TMDB 评分降序' : '从当前媒体库里选出三部以日常生活为主题的作品。',
    comparison: [], session_id: 'demo-' + ++nextTurn }
  if (body.q === episodeQuestion) {
    // The logical episode is 301; the Mandarin track belongs to file version 307.
    result.items = [{ key: 'library-episode-301', kind: 'episode', id: 301, title: dubbedEpisode.title,
      show_id: show.id, season: 2, episode: 3, in_library: true, version_id: dubbedEpisode.id,
      detail_path: '/tv/201/s/2/e/307', reason: '第二季第三集的第二个文件版本包含国语音轨。' }]
    Object.assign(result, { intent: 'identify', total: 1, summary: '找到指定分集的国语版。' })
  }
  if (previous && body.q.includes('比较')) {
    result.intent = 'compare'
    result.items = previous.items
    result.comparison = previous.items.slice(0, 2).map(i => ({ title: i.title, key: i.key, text: i.reason }))
  }
  turns.set(result.session_id, result)
  return result
}

async function mockApi(route) {
  const request = route.request(), url = new URL(request.url())
  const method = request.method(), body = request.postData() ? request.postDataJSON() : undefined
  const key = url.pathname
  requests.push({ path: key, method, body, query: Object.fromEntries(url.searchParams) })
  if (key === assistantEndpoint && method === 'POST') {
    await beforeAssistantReply?.()
    return response(route, assistantReply(body))
  }
  if (key === browseAssistantEndpoint && method === 'POST') {
    assert.deepEqual(Object.keys(body).sort(), ['channel', 'history', 'question'])
    assert.ok(['movie', 'tv', 'online', 'collections', 'settings'].includes(body.channel))
    const social = socialExamples.find(item => item.question === body.question)
    return response(route, social
      ? { mode: 'social', answer: social.answer, reaction: social.reaction }
      : { mode: settings.enabled && viewingSession.mode === 'normal' ? 'ai' : 'local', answer: '模拟回答：' + body.question, reaction: null })
  }
  if (/^\/api\/libraries\/\d+\/matching-rules$/.test(key) && method === 'PUT') {
    const library = libraries.find(item => item.id === Number(key.split('/').at(-2)))
    assert.ok(library, 'A matching rule requires a known fixture library')
    assert.deepEqual(Object.keys(body).sort(), ['expected', 'providers'])
    await beforeMatchingWrite?.(library)
    if (JSON.stringify(JSON.parse(library.metadata_providers)) !== JSON.stringify(body.expected)) {
      return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '此视频库的匹配规则已变更，请重新核对后确认' }) })
    }
    library.metadata_providers = JSON.stringify(body.providers)
    return response(route, { providers: body.providers })
  }
  if (key === '/api/ai/settings') {
    if (method === 'PATCH') {
      const { api_key: secret, clear_api_key: clear, ...config } = body
      Object.assign(settings, config)
      if (settings.invalid_fields && config.timeout_seconds === 16) delete settings.invalid_fields
      if (secret) Object.assign(settings, { api_key_set: true, api_key_masked: '****' + secret.slice(-4), api_key_source: 'db' })
      if (clear) Object.assign(settings, { api_key_set: true, api_key_masked: '****nkey', api_key_source: 'env' })
    }
    return response(route, settings)
  }
  if (key === '/api/ai/check') {
    settings.usage.requests++
    return response(route, { ok: true, code: 'ok', message: simulatedConnection, usage: settings.usage })
  }
  if (key === '/api/ai/search/results/refresh') return response(route, turns.get(body.session_id))
  if (key === '/api/ai/search/results') {
    if (body.q === '旧库晚到' && delayedResolve) { delayedSearch = route; delayedResolve(); return }
    if (searchFailure) return response(route, { ok: false, code: searchFailure, message: '模拟智能搜索不可用' })
    return response(route, discovery(body))
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
  if (key === '/api/viewing/session' || key === '/api/viewing/activity') return response(route, viewingSession)
  if (key === '/api/viewing/mode' && method === 'POST') {
    assert.ok(['normal', 'children'].includes(body.mode))
    Object.assign(viewingSession, { mode: body.mode, generation: viewingSession.generation + 1 })
    return response(route, viewingSession)
  }
  if (key === '/api/onboarding') return response(route, { show_welcome: false, status: 'completed' })
  if (key === '/api/settings') return response(route, { tmdb_configured: false, tmdb_language: 'zh-CN', libraries: [] })
  if (key === '/api/cache-settings') return response(route, { data_warning_gb: 20, transcode_cache_gb: 10, preview_cache_gb: 10 })
  if (key === '/api/data-usage' && method === 'GET') return response(route, {
    ok: true, total_bytes: 0, warning_bytes: 20 * 1024 ** 3, over_limit: false, error: '',
    categories: { database: 0, posters: 0, previews: 0, transcode: 0, other: 0 },
  })
  if (key === '/api/jobs/stats') return response(route, { total: 1, grouped: 1, versions: 2, needs_review: 0,
    no_match: 0, missing_files: 0, by_library: [] })
  if (key === '/api/tv/stats') return response(route, { shows: 1, seasons: 2, episodes: 3, pending: 0,
    episode_review: 0, by_library: [] })
  if (key === '/api/jobs/tv-ingest') return response(route, { job_id: 'demo-tv-ingest' })
  if (key === '/api/jobs/tv-ingest/demo-tv-ingest') return response(route, { state: 'done', summary: { preview_id: 'demo-tv-ingest', plans: [], metadata_results: [], auto_prompt: false } })
  if (key === '/api/tv/ingestion-reviews') return response(route, { items: [], count: 0 })
  if (key === '/api/metadata/providers') return response(route, { providers: [] })
  if (key === '/api/online/catalog/sources') return response(route, { enabled: true,
    scope_token: 'a'.repeat(64), items: ['1', '2'].map(n => ({ id: 'src_' + n.repeat(32), name: '演示源' + n,
      config_fingerprint: n.repeat(64), capabilities: { browse: true } })) })
  if (key === '/api/online/catalog/health') return response(route, { purposes: {} })
  if (key === '/api/online/state/library') return response(route, { items: [], offset: 0, total: 0, scope_token: 'a'.repeat(64) })
  if (key === '/api/online/catalog/search' || key === '/api/online/catalog/browse') return response(route,
    { items: [], page: 1, page_count: 1, total: 0, categories: [] })
  if (key === '/api/health') return response(route, { ffmpeg: true })
  if (key === '/api/facets' || key === '/api/tv/facets') return response(route, facets)
  if (key === '/api/search') return response(route, list(url.searchParams.get('q') === '模拟无匹配片名' ? [] : [movie]))
  if (key === '/api/tv/shows') return response(route, list([show]))
  if (key === '/api/tv/updates' && method === 'GET') return response(route, { items: [], checked_at: 0, error: '' })
  if (key === '/api/collections/suggest') return response(route, { items: suggestedSeries, topups: [], coverage: { unchecked: 0, standalone: 0 } })
  if (key === '/api/collections/suggest/backfill/status') return response(route, { state: 'idle', done: 0, total: 0, failed: [] })
  if (key === '/api/collections' || /\/(recent-played|similar)$/.test(key)) return response(route, list([]))
  if (key === '/api/search/suggest' || key === '/api/tv/suggest') return response(route, { items: [], persons: [] })
  if (key === '/api/movies/101') return response(route, movie)
  if (key === '/api/tv/shows/201') return response(route, show)
  if (key === '/api/tv/episodes/307' && method === 'GET') return response(route, dubbedEpisode)
  if (key === '/api/tv/shows/201/collection' && method === 'GET') return response(route, {
    show_id: show.id, tmdb_id: show.tmdb_id, media_library_id: show.media_library_id,
    confirmed: !!show.tmdb_id && !show.needs_review, status: '', status_text: '',
    checked_at: 0, next_check_at: 0, error: '', seasons: [], latest_episode: null, missing_seasons: [],
  })
  if (/\/(collection-hint|organize-hint)$/.test(key)) return response(route, { needs: false })
  if (key === '/api/movies/101/files') return response(route, { items: [] })
  if (key === '/api/stream/versions') return response(route, { versions: [], best_version_id: 101 })
  if (key === '/api/stream/progress') return response(route, { position: 0 })
  if (key === '/api/stream/307/previews') return response(route, { state: 'missing' })
  if (key === '/api/cast/current') return response(route, { session: null })
  if (key === '/api/stream/307/decide' && method === 'POST') return response(route, {
    method: 'direct', direct_url: '/demo-playback.mp4', media: { width: 640, height: 360, audio: [], subs: [] },
  })
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
  if (pathname === '/demo-playback.mp4') {
    res.writeHead(200, { 'Content-Type': 'video/mp4' })
    res.end(await readFile(path.join(project, 'docs/assets/videos/organizing.mp4')))
    return true
  }
  const demoPoster = /^\/posters\/ai-demo-([0-2])\.webp$/.exec(pathname)
  if (demoPoster) {
    res.writeHead(200, { 'Content-Type': 'image/svg+xml' })
    res.end(art(Number(demoPoster[1])))
    return true
  }
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

async function checkDiscoveryLayouts(panel) {
  for (const width of [320, 390, 701, 1440]) {
    await page.setViewportSize({ width, height: 844 })
    const layout = await panel.locator('ol > li').evaluateAll(cards => {
      const bounds = cards.map(card => card.getBoundingClientRect())
      return { overflow: document.documentElement.scrollWidth > innerWidth + 1,
        top: bounds[0].top, firstRow: bounds.filter(r => Math.abs(r.y - bounds[0].y) < 1).length }
    })
    assert.equal(layout.overflow, false, 'Discovery fits ' + width)
    assert.equal(layout.firstRow, width <= 700 ? 1 : 2)
    assert.ok(layout.top < 844, 'First work is visible on submission')
  }
}

async function checkGlobalAssistant(go) {
  await page.setViewportSize(desktop)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.clock.install()
  await go('/collections')
  const nav = page.getByRole('navigation', { name: '主导航' })
  const channel = name => nav.getByRole('link', { name, exact: true }).click()
  const launcher = page.getByRole('button', { name: '打开小映', exact: true })
  const panel = page.getByRole('complementary', { name: '小映', exact: true })
    .or(page.getByRole('dialog', { name: '小映', exact: true }))
  const bubble = page.locator('.assistant-tip-bubble')
  const manual = panel.getByRole('checkbox', { name: '只在点开时提示' })
  const aiCalls = () => requests.filter(request => request.path.startsWith('/api/ai/')).length
  const before = aiCalls()
  const captureChannel = async name => {
    if (assistantOnly) await page.screenshot({ path: path.join(project, 'output/playwright/settings-assistant', name + '.png') })
  }

  await page.getByRole('heading', { name: '还没有合集' }).waitFor()
  await launcher.waitFor()
  await page.clock.fastForward(6_000)
  await bubble.filter({ hasText: /合集/ }).waitFor()
  assert.equal(await page.locator('.assistant-launcher').count(), 1)
  assert.equal(await launcher.getAttribute('aria-expanded'), 'false', 'A tip leaves the chat closed')
  await captureChannel('collections-tip')
  await channel('电影')
  await bubble.waitFor({ state: 'hidden' })
  await page.getByRole('combobox', { name: '搜索电影' }).fill('模拟无匹配片名')
  await page.getByRole('button', { name: '搜索电影', exact: true }).click()
  await page.getByRole('heading', { name: '没有符合条件的影片' }).waitFor()
  await page.clock.fastForward(7_000)
  assert.equal(await bubble.isVisible(), false, 'A second eligible channel respects the shared cool-down')
  await launcher.click()
  await panel.getByText(/描述你记得的情节/).waitFor()
  await panel.getByRole('link', { name: /查看本页帮助/ }).waitFor()
  await panel.getByRole('button', { name: '打开智能搜索' }).click()
  await page.getByRole('textbox', { name: '智能找片' }).waitFor()
  assert.equal(await bubble.isVisible(), false)

  await channel('剧集')
  await page.locator('[data-browse-id="' + show.id + '"]').waitFor()
  await launcher.click()
  await panel.getByText(/连载状态/).waitFor()
  await manual.check()
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  await channel('在线')
  await page.getByRole('button', { name: '我的记录', exact: true }).waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  await launcher.click()
  await panel.getByText(/本设备的「我的记录」/).waitFor()
  assert.equal(await manual.isChecked(), true)
  const bounds = await panel.boundingBox()
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391, 'Phone assistant stays inside the viewport')
  await captureChannel('online-mobile')
  await panel.getByRole('button', { name: '查看我的收藏' }).click()
  await page.getByRole('heading', { name: '我的记录' }).waitFor()
  await panel.waitFor({ state: 'hidden' })

  suggestedSeries = [{ collection_tmdb_id: 55, collection_name: '模拟系列', members: [movie], member_count: 1 }]
  await channel('合集')
  await page.reload()
  await page.locator('summary').filter({ hasText: '推荐与可补齐合集' }).waitFor()
  await page.clock.fastForward(7_000)
  assert.equal(await bubble.isVisible(), false, 'Manual-only preference survives a reload with an eligible tip')
  await launcher.click()
  assert.equal(await manual.isChecked(), true)
  await panel.getByRole('button', { name: '查看系列推荐' }).waitFor()
  suggestedSeries = []
  await manual.uncheck()
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  await channel('剧集')
  const resultsHeading = page.getByRole('heading', { name: /^全部剧集/ })
  const idle = page.locator('.assistant-launcher[data-motion="idle"]')
  await resultsHeading.click()
  async function nextGesture() {
    await idle.waitFor()
    // Each gesture lasts under two seconds. Observe the scheduler on its one-second
    // ticks so the test sees it even when the route finished loading between ticks.
    for (let seconds = 0; seconds < 106; seconds++) {
      await page.clock.runFor(1_000)
      const phase = await launcher.getAttribute('data-motion')
      if (phase === 'ambient-wave' || phase === 'ambient-frown') return phase
    }
    assert.fail('Expected an idle gesture within the 60–105 second scheduling window')
  }
  const firstGesture = await nextGesture()
  assert.equal(await bubble.isVisible(), false, 'An idle gesture gets the screen to itself')
  await resultsHeading.click()
  assert.notEqual(await nextGesture(), firstGesture, 'Consecutive gestures use different expressions')
  await page.clock.fastForward(3_000)
  await page.clock.fastForward(240_000)
  await page.locator('.assistant-launcher[data-motion="ambient-sleep"]').waitFor()
  const sortButton = page.getByRole('button', { name: /当前[升降]序，切换为[升降]序/ })
  assert.ok(await sortButton.evaluate(button => {
    const rect = button.getBoundingClientRect()
    return button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2))
  }), 'On a phone the projector clears the sorting controls, including at a previously saved position')
  await captureChannel('sleep-mobile')
  await resultsHeading.click()
  await idle.waitFor()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.locator('.assistant-launcher[data-motion="still"]').waitFor()
  await page.clock.fastForward(240_000)
  assert.equal(await launcher.getAttribute('data-motion'), 'still', 'Reduced motion also suppresses sleep')
  assert.equal(aiCalls(), before, 'Channel tips, gestures and opening existing tools never call the model')
  console.log('PASS 全局小映四频道、跨页冷却、手机布局与静音持久化、空闲打瞌睡、唤醒及减少动态效果')
  await compareAssistantPanels(go)
  await checkChannelChat(go)
}

async function checkChannelChat(go) {
  await page.setViewportSize(desktop)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const launcher = page.getByRole('button', { name: '打开小映', exact: true })
  const panel = page.getByRole('complementary', { name: '小映', exact: true })
    .or(page.getByRole('dialog', { name: '小映', exact: true }))
  const chatRequests = () => requests.filter(item => item.path === browseAssistantEndpoint)
  const managementRequests = () => requests.filter(item => item.path === '/api/ai/settings' || item.path === assistantEndpoint)
  const ask = async (question, channel, expectedHistory) => {
    const prior = chatRequests().length
    await panel.getByRole('textbox').fill(question)
    await panel.getByRole('button', { name: '发送', exact: true }).click()
    const reply = socialExamples.find(item => item.question === question)
    const answer = reply?.answer || '模拟回答：' + question
    await panel.getByText(answer, { exact: true }).last().waitFor()
    const body = chatRequests().at(-1).body
    assert.equal(chatRequests().length, prior + 1)
    assert.equal(body.channel, channel)
    assert.equal(body.question, question)
    if (expectedHistory) assert.deepEqual(body.history, expectedHistory)
    if (reply?.reaction) await panel.locator('.assistant-turn').last()
      .locator('.assistant-avatar[data-pose="' + reply.reaction + '"]').waitFor()
    return { question, answer }
  }
  const openFrom = async (parent, question, channel) => {
    const trigger = parent.getByRole('button', { name: '问小映', exact: true })
    await trigger.click()
    assert.equal(await panel.getAttribute('aria-modal'), 'true')
    await ask(question, channel)
    await panel.getByRole('textbox').press('Escape')
    await panel.waitFor({ state: 'hidden' })
    assert.ok(await parent.isVisible(), 'The underlying dialog remains open')
    await page.waitForFunction(el => el === document.activeElement, await trigger.elementHandle())
  }
  await go('/')
  await page.locator('[data-browse-id="' + movie.id + '"]').waitFor()
  const before = managementRequests().length
  const history = []
  const nav = page.getByRole('navigation', { name: '主导航' })
  for (const [channel, label, question] of [
    ['movie', '电影', '跳个舞'], ['tv', '剧集', '刚才让你做了什么？'],
    ['collections', '合集', '没有模型也能问合集怎么用吗？'], ['online', '在线', '在线记录在哪里？'],
  ]) {
    if (channel !== 'movie') await nav.getByRole('link', { name: label, exact: true }).click()
    if (channel === 'online') await page.setViewportSize({ width: 390, height: 844 })
    const prior = chatRequests().length
    await launcher.click()
    assert.equal(chatRequests().length, prior, 'Opening a channel does not send a chat request')
    if (history.length) await panel.getByText(history.at(-1).answer, { exact: true }).waitFor()
    settings.enabled = channel !== 'collections'
    const turn = await ask(question, channel, history)
    history.push(turn)
    if (channel === 'collections') await panel.getByText('本地回答', { exact: true }).waitFor()
    if (channel === 'online') {
      assert.ok(await panel.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'Mobile chat fits its panel')
      await panel.getByRole('textbox').press('Escape')
    }
  }
  assert.equal(managementRequests().length, before, 'Channel chat never reads model settings or calls the management assistant')
  await page.setViewportSize(desktop)
  await nav.getByRole('link', { name: '电影', exact: true }).click()
  const library = page.getByRole('combobox', { name: '切换媒体库' })
  await library.selectOption('2')
  await page.waitForURL(url => url.searchParams.get('media') === '2')
  await launcher.click()
  assert.equal(await panel.getByText(history.at(-1).answer, { exact: true }).count(), 0)
  await ask('现在是新媒体库吗？', 'movie', [])
  await panel.getByRole('textbox').press('Escape')
  await page.getByRole('button', { name: '全部筛选', exact: true }).click()
  const filters = page.locator('.browse-filters-dialog')
  await openFrom(filters, '怎么操作这个筛选面板？', 'movie')
  await filters.getByRole('button', { name: '取消并关闭筛选' }).click()

  await go('/tv/201/s/2/e/307')
  await page.getByRole('button', { name: '播放', exact: true }).click()
  const player = page.locator('.player-dlg')
  await page.waitForFunction(() => document.querySelector('.player-video')?.readyState >= 2)
  await player.locator('.pv-wrap').hover()
  await player.getByRole('button', { name: '全屏', exact: true }).click()
  await page.waitForFunction(() => !!document.fullscreenElement)
  await player.locator('.pv-wrap').hover()
  await openFrom(player, '播放时怎么设置字幕？', 'tv')
  if (await page.evaluate(() => !!document.fullscreenElement)) {
    await player.locator('.pv-wrap').hover()
    await player.getByRole('button', { name: '退出全屏', exact: true }).click()
  }
  await player.getByRole('button', { name: '关闭播放器' }).click()
  await player.waitFor({ state: 'hidden' })

  viewingSession.enabled = true
  await go('/')
  await launcher.click()
  await ask('刚才的普通模式问题', 'movie', [])
  await panel.getByRole('textbox').press('Escape')
  await page.getByRole('button', { name: '普通模式', exact: true }).click()
  const modes = page.getByRole('dialog', { name: '观看模式' })
  await modes.getByRole('button', { name: /^儿童模式/ }).click()
  await page.getByRole('button', { name: '儿童模式', exact: true }).waitFor()
  await launcher.click()
  await ask('这里的影片怎么找？', 'movie', [])
  await panel.getByText('本地回答', { exact: true }).waitFor()
  assert.equal(await panel.getByText('刚才的普通模式问题', { exact: true }).count(), 0)
  await panel.getByRole('textbox').press('Escape')
  await page.getByRole('button', { name: '儿童模式', exact: true }).click()
  await modes.getByRole('button', { name: /^普通模式/ }).click()
  const auth = page.getByRole('dialog', { name: '退出儿童模式' })
  await auth.getByLabel('保护密码').waitFor()
  assert.equal(await launcher.isVisible(), false, 'Authentication takes precedence over the assistant')
  assert.equal(await auth.getByRole('button', { name: '问小映', exact: true }).count(), 0)
  await auth.getByLabel('保护密码').fill('demo-credential')
  await auth.getByRole('button', { name: '确认', exact: true }).click()
  await page.getByRole('button', { name: '普通模式', exact: true }).waitFor()
  await launcher.click()
  await ask('返回普通模式', 'movie', [])
  viewingSession.enabled = false
  console.log('PASS 四频道提问和跨页追问、表演、手机对话、切库清空、筛选与全屏播放器内提问、观看模式隔离')
}

// Read the rendered surfaces so a channel-specific rule or a missing color
// variable cannot silently replace the shared chat appearance.
function panelAppearance(panel) {
  return panel.evaluate(root => {
    const styles = (element, names) => {
      const style = getComputedStyle(element)
      return Object.fromEntries(names.map(name => [name, style[name]]))
    }
    return {
      shell: styles(root, ['backgroundColor', 'borderColor', 'borderRadius', 'backdropFilter', 'boxShadow']),
      header: styles(root.querySelector('.jz-dialog-header'), ['padding', 'borderBottomColor']),
      title: styles(root.querySelector('h2'), ['color', 'fontSize', 'fontWeight']),
      context: styles(root.querySelector('.assistant-context'), ['color', 'fontSize']),
      avatar: styles(root.querySelector('.assistant-avatar'), ['width', 'height']),
      bubble: styles(root.querySelector('.assistant-bubble'), ['backgroundColor', 'padding', 'borderRadius']),
      shortcut: styles(root.querySelector('.assistant-starters button'), ['backgroundColor', 'borderColor', 'borderRadius']),
    }
  })
}

async function compareAssistantPanels(go) {
  Object.assign(settings, { enabled: true, api_key_set: true })
  const directory = path.join(project, 'output/playwright/assistant-panel/after')
  if (assistantOnly) await mkdir(directory, { recursive: true })
  for (const viewport of [desktop, { width: 390, height: 844 }, { width: 320, height: 720 }]) {
    await page.setViewportSize(viewport)
    let reference
    for (const [channel, route] of [['settings', '/settings'], ['movie', '/']]) {
      const isSettings = channel === 'settings'
      await go(route)
      if (!isSettings) await page.locator('[data-browse-id="' + movie.id + '"]').waitFor()
      const launcher = page.getByRole('button', { name: isSettings ? '问设置助手' : '打开小映', exact: true })
      await launcher.focus()
      await launcher.press('Home')
      await launcher.click()
      const role = viewport.width <= 700 ? 'dialog' : 'complementary'
      const panel = page.getByRole(role, { name: isSettings ? '设置助手' : '小映', exact: true })
      await panel.getByRole('heading', { name: '小映', exact: true }).waitFor()
      await panel.getByRole('group', { name: '小映回复' }).first().waitFor()
      await panel.getByText(isSettings ? /片场的小放映机/ : /描述你记得的情节/).waitFor()
      const dock = panel.getByRole('combobox', { name: '停靠位置' })
      assert.equal(await dock.inputValue(), 'top-right')
      const appearance = await panelAppearance(panel)
      if (isSettings) {
        reference = appearance
        assert.ok(reference.shell.backdropFilter.includes('blur('))
      } else {
        assert.deepEqual(appearance, reference, 'Browsing and settings share the chat appearance at ' + viewport.width)
        await panel.locator('.assistant-context').filter({ hasText: '电影' }).waitFor()
        await panel.getByRole('link', { name: /查看本页帮助/ }).waitFor()
      }
      assert.ok(await panel.evaluate(el => {
        const rect = el.getBoundingClientRect(), body = el.querySelector('.jz-dialog-body')
        return rect.left >= -1 && rect.right <= innerWidth + 1 && rect.top >= -1 && rect.bottom <= innerHeight + 1 &&
          body.clientHeight > 0 && body.scrollWidth <= body.clientWidth + 1
      }), 'Chat content is readable within the viewport at ' + viewport.width)
      if (assistantOnly) await page.screenshot({ path: path.join(directory, channel + '-' + viewport.width + '.png'), animations: 'disabled' })
      if (!isSettings) {
        await dock.selectOption('bottom-left')
        await page.waitForFunction(() => document.querySelector('.assistant-launcher').getBoundingClientRect().left < innerWidth / 2)
      }
      await panel.getByRole('button', { name: isSettings ? '关闭设置助手' : '收起小映' }).click()
      await panel.waitFor({ state: 'hidden' })
    }
  }
  await go('/m/' + movie.id)
  await page.locator('.movie-detail h1').waitFor()
  const launcher = page.getByRole('button', { name: '打开小映', exact: true })
  await launcher.focus()
  await launcher.press('ArrowRight')
  await page.waitForFunction(() => document.querySelector('.assistant-launcher').getBoundingClientRect().left > innerWidth / 2)
  await launcher.press('Enter')
  const detailPanel = page.getByRole('dialog', { name: '小映', exact: true })
  await detailPanel.waitFor()
  assert.equal(await detailPanel.getByRole('list', { name: '本页提示' }).count(), 0)
  assert.equal(await detailPanel.getByRole('link', { name: /查看本页帮助/ }).getAttribute('href'), '/help/user-guide/movies.html')
  await page.keyboard.press('Escape')
  await detailPanel.waitFor({ state: 'hidden' })
  assert.ok(await launcher.evaluate(el => el === document.activeElement), 'Closing the detail helper returns keyboard focus')
  console.log('PASS 1440/390/320px 设置与浏览小映的玻璃外壳、标题、停靠位置、头像、回复气泡和快捷按钮一致')
}

async function checkSettingsAssistant(go) {
  Object.assign(settings, { enabled: true, api_key_set: true })
  const launcher = page.getByRole('button', { name: '问设置助手', exact: true })
  const floating = page.getByRole('complementary', { name: '设置助手', exact: true })
  const modal = page.getByRole('dialog', { name: '设置助手', exact: true })
  const dialog = floating.or(modal)
  const proposal = dialog.getByRole('region', { name: '配置建议' })
  const confirm = proposal.getByRole('button', { name: '确认应用' })
  const target = proposal.getByLabel('目标视频库')
  const rules = page.locator('#sec-matching')
  const captureAssistant = async name => {
    if (!assistantOnly) return
    const directory = path.join(project, 'output/playwright/settings-assistant')
    await mkdir(directory, { recursive: true })
    await page.screenshot({ path: path.join(directory, name + '.png'), animations: 'disabled' })
  }
  const open = async () => { if (!await dialog.isVisible()) await launcher.click() }
  const motion = state => page.locator('.assistant-launcher[data-motion="' + state + '"]')
  const holdReply = async action => {
    let release
    const gate = new Promise(resolve => { release = resolve })
    beforeAssistantReply = () => gate
    try { await action(page.waitForRequest('**' + assistantEndpoint)) }
    finally { beforeAssistantReply = undefined; release() }
  }
  // Read the active artwork, including the OS preference's <picture> selection.
  const checkReducedMotion = async (expectIdleArt = true) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    try {
      await page.waitForFunction(expectIdle => {
        const button = document.querySelector('.assistant-launcher')
        const panel = document.querySelector('#settings-assistant-panel')
        return (!expectIdle || button.querySelector('img').currentSrc.includes('projector-idle')) &&
          [button, panel].filter(Boolean).every(root =>
            !root.getAnimations({ subtree: true }).some(animation => animation instanceof CSSAnimation))
      }, expectIdleArt)
    } finally { await page.emulateMedia({ reducedMotion: 'no-preference' }) }
  }
  const ask = async text => {
    await dialog.getByRole('textbox').fill(text)
    await dialog.getByRole('button', { name: '发送' }).click()
    await dialog.getByRole('heading', { name: text, exact: true }).last().waitFor()
  }
  const checkLinks = async (id, label) => {
    const { title, page: guide, anchor, section } = assistantTopics.find(topic => topic.id === id)
    const link = dialog.getByRole('link', { name: new RegExp(title) }).last()
    assert.equal(decodeURIComponent(await link.getAttribute('href')), '/help/' + guide + '.html#' + anchor)
    assert.equal(await link.getAttribute('target'), '_blank')
    const settingsLink = dialog.getByRole('link', { name: '打开' + label, exact: true }).last()
    assert.equal(await settingsLink.getAttribute('href'), '/settings?sec=' + section)
  }

  // Measure the actual visible boxes, so a fixed but disconnected chat or a
  // three-column grid with an apparent overlay both fail this regression.
  const checkAttached = async (where, side) => {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    const { pet, panel, w, h, petCanReceiveClicks } = await page.evaluate(() => {
      const launcher = document.querySelector('.assistant-launcher')
      const rect = node => {
        const { left, top, right, bottom, width, height } = node.getBoundingClientRect()
        return { left, top, right, bottom, width, height }
      }
      const pet = rect(launcher)
      return { pet, panel: rect(document.querySelector('#settings-assistant-panel')), w: innerWidth, h: innerHeight,
        petCanReceiveClicks: launcher.contains(document.elementFromPoint(pet.left + pet.width / 2, pet.top + pet.height / 2)) }
    })
    const hGap = Math.max(0, panel.left - pet.right, pet.left - panel.right)
    const vGap = Math.max(0, panel.top - pet.bottom, pet.top - panel.bottom)
    const context = where + ' ' + JSON.stringify({ pet, panel, w, h })
    assert.ok((hGap === 0 || vGap === 0) && Math.max(hGap, vGap) <= 24, 'Panel detached at ' + context)
    assert.ok(panel.left >= -1 && panel.top >= -1 && panel.right <= w + 1 && panel.bottom <= h + 1,
      'Panel escaped viewport at ' + context)
    assert.ok(petCanReceiveClicks, 'Launcher is covered at ' + context)
    if (side === 'right') assert.ok(pet.left > w / 2 && panel.right <= pet.right + 1, context)
    if (side === 'left') assert.ok(pet.right < w / 2 && panel.left >= pet.left - 1, context)
    return panel
  }
  const settingsBoxes = () => page.locator('.side-nav, .settings-main, #sec-status .settings-shortcuts').evaluateAll(nodes =>
    nodes.map(node => {
      const { x, y, width, height } = node.getBoundingClientRect()
      return { x, y, width, height }
    }))

  await page.setViewportSize({ width: 1700, height: 900 })
  await go('/settings')
  await page.locator('#sec-status .settings-shortcuts').waitFor()
  await page.locator('#sec-status .overview-clear').waitFor()
  const overviewBoxes = await settingsBoxes()
  await motion('idle').waitFor()
  const restingBox = await launcher.boundingBox()
  const movingOnlyArtwork = await launcher.evaluate(node => {
    const animations = node.getAnimations({ subtree: true }).filter(a => a instanceof CSSAnimation)
    return animations.length > 0 && animations.every(a => a.effect.target !== node && node.contains(a.effect.target))
  })
  assert.ok(movingOnlyArtwork, 'Idle motion is confined to the artwork within the button')
  await checkReducedMotion()
  assert.deepEqual(await launcher.boundingBox(), restingBox, 'Motion preferences preserve the launcher hit area')
  await open()
  await dialog.getByRole('textbox').waitFor()
  await checkAttached('opening on the overview', 'right')
  assert.deepEqual(await settingsBoxes(), overviewBoxes, 'The overview and navigation retain their normal footprint')
  const glass = await dialog.evaluate(node => {
    const style = getComputedStyle(node)
    const paint = document.createElement('canvas').getContext('2d')
    paint.fillStyle = style.backgroundColor
    paint.fillRect(0, 0, 1, 1)
    return { alpha: paint.getImageData(0, 0, 1, 1).data[3], filter: style.backdropFilter }
  })
  assert.ok(glass.alpha > 0 && glass.alpha < 255 && glass.filter.includes('blur('), 'Floating chat has a translucent, blurred shell')
  assert.equal(requests.filter(r => r.path === assistantEndpoint).length, 0, 'Opening reads only the saved model configuration')
  await captureAssistant('overview-right')
  await ask('电影匹配时，怎么优先使用 TMDB？')
  await ask('会影响其他视频库吗？')
  await captureAssistant('conversation')
  const chooseCorner = async corner => dialog.getByRole('combobox', { name: '停靠位置' }).selectOption(corner)
  for (const corner of ['top-left', 'bottom-left', 'bottom-right', 'top-right']) {
    await chooseCorner(corner)
    await checkAttached(corner, corner.split('-')[1])
    assert.deepEqual(await settingsBoxes(), overviewBoxes)
    if (corner === 'top-left') await captureAssistant('overview-left')
    if (corner === 'bottom-left') await captureAssistant('conversation-left')
  }
  const initial = await launcher.boundingBox()
  await page.mouse.move(initial.x + 24, initial.y + 24)
  await page.mouse.down()
  await page.mouse.move(610, 460, { steps: 12 })
  await launcher.locator('img[src*="projector-drag"]').waitFor()
  await motion('drag').waitFor()
  const traversing = await checkAttached('drag in progress')
  const heldBox = await launcher.boundingBox()
  await checkReducedMotion()
  assert.deepEqual(await launcher.boundingBox(), heldBox, 'Drag feedback does not shift the hit area')
  await page.mouse.up()
  await motion('settle').waitFor()
  const snapped = await checkAttached('custom left height', 'left')
  assert.ok(Math.abs(snapped.left - traversing.left) > 100, 'Releasing moves the open chat to its docked position')
  assert.deepEqual(await settingsBoxes(), overviewBoxes)
  await launcher.press('Home')
  await page.setViewportSize({ width: 820, height: 820 })
  await floating.waitFor()
  await checkAttached('tablet', 'right')
  await page.setViewportSize({ width: 1700, height: 900 })
  await dialog.getByRole('button', { name: '关闭设置助手' }).click()
  assert.deepEqual(await settingsBoxes(), overviewBoxes, 'Closing the chat restores the same overview')

  await page.setViewportSize(desktop)
  await go('/settings?sec=sec-matching')
  const beforeDrag = await launcher.boundingBox()
  assert.equal(beforeDrag.width, 56)
  await page.mouse.move(beforeDrag.x + 24, beforeDrag.y + 24)
  await page.mouse.down()
  await page.mouse.move(72, 300, { steps: 10 })
  await launcher.locator('img[src*="projector-drag"]').waitFor()
  await page.mouse.up()
  assert.equal(await dialog.count(), 0, 'Releasing a drag only docks the projector')
  assert.ok((await launcher.boundingBox()).x < 100)
  await page.reload()
  await launcher.waitFor()
  assert.ok((await launcher.boundingBox()).x < 100, 'Docked side survives reload')
  await launcher.press('Home')
  await open()
  await floating.waitFor()
  assert.equal(await modal.count(), 0)
  assert.equal(await floating.getAttribute('aria-modal'), null)
  assert.equal(requests.filter(r => r.path === assistantEndpoint).length, 2, 'Reopening does not start a new model request')
  const firstQuestion = '怎么让电影库优先用 TMDB？'
  await holdReply(async started => {
    await dialog.getByRole('textbox').fill(firstQuestion)
    await dialog.getByRole('button', { name: '发送' }).click()
    await started
    const pending = dialog.locator('.assistant-pending')
    await pending.getByText(firstQuestion, { exact: true }).waitFor()
    await pending.getByRole('status').waitFor()
    const thinking = motion('thinking')
    await thinking.waitFor()
    assert.ok(await thinking.locator('.projector-reel').evaluateAll(reels => reels.length === 2 &&
      reels.every(reel => reel.getAnimations().some(a => a instanceof CSSAnimation))),
    'Both film reels turn while the model request is pending')
    await checkReducedMotion()
    assert.equal(await proposal.count(), 0)
  })
  await motion('reply').waitFor()
  await dialog.locator('.assistant-reply-arriving .assistant-bubble').waitFor()
  await dialog.getByRole('heading', { name: firstQuestion, exact: true }).waitFor()
  await motion('idle').waitFor()
  await checkLinks('matching', '匹配规则')
  const turn = dialog.locator('.assistant-turn').last()
  const outgoing = await turn.locator('.assistant-question').boundingBox()
  const incoming = await turn.locator('.assistant-answer').boundingBox()
  assert.ok(outgoing.x + outgoing.width > incoming.x + incoming.width && outgoing.x > incoming.x,
    'User and assistant messages are aligned to opposite edges')
  await captureAssistant('desktop')
  assert.equal(await proposal.count(), 0, 'Asking for instructions has no proposed write')
  await ask('帮我调整这个顺序')
  const followUp = requests.filter(r => r.path === assistantEndpoint).at(-1).body
  assert.equal(followUp.section, 'sec-matching')
  assert.equal(followUp.history[0].question, '怎么让电影库优先用 TMDB？')
  assert.equal(await target.locator('option:checked').innerText(), '选择要修改的视频库')
  assert.equal(await confirm.count(), 0)
  await target.selectOption('3')
  assert.match(await target.locator('option:checked').innerText(), /模拟媒体库乙.*电影/)
  assert.deepEqual(await proposal.locator('dd').allTextContents(), ['本地索引', 'TMDB → 本地索引'])
  // The underlying real form remains usable while an unapplied proposal is visible.
  await rules.getByLabel('视频库', { exact: true }).selectOption('1')
  assert.equal(await target.inputValue(), '3')
  await proposal.getByRole('button', { name: '取消', exact: true }).click()
  await proposal.getByText('已取消修改').waitFor()
  assert.equal(ruleWrites().length, 0)
  await ask('帮我优先使用 TMDB')
  await target.selectOption('3')
  await confirm.evaluate(button => { button.click(); button.click() })
  await proposal.getByText('已保存匹配规则').waitFor()
  assert.deepEqual(ruleWrites().map(r => [r.path, r.body]), [[
    '/api/libraries/3/matching-rules', { expected: ['local'], providers: ['tmdb', 'local'] },
  ]])
  await proposal.getByRole('link', { name: '查看已保存规则' }).click()
  await page.waitForURL(url => url.searchParams.get('library') === '3')
  assert.equal(await rules.getByLabel('视频库', { exact: true }).inputValue(), '3')
  assert.match(await rules.getByRole('list', { name: '已启用来源的匹配顺序' }).innerText(), /^1\. TMDB/)

  await rules.getByLabel('视频库', { exact: true }).selectOption('2')
  await rules.getByLabel('TVmaze', { exact: true }).check()
  await open()
  await ask('帮我调整视频库的优先级')
  await target.selectOption('2')
  await proposal.getByText(/请先在原表单处理草稿/).waitFor()
  assert.equal(await confirm.count(), 0)
  await proposal.getByRole('link', { name: '查看本库草稿' }).click()
  assert.equal(await rules.getByLabel('TVmaze', { exact: true }).isChecked(), true)
  await rules.getByRole('button', { name: '放弃本库修改' }).click()
  assert.equal(ruleWrites().length, 1)

  await open()
  await ask('帮我让 TMDB 优先')
  await target.selectOption('4')
  expectedConflictPath = '/api/libraries/4/matching-rules'
  beforeMatchingWrite = library => { library.metadata_providers = '["local","wikidata"]' }
  await confirm.click()
  await proposal.getByRole('alert').filter({ hasText: /重新核对/ }).waitFor()
  beforeMatchingWrite = undefined
  assert.equal(await confirm.count(), 0)
  await proposal.getByRole('button', { name: '重新核对' }).click()
  await proposal.getByText('TMDB → 本地索引 → Wikidata').waitFor()
  assert.equal(ruleWrites().length, 2, 'Reviewing a conflict cannot write')
  await confirm.click()
  await proposal.getByText('已保存匹配规则').waitFor()
  expectedConflictPath = undefined
  assert.deepEqual(ruleWrites().at(-1).body, { expected: ['local', 'wikidata'], providers: ['tmdb', 'local', 'wikidata'] })
  await ask('帮我停用本地索引')
  await target.selectOption('1')
  await proposal.getByText('至少保留一个匹配来源。', { exact: true }).waitFor()
  assert.equal(await confirm.count(), 0)
  await target.selectOption('4')
  assert.deepEqual(await proposal.locator('dd').allTextContents(), ['TMDB → 本地索引 → Wikidata', 'TMDB → Wikidata'])
  assert.equal(ruleWrites().length, 3)
  await confirm.click()
  await proposal.getByRole('link', { name: '查看已保存规则' }).click()
  await page.waitForURL(url => url.searchParams.get('library') === '4')
  const savedOrder = rules.getByRole('list', { name: '已启用来源的匹配顺序' })
  assert.deepEqual(await savedOrder.locator('li > span').allTextContents(), ['1. TMDB', '2. Wikidata'])
  assert.deepEqual(ruleWrites().at(-1).body, { expected: ['tmdb', 'local', 'wikidata'], providers: ['tmdb', 'wikidata'] })
  await open()
  await ask('帮我调整匹配来源')
  await page.locator('.desktop-categories').getByRole('button', { name: '界面显示' }).click()
  await floating.getByRole('heading', { name: '帮我调整匹配来源' }).waitFor()
  assert.equal(await proposal.count(), 0, 'Changing section retires the previous proposal')
  await ask('这里可以设置什么？')
  assert.equal(requests.filter(r => r.path === assistantEndpoint).at(-1).body.section, 'sec-display')
  console.log('PASS 设置助手问答、教程、显式选库和确认、取消、连续点击、未保存草稿与过期规则重新核对')

  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await go('/settings?sec=sec-display')
    assert.equal((await launcher.boundingBox()).width, 48)
    const slider = page.getByLabel(/^字体大小/)
    const previous = Number(await slider.inputValue())
    await open()
    await modal.waitFor()
    assert.equal(await floating.count(), 0)
    await modal.getByRole('textbox').waitFor()
    await checkAttached(width + 'px mobile')
    for (const key of ['Tab', 'Shift+Tab']) {
      await page.keyboard.press(key)
      assert.ok(await modal.evaluate(el => el.contains(document.activeElement)), 'Mobile chat keeps keyboard focus')
    }
    if (width === 390) {
      await modal.getByRole('textbox').focus()
      await page.setViewportSize({ width, height: 450 })
      const short = await checkAttached('short mobile viewport')
      const composer = await modal.getByRole('textbox').boundingBox()
      assert.ok(composer.height > 0 && composer.y + composer.height <= short.bottom, 'Composer fits when the visible height shrinks')
      await page.setViewportSize({ width, height: 844 })
    }
    await ask('帮我把字体调大一点')
    if (width === 390 && assistantOnly) {
      await confirm.scrollIntoViewIfNeeded()
      await captureAssistant('mobile-confirmation')
    }
    assert.deepEqual(await proposal.locator('dd').allTextContents(), [previous + ' px', (previous + 2) + ' px'])
    assert.equal(await slider.inputValue(), String(previous))
    const layout = await dialog.evaluate(node => ({
      page: document.documentElement.scrollWidth, viewport: innerWidth,
      content: node.scrollWidth, dialog: node.clientWidth,
      overflowing: [...node.querySelectorAll('*')].filter(el => el.clientWidth && el.scrollWidth > el.clientWidth + 1)
        .map(el => [el.tagName, el.className, el.clientWidth, el.scrollWidth]),
    }))
    assert.ok(layout.content <= layout.dialog + 1 && layout.page <= layout.viewport + 1,
      width + 'px assistant overflow: ' + JSON.stringify(layout))
    let reviewedFontSize = previous
    if (width === 320) {
      const otherTab = await page.context().newPage()
      await otherTab.goto(page.url())
      await otherTab.getByLabel(/^字体大小/).press('ArrowLeft')
      await otherTab.getByLabel(/^海报大小/).press('End')
      reviewedFontSize = Number(await otherTab.getByLabel(/^字体大小/).inputValue())
      assert.equal(reviewedFontSize, previous - 1)
      await otherTab.close()

      await confirm.click()
      await proposal.getByRole('alert').filter({ hasText: /显示偏好已变化/ }).waitFor()
      assert.equal(await confirm.count(), 0)
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('jz.prefs')))
      assert.equal(stored.fontSize, reviewedFontSize)
      assert.equal(stored.posterMin, 200)
      await proposal.getByRole('button', { name: '重新核对' }).click()
      assert.deepEqual(await proposal.locator('dd').allTextContents(), [reviewedFontSize + ' px', (reviewedFontSize + 2) + ' px'])
    }
    await confirm.click()
    await proposal.getByRole('link', { name: '查看显示设置' }).click()
    await modal.waitFor({ state: 'hidden' })
    assert.equal(await slider.inputValue(), String(reviewedFontSize + 2))
    await page.reload()
    assert.equal(await slider.inputValue(), String(reviewedFontSize + 2), 'Browser preference survives reload')
    assert.equal(await page.getByLabel(/^海报大小/).inputValue(), '200', 'Keep the poster size saved by the other tab')
  }
  await go('/settings?sec=sec-ai')
  const modelName = page.getByLabel('模型名称', { exact: true })
  await modelName.fill('unsaved-test-model')
  await open()
  await ask('怎么调整字体？')
  const openDisplay = modal.getByRole('link', { name: '打开界面显示', exact: true })
  const leaveGuard = page.getByRole('dialog', { name: '设置尚未保存' })
  await openDisplay.click()
  await leaveGuard.getByRole('button', { name: '继续编辑', exact: true }).click()
  await leaveGuard.waitFor({ state: 'hidden' })
  await modal.getByRole('heading', { name: '怎么调整字体？', exact: true }).waitFor()
  assert.equal(await modelName.inputValue(), 'unsaved-test-model')
  assert.equal(new URL(page.url()).searchParams.get('sec'), 'sec-ai')
  await openDisplay.click()
  await leaveGuard.getByRole('button', { name: '放弃修改并离开' }).click()
  await page.waitForURL(url => url.searchParams.get('sec') === 'sec-display')
  await modal.waitFor({ state: 'hidden' })
  await open()
  await ask('界面字体怎么改？')
  await captureAssistant('mobile')
  await openDisplay.click()
  await modal.waitFor({ state: 'hidden' })
  settings.enabled = false
  await go('/settings')
  await open()
  const sends = requests.filter(r => r.path === assistantEndpoint).length
  await dialog.getByText('去配置智能辅助', { exact: true }).waitFor()
  await dialog.getByRole('link', { name: /配置教程/ }).waitFor()
  await ask('你好啊')
  await dialog.getByText(socialExamples.find(example => example.question === '你好啊').answer, { exact: true }).waitFor()
  await dialog.getByText('去配置智能辅助', { exact: true }).click()
  await page.waitForURL(url => url.searchParams.get('sec') === 'sec-ai')
  await page.getByLabel('服务商', { exact: true }).waitFor()
  Object.assign(settings, { enabled: true, api_key_set: false })
  await open()
  await dialog.getByText('去配置智能辅助', { exact: true }).waitFor()
  await ask('怎么调整字体？')
  await dialog.getByText('回答来自本版手册', { exact: true }).waitFor()
  await dialog.getByRole('button', { name: /关闭/ }).first().click()
  settings.provider = 'compatible'
  await open()
  await dialog.getByRole('textbox').waitFor()
  assert.equal(requests.filter(r => r.path === assistantEndpoint).length, sends + 2)
  assert.equal(await proposal.count(), 0)
  assert.equal(ruleWrites().length, 4)
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'hidden' })
  assert.equal(await launcher.evaluate(el => document.activeElement === el), true)
  await open()
  await holdReply(async started => {
    await dialog.getByRole('textbox').fill('打开字体设置的方法是什么？')
    await dialog.getByRole('button', { name: '发送' }).click()
    await started
    await motion('thinking').waitFor()
    const aborted = page.waitForEvent('requestfailed', req => new URL(req.url()).pathname === assistantEndpoint)
    await dialog.getByRole('button', { name: '关闭设置助手' }).click()
    await aborted
    await motion('idle').waitFor()
  })
  await open()
  await dialog.getByRole('textbox').waitFor()
  assert.equal(await dialog.locator('.assistant-turn').count(), 0, 'Closing a pending request leaves a fresh chat on reopen')
  await dialog.getByRole('heading', { name: '小映', exact: true }).waitFor()
  const writesBeforeChat = requests.filter(r => r.method === 'PUT').length
  for (const { question, answer, reaction } of socialExamples) {
    await ask(question)
    const newest = dialog.locator('.assistant-turn').last()
    await newest.getByText(answer, { exact: true }).waitFor()
    assert.equal(await newest.getByRole('link').count(), 0, 'Small talk has no tutorial or settings links')
    assert.equal(await newest.locator('.assistant-source').count(), 0)
    assert.equal(await proposal.count(), 0)
    if (reaction) {
      const performing = motion(reaction)
      await performing.waitFor()
      const posing = dialog.locator('.assistant-reply .projector-character[data-pose="' + reaction + '"]')
      await newest.locator('.projector-character[data-pose="' + reaction + '"]').waitFor()
      assert.equal(await posing.count(), 1, 'Only the newest reply performs')
      assert.ok(await performing.evaluate(node => node.getAnimations({ subtree: true })
        .some(a => a instanceof CSSAnimation && a.playState === 'running')), 'Performs ' + reaction)
      if (reaction === 'dance') await checkReducedMotion(false)
    } else await motion('reply').waitFor()
  }
  await motion('idle').waitFor()
  assert.equal(requests.filter(r => r.method === 'PUT').length, writesBeforeChat)
  await ask('小映你好，怎么调整字体大小？')
  await checkLinks('display', '界面显示')
  assert.equal(await proposal.count(), 0, 'Asking about a setting after chatting remains read-only')
  await dialog.getByRole('button', { name: '关闭设置助手' }).click()
  Object.assign(settings, { enabled: false, provider: 'deepseek', api_key_set: false })
  await page.evaluate(() => localStorage.removeItem('jz.prefs'))
  await page.setViewportSize(desktop)
  console.log('PASS 半透明浮窗与设置布局、四角停靠及拖动跟随、双向气泡和等待状态、320/390px 手机确认及模型引导')
  console.log('PASS 放映机待机、拖动与回稳、等待与应答、关闭取消和减少动态效果')
  console.log('PASS 小映的自我介绍、问候、笑哭舞动作及减弱动效，闲聊后可以继续查设置教程')
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
      recording_script: 'scripts/smoke_ai_ui.mjs ' + (searchCapture ? '--capture-search' : '--capture-docs'),
      fixtures: 'scripts/smoke_ai_ui.mjs 内置虚构资料与模拟响应',
      ...(name === 'ai-search-preview' ? { artwork_sha256: artworkFingerprint } : {}),
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
  await page.getByRole('textbox', { name: '智能找片' }).fill('我没看过的文艺片，想看普通人的生活')
  await page.getByRole('button', { name: '找片', exact: true }).click()
  await search.getByRole('link', { name: '远山来信' }).waitFor()
  await screenshot('ai-search-preview', 'user-guide/find-movies.md', '模拟片单的推荐理由、范围与后续操作', search)
  for (const kind of searchCapture ? [] : ['movie', 'tv']) {
    if (kind === 'tv') show.tmdb_id = 777 // A directory-locked show can only refresh its current TMDB identity.
    await go(kind === 'movie' ? '/m/101' : '/tv/201')
    if (kind === 'tv') {
      await page.locator('summary').filter({ hasText: '更多操作' }).click()
      await page.getByRole('button', { name: '重新匹配剧集', exact: true }).click()
    }
    if (kind === 'movie' || !show.tmdb_id) await page.getByRole('button', { name: '匹配资料', exact: true }).click()
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
  const replacements = new Map(entries.map(entry => [entry.file, entry]))
  const previous = new Set(manifest.assets.map(entry => entry.file))
  manifest.assets = [...manifest.assets.map(entry => replacements.get(entry.file) || entry),
    ...entries.filter(entry => !previous.has(entry.file))]
  await writeFile(manifestFile, JSON.stringify(manifest, null, 2) + '\n')
  console.log('Updated AI entries only: docs/assets/manifest.json; raw PNGs: ' + temporary)
}

async function runSmoke() {
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
      else if (/^\/(?:settings|collections|online|tv(?:\/\d+(?:\/s\/\d+\/e\/\d+)?)?|m\/\d+)?\/?$/.test(pathname)) file = path.join(output, 'index.html')
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
  page.on('response', r => {
    const pathname = new URL(r.url()).pathname
    if (r.status() >= 400 && !(r.status() === 409 && pathname === expectedConflictPath)) errors.push(`${r.status()} ${pathname}`)
  })
  page.on('console', message => {
    if (message.type() !== 'error') return
    if (expectedConflictPath && message.location().url === base + expectedConflictPath &&
      /Failed to load resource:.*409/.test(message.text())) return
    errors.push(message.text())
  })
  const go = async pathname => {
    await page.goto(base + pathname)
    if (pathname.startsWith('/settings')) await page.locator('.settings-layout').waitFor()
    else await page.getByRole('combobox', { name: '切换媒体库' }).waitFor()
  }

  await checkSettingsAssistant(go)
  assert.deepEqual(unexpected, [])
  assert.deepEqual(errors, [])
  if (assistantOnly) {
    await checkGlobalAssistant(go)
    assert.deepEqual(unexpected, [])
    assert.deepEqual(errors, [])
    return
  }
  settings.invalid_fields = { timeout_seconds: '超时秒数须为整数' }
  await go('/settings?sec=sec-ai')
  const config = page.locator('.ai-settings')
  await config.getByText('超时秒数须为整数', { exact: true }).waitFor()
  assert.ok(await config.getByRole('button', { name: '测试已保存连接' }).isDisabled())
  await config.getByLabel('单次超时（秒）').fill('16')
  const repair = page.waitForRequest(req => new URL(req.url()).pathname === '/api/ai/settings' && req.method() === 'PATCH')
  await config.getByRole('button', { name: '保存智能辅助配置' }).click()
  assert.deepEqual((await repair).postDataJSON(), { timeout_seconds: 16 })
  await config.getByText('智能辅助配置需要修正', { exact: true }).waitFor({ state: 'hidden' })
  assert.equal(paid().length, 0)
  await go('/settings?sec=sec-ai')
  await config.getByRole('button', { name: '配置与测试服务' }).click()
  await config.getByRole('button', { name: '保存智能辅助配置' }).waitFor()
  const desktopGroups = await page.locator('.desktop-categories > section').evaluateAll(groups => groups.map(group => {
    const box = group.getBoundingClientRect(), heading = group.querySelector('.nav-group').getBoundingClientRect()
    return { x: box.x, y: box.y, width: box.width, height: heading.height }
  }))
  assert.equal(desktopGroups.length, 4)
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
  await config.locator('summary').filter({ hasText: '调用限额、超时与密钥管理' }).click()
  await config.getByRole('button', { name: '移除已保存密钥', exact: true }).click()
  assert.equal(requests.filter(r => r.path === '/api/ai/settings' && r.method === 'PATCH').length, writesBeforeClear)
  await config.getByRole('button', { name: '确认移除已保存密钥', exact: true }).click()
  await config.getByText('已移除设置页密钥，现使用服务器环境密钥', { exact: true }).waitFor()
  console.log('PASS 模拟设置保存、独立连接测试、密钥确认移除和环境回退')

  for (const kind of ['movie', 'tv']) {
    await go(kind === 'movie' ? '/?media=1' : '/tv?media=1')
    const count = paid().length
    await page.getByRole('button', { name: '智能搜索', exact: true }).click()
    const panel = page.getByRole('region', { name: '智能搜索', exact: true })
    const input = page.getByRole('textbox', { name: '智能找片' })
    await input.fill('今年评分最高的10部作品')
    assert.equal(paid().length, count)
    await input.press('Enter')
    await panel.getByRole('link', { name: '远山来信' }).waitFor()
    assert.equal(paid().length, count + 1)
    assert.equal(paid().at(-1).body.media_library_id, 1)
    assert.equal(paid().at(-1).body.kind, kind)
    assert.equal(await panel.getByRole('listitem').count(), 3)
    await panel.getByRole('button', { name: '比较前两部' }).click()
    await panel.getByRole('heading', { name: '片单比较' }).waitFor()
    assert.ok(paid().at(-1).body.session_id)
    const spend = paid().length
    await panel.getByRole('link', { name: '远山来信' }).click()
    await page.getByRole('link', { name: '返回片单' }).click()
    await panel.getByRole('link', { name: '远山来信' }).waitFor()
    assert.equal(paid().length, spend, 'Returning to a list never calls the model')
    await page.getByRole('button', { name: '普通搜索', exact: true }).click()
    await page.getByRole('combobox', { name: kind === 'movie' ? '搜索电影' : '搜索剧集' }).waitFor()
  }
  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  const mixed = page.getByRole('region', { name: '智能搜索', exact: true })
  await mixed.getByLabel('作品类型').selectOption('tv')
  await page.getByRole('textbox', { name: '智能找片' }).fill('找一部慢节奏的剧')
  await page.getByRole('button', { name: '找片', exact: true }).click()
  await mixed.getByRole('link', { name: '远山来信' }).click()
  await page.waitForURL(url => url.pathname === '/tv/201')
  await page.getByRole('link', { name: '返回片单' }).click()
  await page.waitForURL(url => url.pathname === '/' && url.searchParams.get('media') === '1')
  await mixed.getByRole('link', { name: '远山来信' }).waitFor()
  assert.deepEqual(requests.filter(r => r.path.endsWith('/results/refresh')).at(-1).body.kinds, ['tv'])
  console.log('PASS 电影与剧集一次提交出片单、追问、进入详情和免模型返回')

  await go('/tv?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  await page.getByRole('textbox', { name: '智能找片' }).fill(episodeQuestion)
  await page.getByRole('button', { name: '找片', exact: true }).click()
  const episodeLink = mixed.getByRole('link', { name: dubbedEpisode.title, exact: true })
  await episodeLink.waitFor()
  const episodeSpend = paid().length
  const beforeEpisode = requests.length
  await episodeLink.click()
  await page.waitForURL(url => url.pathname === '/tv/201/s/2/e/307')
  await page.getByRole('heading', { level: 1, name: dubbedEpisode.title, exact: true }).waitFor()
  await page.getByRole('navigation', { name: '当前位置' }).getByText('S02E03', { exact: true }).waitFor()
  const [refreshedEpisode] = await Promise.all([
    page.waitForRequest(r => new URL(r.url()).pathname === '/api/ai/search/results/refresh'),
    page.getByRole('link', { name: '返回片单' }).click(),
  ])
  await page.waitForURL(url => url.pathname === '/tv' && url.searchParams.get('media') === '1')
  assert.equal(turns.get(refreshedEpisode.postDataJSON().session_id)?.query, episodeQuestion)
  await mixed.getByText(episodeQuestion, { exact: true }).waitFor()
  await episodeLink.waitFor()
  assert.equal(await episodeLink.getAttribute('href'), '/tv/201/s/2/e/307')
  assert.deepEqual(requests.slice(beforeEpisode).filter(r => r.path.startsWith('/api/tv/episodes/')).map(r => r.path),
    ['/api/tv/episodes/307'], 'Only load the matched file version, including while returning to the list')
  assert.equal(paid().length, episodeSpend, 'Opening a matched episode version and returning never calls the model')
  console.log('PASS 指定分集打开匹配的文件版本，返回原媒体库与片单')

  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  const panel = page.getByRole('region', { name: '智能搜索', exact: true })
  const input = page.getByRole('textbox', { name: '智能找片' })
  await input.fill('旧库晚到')
  const delayedSeen = new Promise(resolve => { delayedResolve = resolve })
  await input.press('Enter')
  await delayedSeen
  await page.getByRole('combobox', { name: '切换媒体库' }).selectOption('2')
  await page.waitForURL(url => url.searchParams.get('media') === '2')
  await response(delayedSearch, discovery({ kind: 'movie', q: '旧库晚到' }))
  delayedSearch = null
  assert.equal(await page.getByRole('link', { name: '远山来信' }).count(), 0)
  if (!await input.count()) await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  await input.fill('新库搜索')
  await input.press('Enter')
  await panel.getByRole('link', { name: '远山来信' }).waitFor()
  assert.equal(paid().at(-1).body.media_library_id, 2)
  searchFailure = 'disabled'
  await page.getByRole('textbox', { name: '追问当前片单' }).fill('调整要求')
  await page.getByRole('button', { name: '提交追问' }).click()
  await panel.getByText(/模拟智能搜索不可用/).waitFor()
  await panel.getByRole('link', { name: '远山来信' }).waitFor()
  await page.getByRole('button', { name: '普通搜索', exact: true }).click()
  const localSearch = page.waitForRequest(r => new URL(r.url()).pathname === '/api/search' && new URL(r.url()).searchParams.get('q') === '普通查找')
  await page.getByRole('combobox', { name: '搜索电影', exact: true }).fill('普通查找')
  await page.getByRole('button', { name: '搜索电影', exact: true }).click()
  await localSearch
  searchFailure = null
  console.log('PASS 切库隔离与追问失败保留片单、普通搜索')

  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  await panel.getByLabel('搜索范围').selectOption('public')
  await input.fill('想看普通人之间的故事')
  await input.press('Enter')
  const handoff = () => page.waitForRequest(r => {
    const url = new URL(r.url())
    return url.pathname === '/api/online/catalog/search' && url.searchParams.get('q') === '远山来信'
  })
  const handedOff = handoff()
  await panel.getByRole('link', { name: '去在线搜索' }).first().click()
  await handedOff
  assert.equal(await page.locator('#online-query').inputValue(), '远山来信')
  assert.equal(new URL(page.url()).searchParams.has('q'), false, 'The private handoff stays out of the URL')
  const otherSource = handoff()
  await page.locator('#online-source').selectOption('src_' + '2'.repeat(32))
  await otherSource
  await page.getByRole('link', { name: '返回片单' }).click()
  await panel.getByRole('link', { name: '去在线搜索' }).first().waitFor()
  console.log('PASS 库外作品带片名查在线来源、换源重查并返回原片单')

  for (const kind of ['movie', 'tv']) {
    await go(kind === 'movie' ? '/m/101' : '/tv/201')
    if (kind === 'movie' || !show.tmdb_id) await page.getByRole('button', { name: '匹配资料', exact: true }).click()
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
  await rules.locator('.matching-test > summary').click()
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
  await page.locator('.settings-mobile-header').getByRole('link', { name: '设置', exact: true }).click()
  await page.locator('.settings-directory').getByRole('link', { name: '离线资料', exact: true }).click()
  await page.locator('.settings-mobile-title h1').filter({ hasText: '离线资料' }).waitFor()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Settings directory and task header must fit the viewport')
  await go('/?media=1')
  await page.getByRole('button', { name: '智能搜索', exact: true }).click()
  await page.getByRole('textbox', { name: '智能找片' }).fill('手机搜索')
  await page.getByRole('button', { name: '找片', exact: true }).click()
  await panel.getByRole('link', { name: '远山来信' }).waitFor()
  await checkDiscoveryLayouts(panel)
  assert.deepEqual(unexpected, [], 'All requests must remain in explicitly mocked APIs and local frontend assets')
  assert.deepEqual(errors, [], 'Browser runtime or console errors')
  assert.equal(requests.some(r => r.method !== 'GET' && /organize|scan|delete/.test(r.path)), false)
  console.log('PASS 390px 布局与运行时；所有 API/模型响应均为模拟，未访问真实后端、媒体或云服务')
  if (capture) await captureDocs(go)
  await checkGlobalAssistant(go)
  assert.deepEqual(unexpected, [])
  assert.deepEqual(errors, [])
  }
}

try {
  await runSmoke()
} catch (error) {
  const report = path.join(project, 'output/playwright/ai-matching')
  await mkdir(report, { recursive: true })
  await writeFile(path.join(report, 'failure.json'), JSON.stringify({
    mocked: true, message: String(error), unexpected, browserErrors: errors,
  }, null, 2) + '\n')
  if (page && !page.isClosed()) {
    const screenshot = path.join(report, 'failure.png')
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
