// Real Vue UI + in-memory API fixtures. Never opens .env, backend, DB or media.
// Run: node scripts/smoke_settings_ui.mjs [--capture-docs] [--player-only|--drafts-only|--cache-only|--previews-only|--preview-baseline|--tools-only]
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdir, mkdtemp, readFile, writeFile, rm, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
assert.ok(args.every(arg => ['--capture-docs', '--player-only', '--drafts-only', '--cache-only', '--preview-baseline', '--previews-only', '--tools-only'].includes(arg)))
const capture = args.includes('--capture-docs')
const playerOnly = args.includes('--player-only')
const draftsOnly = args.includes('--drafts-only')
const cacheOnly = args.includes('--cache-only')
const previewBaseline = args.includes('--preview-baseline')
const previewsOnly = args.includes('--previews-only') || previewBaseline
const toolsOnly = args.includes('--tools-only')
assert.ok([playerOnly, draftsOnly, cacheOnly, previewsOnly, toolsOnly].filter(Boolean).length <= 1, 'Choose one focused regression scope')
assert.ok(!(capture && (draftsOnly || cacheOnly || previewsOnly || toolsOnly)), 'Documentation capture requires the complete settings regression')
async function frontendFingerprint() {
  const hash = createHash('sha256')
  async function walk(directory) {
    for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) await walk(file)
      else if (entry.isFile()) { hash.update(path.relative(root, file)); hash.update(await readFile(file)) }
    }
  }
  for (const directory of ['frontend/src', 'frontend/public']) await walk(path.join(root, directory))
  for (const file of ['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json']) {
    hash.update(file); hash.update(await readFile(path.join(root, file)))
  }
  return hash.digest('hex')
}
const sourceSha256 = capture || previewsOnly ? await frontendFingerprint() : null
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-settings-ui-'))
const dist = path.join(work, 'dist')
const libs = [{ id: 1, name: '电影', kind: 'movie', subpath: '电影' }, { id: 2, name: '剧集', kind: 'tv', subpath: '剧集' }]
  .map(l => ({ ...l, media_library_id: 1, media_name: '演示媒体库', source: 'local', enabled: true,
    effective_enabled: true, media_enabled: true, read_only: false, metadata_providers: '["local","tmdb"]' }))
libs.push({ ...libs[0], id: 3, name: '备用电影', media_library_id: 2, media_name: '演示媒体库乙' })
libs.push({ ...libs[1], id: 4, name: '备用剧集', media_library_id: 2, media_name: '演示媒体库乙' })
const requests = [], errors = [], unexpected = [], captured = []
const previewFiles = [
  { name: '海报.png', size: 500, kind: 'other' },
  { name: '字幕.vtt', size: 80, kind: 'subtitle' },
  { name: 'movie.nfo', size: 80, kind: 'nfo' },
  { name: '说明.pdf', size: 500, kind: 'other' },
  { name: '未入库.mp4', size: 50000, kind: 'feature', match_status: 'unregistered' },
  { name: '格式不兼容.mkv', size: 8, kind: 'feature', match_status: 'unregistered' },
  { name: '关联花絮.mp4', size: 50000, kind: 'sidecar', extra_id: 73, movie_id: 1 },
  { name: '未知格式.bin', size: 10, kind: 'other' },
  { name: '已消失.txt', size: 10, kind: 'other' },
  { name: '离线图片.png', size: 10, kind: 'other' },
  { name: '离线文档.pdf', size: 10, kind: 'other' },
]
const harnessId = 'virtual:player-preview-harness'
const harnessSource = `
import {createApp, h, ref} from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import PlayerModal from ${JSON.stringify(path.join(root, 'frontend/src/components/PlayerModal.vue'))};
createApp({setup() {
  const mode=ref(''), player=ref(null), watched=ref(0), ended=ref(0);
  window.testPlayerEvents={watched,ended};
  return () => h('main', [
    h('button',{onClick:()=>mode.value='preview'},'打开预览播放器'),
    h('button',{onClick:()=>mode.value='normal'},'打开普通播放器'),
    h('button',{onClick:()=>mode.value='ass'},'打开 ASS 播放器'),
    h('output',{id:'player-events'},'watched='+watched.value+';ended='+ended.value),
    mode.value ? h(PlayerModal,{key:mode.value,ref:player,versionId:mode.value==='ass'?82:81,title:'合成测试视频',preview:mode.value!=='normal',
      onClose:async()=>{await player.value?.saveFinal();mode.value=''},
      onWatched:()=>watched.value++,onEnded:()=>ended.value++}) : null,
  ])
}}).mount('#app');`
const files = Object.fromEntries(libs.map(l => [l.id, [
  { name: l.kind === 'tv' ? '演示剧.S01E01.mkv' : '模拟电影 (2020).mkv', kind: 'feature', size: 104857600, mtime: 1780000000, tmdb_id: 99, ...(l.kind === 'tv' ? { episode_id: 1, show_id: 1 } : { movie_id: 1 }) },
  { name: 'poster.jpg', kind: 'other', size: 20000, mtime: 1780000000 },
  { name: '模拟电影.zh.srt', kind: 'subtitle', size: 14000, mtime: 1780000000 },
]]))
const changes = new Map()
let revision = 0, scanJob = null, scanPolls = 0, browser, server, harnessEntry
let cacheLimits = { data_warning_gb: 20, transcode_cache_gb: 10, preview_cache_gb: 10 }
const cacheUsageCategories = { database: 100 * 1024 ** 2, posters: 200 * 1024 ** 2, previews: 300 * 1024 ** 2, transcode: 1400 * 1024 ** 2, other: 48 * 1024 ** 2 }
let previewPhase = 'missing', previewPolls = 0
let previewQueue = Array.from({ length: 52 }, (_, index) => ({
  kind: 'movie', id: 200 + index, library_id: 1, title: `队列样本 ${index + 1}`, file_name: `queued-${index + 1}.mkv`,
  source: 'auto', auto: true,
}))
let previewRunning = { kind: 'movie', id: 91, library_id: 1, title: '处理样本', file_name: 'running.mkv',
  job_id: 'preview-auto', source: 'auto', auto: true, waiting: false, frames: 4, frame_total: 10, done: 0, total: 1 }
let previewBatchSequence = 0
function previewBatchSummaries(libraryId) {
  const batches = new Map()
  for (const item of [...previewQueue, ...(previewRunning ? [previewRunning] : [])]) {
    if (item.library_id !== libraryId || !item.batch_id) continue
    const batch = batches.get(item.batch_id) || { batch_id: item.batch_id, pending: 0, running: 0, total: 0 }
    batch[item === previewRunning ? 'running' : 'pending']++
    batch.total++
    batches.set(item.batch_id, batch)
  }
  return [...batches.values()].map(batch => ({ ...batch, remaining: batch.total }))
}
let previewCompleted = Array.from({ length: 53 }, (_, index) => ({
  kind: ['movie', 'episode', 'extra'][index % 3], id: 500 + index, library_id: 1,
  title: `成品样本 ${index + 1}`, file_name: `completed-${index + 1}.mkv`,
  frames: 120 + index, interval: 10, quality_revision: index === 1 ? 0 : 1, needs_update: index === 1,
}))
let previewFailed = Array.from({ length: 53 }, (_, index) => ({
  kind: ['movie', 'episode', 'extra'][index % 3], id: 700 + index, library_id: 1,
  title: `失败样本 ${index + 1}`, file_name: `failed-${index + 1}.mkv`, failed_at: 1780000000 - index * 60,
  error_code: index % 2 ? 'timeout' : 'source_unavailable', error: index % 2 ? '生成缩略图超时，请稍后重新生成。' : '片源暂不可用，请检查媒体库连接后重新生成。',
}))
const previewRevision = items => createHash('sha256').update(JSON.stringify(items.map(item =>
  [item.kind, item.id, item.library_id]))).digest('hex').slice(0, 16)
let videoFixture, imageFixture, assVideoFixture, assFontFixture
const assFixture = `[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Liberation Sans,32,&H0000FFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,20,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:40.00,Default,,0,0,0,,{\\an8\\pos(320,25)\\c&H0000FF&\\b1}ASS TOP RED
Dialogue: 0,0:00:00.00,0:00:40.00,Default,,0,0,0,,ASS BOTTOM YELLOW
`
const directFixturePath = '中文目录/合成视频 100% & #.mp4'
const directFixtureUrl = '/fixtures/video.mp4?' + new URLSearchParams({ name: directFixturePath })
function syntheticPdf() {
  const text = 'BT /F1 18 Tf 45 220 Td (JZMedia synthetic file preview) Tj ET'
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', '<< /Length ' + text.length + ' >>\nstream\n' + text + '\nendstream']
  let body = '%PDF-1.4\n', offsets = [0]
  for (let i = 0; i < objects.length; i++) { offsets.push(body.length); body += (i + 1) + ' 0 obj\n' + objects[i] + '\nendobj\n' }
  const start = body.length
  body += 'xref\n0 6\n0000000000 65535 f \n' + offsets.slice(1).map(n => String(n).padStart(10, '0') + ' 00000 n \n').join('')
  return Buffer.from(body + 'trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n' + start + '\n%%EOF')
}
function mark(id, operation, rel) {
  const events = changes.get(id) || []
  events.push({ id: ++revision, operation, rel }); changes.set(id, events)
}
function summary(id) {
  const events = changes.get(id) || []
  return { library_id: id, pending: events.length > 0, count: events.length, revision: events.at(-1)?.id || 0,
    paths: events.map(e => e.rel), actions: {}, active_jobs: [] }
}
function mock(url, method, body) {
  const key = url.pathname, id = Number(url.searchParams.get('library') || body?.library_id || 1)
  requests.push({ key, method, body, query: Object.fromEntries(url.searchParams) })
  if (key === '/api/libraries') return { items: libs, default_id: 1 }
  if (/^\/api\/libraries\/\d+$/.test(key) && method === 'PATCH') { const lib = libs.find(l => l.id === Number(key.split('/').at(-1))); Object.assign(lib, body); return lib }
  if (key === '/api/viewing/session') return { mode: 'normal', enabled: false, management_unlocked: false, generation: 0, expires_at: 0 }
  if (key === '/api/onboarding') return { show_welcome: false, status: 'completed' }
  if (key === '/api/settings') return { tmdb_configured: false, tmdb_language: 'zh-CN', jzmedia_token_source: 'unset' }
  if (key === '/api/cache-settings') {
    if (method === 'PUT') {
      const base = (cacheUsageCategories.database + cacheUsageCategories.posters + cacheUsageCategories.other) / 1024 ** 3
      const reserve = Math.ceil((base + Math.max(base * 0.1, 1)) * 10 - 1e-8) / 10
      assert.ok(body.transcode_cache_gb > 0 && body.transcode_cache_gb + body.preview_cache_gb + reserve <= body.data_warning_gb + 1e-8, 'isolated cache save must fit the total budget')
      cacheLimits = { ...body }
    }
    return cacheLimits
  }
  if (key === '/api/ai/settings') return { enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'demo', daily_limit: 100, timeout_seconds: 12, usage: { requests: 0, input_tokens: 0, output_tokens: 0 } }
  if (key === '/api/jobs/stats') return { grouped: 10, versions: 12, no_match: 1, needs_review: 2, missing_files: 0,
    by_library: [{ library_id: 1, pending: 3, missing_files: 0 }] }
  if (key === '/api/data-usage') return { ok: true, total_bytes: 2 * 1024 ** 3, warning_bytes: cacheLimits.data_warning_gb * 1024 ** 3,
    over_limit: cacheLimits.data_warning_gb <= 2, categories: cacheUsageCategories, error: '' }
  if (key === '/api/tv/stats') return { shows: 3, seasons: 5, episodes: 60, pending: 0, episode_review: 0, by_library: [] }
  if (key === '/api/tv/airing/status' && method === 'GET') return { configured: false, running: false,
    current: null, error: '', retry_at: 0, total: 0, checked: 0, pending: 0, failed: 0 }
  if (key === '/api/cast/current' && method === 'GET') return { session: null }
  if (key === '/api/metadata/providers') return { providers: [{ name: 'local', label: '本地索引', available: true }, { name: 'tmdb', label: 'TMDB', available: true }] }
  if (key === '/api/metadata/test-search') return { library_id: id, kind: libs.find(l => l.id === id)?.kind, items: [], source: null, elapsed_ms: 1 }
  if (key === '/api/jobs/scan') {
    if (method === 'POST') { scanJob = { job_id: 'scan-' + revision, library_id: id, state: 'running', done: 0, total: 3, watermark: revision }; scanPolls = 0 }
    else if (scanJob && ++scanPolls > 1) {
      scanJob.state = 'done'; scanJob.done = 3; scanJob.summary = { counts: { ok: 1 } }
      changes.set(scanJob.library_id, (changes.get(scanJob.library_id) || []).filter(e => e.id > scanJob.watermark))
    }
    return scanJob || { state: 'idle' }
  }
  if (key === '/api/files/missing' || key === '/api/files/restore-candidates' || key === '/api/tv/shows' || key === '/api/jobs/tv-organize/history') return { items: [] }
  if (key === '/api/files/unmatched') return { items: [], unmatched: [], needs_review: [], suspect_title: [], orphan_extras: [] }
  if (key === '/api/files/organize') return { plans: [], conflicts: [] }
  if (key === '/api/fs/changes') return summary(id)
  if (key === '/api/fs/list') {
    const dir = url.searchParams.get('path') || '', offset = Number(url.searchParams.get('offset') || 0)
    const rows = dir === '预览样本' ? previewFiles.map(f => ({ ...f, rel: dir + '/' + f.name })) : dir ? [] : files[id].map(f => ({ ...f, rel: f.name }))
    // Deliberately paginate below the requested page size to verify complete listing.
    const page = rows.slice(offset, offset + 2)
    return { path: dir, parent: '', crumbs: dir ? [{ name: dir, rel: dir }] : [], fs_writable: true,
      dirs: dir ? [] : [{ name: '待整理', rel: '待整理', children: 0 }, { name: '预览样本', rel: '预览样本', children: 0 }], files: page,
      total_files: rows.length, has_more: offset + page.length < rows.length, offset, limit: 2 }
  }
  if (key === '/api/fs/rename') {
    if (body.dry_run) return { plans: [{ from: body.from, to: body.name, status: 'planned', followers: [] }] }
    const file = files[id].find(f => f.name === body.from)
    assert.ok(file); file.name = body.name; mark(id, 'rename', body.name)
    return { moved: 1, results: [{ from: body.from, to: body.name, status: 'moved', followed: 0 }] }
  }
  if (key === '/api/fs/copy') {
    assert.equal(body.dry_run, true, 'selection checks must stop at the copy preview')
    return { total: body.from.length, files: body.from.length, bytes: 100, needs_confirm: true, conflicts: [] }
  }
  if (/^\/api\/stream\/\d+\/previews$/.test(key)) {
    if (key !== '/api/stream/81/previews') return { state: 'missing', pages: [] }
    if (method === 'POST') { previewPhase = 'running'; previewPolls = 0; return { job_id: 'preview-film' } }
    if (previewPhase === 'running' && ++previewPolls > 1) previewPhase = 'ready'
    return previewPhase === 'running'
      ? { state: 'running', job_id: 'preview-film', frames: 37, frame_total: 100, count: 100, columns: 5, rows: 5, pages: [] }
      : { state: previewPhase, pages: [] }
  }
  if (key === '/api/stream/82/fonts') return { fonts: [{ name: 'Liberation Sans', url: '/fixtures/ass-font.woff2' }] }
  if (key === '/api/stream/82/decide') return { method: 'direct', direct_url: '/fixtures/ass-video.mp4', reasons: [],
    media: { width: 640, height: 360, duration: 40, audio: [],
      subs: [{ index: 0, codec: 'ass', lang: 'eng', title: '合成 ASS 样式', default: 1 }] }, plan: {} }
  if (/^\/api\/stream\/\d+\/decide$/.test(key)) return { method: 'direct', direct_url: directFixtureUrl, reasons: [],
    media: { width: 320, height: 180, duration: 1200, audio: [], subs: [] }, plan: {} }
  if (key === '/api/stream/progress') return method === 'GET' ? { position: 25, duration: 1200, position_text: '00:25' } : { ok: true }
  if (key === '/api/stream/previews/jobs/latest') return { state: 'idle' }
  if (key === '/api/stream/previews/jobs' && method === 'POST') {
    const batchId = `preview-fixture-batch-${++previewBatchSequence}`
    const batch = Array.from({ length: 3 }, (_, index) => ({ kind: 'movie', id: 1100 + previewBatchSequence * 10 + index,
      library_id: body.library_id, title: `批次 ${previewBatchSequence} 样本 ${index + 1}`, file_name: `batch-${previewBatchSequence}-${index + 1}.mkv`,
      source: 'batch', batch_id: batchId, auto: false }))
    previewQueue.push(...batch)
    return { state: 'queued', batch_id: batchId, total: 3, added: 3, ready: 0, already_queued: 0, unavailable: 0 }
  }
  if (/^\/api\/stream\/previews\/batches\/[^/]+\/cancel$/.test(key) && method === 'POST') {
    const batchId = key.split('/').at(-2)
    const matches = item => item?.batch_id === batchId && item.library_id === body.library_id
    const before = previewQueue.length, cancelled = matches(previewRunning)
    previewQueue = previewQueue.filter(item => !matches(item))
    if (cancelled) previewRunning = null
    return { removed: before - previewQueue.length, cancelled }
  }
  if (key === '/api/stream/previews/auto') return { mode: 'all', queued: previewQueue.length + Number(!!previewRunning), running: !!previewRunning, start_hour: 3, end_hour: 6, pause_reason: 'scheduled' }
  if (key === '/api/stream/previews/completed') {
    const libraryId = Number(url.searchParams.get('library_id'))
    const items = previewCompleted.filter(item => !libraryId || item.library_id === libraryId)
    const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 50)
    return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }
  }
  if (key === '/api/stream/previews/failed' && method === 'GET') {
    const libraryId = Number(url.searchParams.get('library_id'))
    const items = previewFailed.filter(item => !libraryId || item.library_id === libraryId)
    const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 50)
    return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }
  }
  if (/^\/api\/stream\/previews\/failed\/(movie|episode|extra)\/\d+\/retry$/.test(key) && method === 'POST') {
    const [, , , , , kind, itemId] = key.split('/')
    const matches = item => item?.kind === kind && item.id === Number(itemId) && item.library_id === body.library_id
    assert.ok(previewFailed.some(matches), 'retry uses a failure from the selected library')
    if (matches(previewRunning)) return { state: 'running' }
    const queued = previewQueue.find(matches)
    if (queued && queued.source !== 'auto') return { state: 'already_queued' }
    previewQueue = previewQueue.filter(item => !matches(item))
    previewQueue.push({ ...(queued || previewFailed.find(matches)), source: 'retry', auto: false })
    return { state: 'queued' }
  }
  if (key === '/api/stream/previews/queue') {
    if (method === 'POST') {
      previewQueue.push({ kind: 'movie', id: 93, library_id: body.library_id, title: '已有影片样本', file_name: 'existing.mkv', source: 'auto', auto: true })
      return { total: 2, added: 1, ready: 1, unavailable: 0, already_queued: 0 }
    }
    const libraryId = Number(url.searchParams.get('library_id'))
    const items = previewQueue.filter(item => item.library_id === libraryId)
    const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 50)
    return { items: items.slice(offset, offset + limit), total: items.length, revision: previewRevision(items),
      offset: Number(url.searchParams.get('offset') || 0), limit: 50,
      running: previewRunning?.library_id === libraryId ? previewRunning : null,
      batches: previewBatchSummaries(libraryId) }
  }
  if (key === '/api/stream/previews/queue/remove' && method === 'POST') {
    const pending = previewQueue.filter(item => item.library_id === body.library_id)
    assert.equal(body.revision, previewRevision(pending), 'bulk removal uses the queue snapshot')
    const keyOf = item => `${item.kind}:${item.id}`
    const keys = new Set((body.all_pending ? body.exclude : body.items).map(keyOf))
    const chosen = pending.filter(item => body.all_pending ? !keys.has(keyOf(item)) : keys.has(keyOf(item)))
    const chosenKeys = new Set(chosen.map(keyOf))
    previewQueue = previewQueue.filter(item => item.library_id !== body.library_id || !chosenKeys.has(keyOf(item)))
    return { removed: chosen.length }
  }
  if (/^\/api\/stream\/previews\/queue\/(movie|episode|extra)\/\d+$/.test(key) && method === 'DELETE') {
    const itemId = Number(key.split('/').at(-1)), kind = key.split('/').at(-2)
    const matches = item => item?.kind === kind && item.id === itemId && item.library_id === Number(url.searchParams.get('library_id'))
    const cancelled = matches(previewRunning)
    if (cancelled) previewRunning = null
    else previewQueue = previewQueue.filter(item => !matches(item))
    return { removed: true, cancelled }
  }
  if (key === '/api/stream/previews/cache/clean' || key === '/api/stream/cache/clean') return { total: cacheUsageCategories[key === '/api/stream/cache/clean' ? 'transcode' : 'previews'], candidates: 0, candidate_bytes: 0, dry_run: body?.dry_run ?? true, removed: 0, freed: 0 }
  unexpected.push({ key, method })
  return { items: [] }
}
async function ffmpeg() {
  try { execFileSync('ffmpeg', ['-version'], { stdio: 'ignore' }); return 'ffmpeg' } catch { /* Existing dependency fallback. */ }
  for (const python of await readdir(path.join(root, '.venv/lib'))) {
    const base = path.join(root, '.venv/lib', python, 'site-packages/static_ffmpeg/bin')
    for (const platform of await readdir(base).catch(() => [])) {
      const binary = path.join(base, platform, 'ffmpeg')
      try { execFileSync(binary, ['-version'], { stdio: 'ignore' }); return binary } catch { /* Try another installed platform. */ }
    }
  }
  throw new Error('FFmpeg is required for documentation captures')
}
async function checkPlaybackCompatibility(browser, base) {
  // 模拟无 MSE 的浏览器，仍由真实 video 解码原生 MP4。
  const fileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block' })
  try {
    await fileContext.addInitScript(() => {
      for (const key of ['MediaSource', 'ManagedMediaSource', 'WebKitMediaSource']) {
        Object.defineProperty(window, key, { configurable: true, value: undefined })
      }
      const original = HTMLMediaElement.prototype.canPlayType
      HTMLMediaElement.prototype.canPlayType = function (type) {
        return /mpegurl/i.test(type) ? '' : original.call(this, type)
      }
    })
    const page = await fileContext.newPage()
    page.on('pageerror', e => errors.push(String(e)))
    await page.goto(base + '/player-harness.html')
    await page.getByRole('button', { name: '打开预览播放器', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
    const caps = await page.evaluate(() => window.__jzCaps())
    assert.equal(caps.mse, false)
    assert.equal(caps.native_hls, false)
    assert.equal(caps.file.video.h264, true)
    assert.equal(await page.evaluate(() => window.__jzPlayerDebug().engine), 'direct')
    await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
    await page.locator('.player-dlg').waitFor({ state: 'hidden' })
  } finally { await fileContext.close() }

  // 模拟原生 HLS 能力声明和组件下载失败。兼容 sid 用 MP4 驱动真实媒体元素，
  // 验证切轨/重挂/关闭生命周期；实际 TS 编码由 test_playback_compat.py 验证。
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block' })
  const created = [], deleted = []
  let loadAttempts = 0
  try {
    await context.addInitScript(() => {
      const original = HTMLMediaElement.prototype.canPlayType
      HTMLMediaElement.prototype.canPlayType = function (type) {
        return /mpegurl/i.test(type) ? 'maybe' : original.call(this, type)
      }
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('synthetic clipboard restriction') } } })
      document.execCommand = () => false
    })
    await context.route('**/assets/hls*.js', route => { loadAttempts++; return route.abort('failed') })
    await context.route('**/api/stream/81/decide', async route => {
      const body = route.request().postDataJSON()
      const compat = body.caps.hls_compat === true
      await route.fulfill({ json: { method: 'remux', direct_url: directFixtureUrl,
        reasons: compat ? ['hls_compatibility'] : [],
        plan: { seg: compat ? 'ts' : 'fmp4', vcopy: true, acopy: true },
        media: { width: 320, height: 180, duration: 1200, container: 'mkv', vcodec: 'h264', bit_depth: 8,
          vcaps: ['video/mp4; codecs="avc1.640028"'],
          audio: [{ codec: 'aac', channels: 2 }, { codec: 'aac', channels: 2 }], subs: [] } } })
    })
    await context.route('**/api/stream/81/sessions', async route => {
      const body = route.request().postDataJSON()
      created.push(body)
      const compat = body.caps.hls_compat === true
      const mp4 = body.caps.mp4_compat === true
      await route.fulfill({ json: { session_id: 'compat-' + created.length,
        playlist_url: mp4 ? '' : directFixtureUrl,
        media_url: mp4 ? directFixtureUrl + '&transport=compat-mp4' : '', media_start: body.start, initial_time: 0,
        clip_duration: mp4 ? 40 : 0,
        method: 'remux', reasons: compat ? ['hls_compatibility'] : [],
        plan: { seg: mp4 ? 'mp4' : (compat ? 'ts' : 'fmp4'), clip_seconds: 30, vcopy: true, acopy: true } } })
    })
    await context.route(/\/api\/stream\/sessions\/compat-/, async route => {
      if (route.request().method() === 'DELETE') deleted.push(route.request().url())
      await route.fulfill({ json: { ok: true, running: false, segments: 1 } })
    })
    const page = await context.newPage()
    page.on('pageerror', e => errors.push(String(e)))
    await page.goto(base + '/player-harness.html')
    await page.getByRole('button', { name: '打开预览播放器', exact: true }).click()
    await page.waitForFunction(() => window.__jzPlayerDebug?.().engine === 'native' && document.querySelector('video')?.readyState >= 2)
    assert.equal(loadAttempts, 1)
    assert.equal(created.length, 2, '组件下载失败只发起一次兼容重播')
    assert.match(created[0].client_id, /^[A-Za-z0-9_-]{16,64}$/)
    assert.equal(created[1].client_id, created[0].client_id, '同一播放器重播保留会话归属')
    assert.ok(created[1].request_seq > created[0].request_seq, '后发请求可撤销前一轮建会话')
    assert.equal(created[1].caps.native_hls, true)
    assert.equal(created[1].caps.hls_compat, true)
    assert.equal(created[1].caps.force_hls, true)
    assert.equal(created[1].caps.file.video.h264, true)
    assert.equal(deleted.length, 1, '兼容重播回收旧 sid')
    await page.evaluate(() => document.querySelector('video').pause())
    await page.getByRole('button', { name: '字幕与音轨', exact: true }).click()
    await page.getByRole('region', { name: '字幕与音轨', exact: true }).getByLabel('音轨', { exact: true }).selectOption('1')
    await page.waitForFunction(() => window.__jzPlayerDebug?.().session === 'compat-3' && document.querySelector('video')?.readyState >= 2)
    assert.equal(created[2].audio, 1, 'TS 单轨产物切轨必须重开会话')
    assert.equal(created[2].client_id, created[0].client_id)
    assert.ok(created[2].request_seq > created[1].request_seq)
    assert.equal(deleted.length, 2)
    if (!await page.getByText('更多选项', { exact: true }).isVisible()) {
      await page.getByRole('button', { name: '播放设置', exact: true }).click()
    }
    await page.getByText('更多选项', { exact: true }).click()
    await page.getByRole('button', { name: '复制诊断信息', exact: true }).click()
    const diagnostic = JSON.parse(await page.getByLabel('播放诊断信息', { exact: true }).inputValue())
    assert.equal(diagnostic.output.seg, 'ts')
    assert.equal(diagnostic.media.vcodec, 'h264')
    assert.equal(diagnostic.caps.hls_compat, true)
    const caps = await page.evaluate(() => window.__jzCaps())
    assert.equal(caps.hls_compat, false, '重播不污染基础能力缓存')
    // 复现真机：native 有画面/readyState=4，却始终回传 ct=0、buffered=[]。
    await page.locator('video').evaluate(video => {
      const time = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')
      const ranges = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'buffered')
      Object.defineProperty(video, 'currentTime', { configurable: true,
        get() { return this.currentSrc.startsWith('blob:') ? time.get.call(this) : 0 },
        set(value) { time.set.call(this, value) } })
      Object.defineProperty(video, 'buffered', { configurable: true,
        get() { return this.currentSrc.startsWith('blob:') ? ranges.get.call(this) : { length: 0 } } })
      video.currentTime = 0
      video.play()
    })
    await page.waitForFunction(() => window.__jzPlayerDebug().engine === 'mp4' && document.querySelector('video')?.readyState >= 2)
    assert.equal(await page.evaluate(() => window.__jzPlayerDebug().recoverCount), 0,
      'native 时间轴失联不得连续重挂三次')
    await page.waitForFunction(() => window.__jzPlayerDebug().mp4Prefetch?.ready)
    await page.evaluate(() => { window.compatVideo = document.querySelector('video') })
    const active = await page.evaluate(() => window.__jzPlayerDebug().session)
    const closed = page.waitForResponse(response => response.url().includes('/sessions/' + active) && response.request().method() === 'DELETE')
    await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
    await closed
    await page.locator('.player-dlg').waitFor({ state: 'hidden' })
    await page.waitForFunction(() => window.compatVideo.paused && !window.compatVideo.hasAttribute('src'))
    assert.equal(deleted.length, created.length, '兼容回退及关窗回收全部 sid（含预取）')
  } finally { await context.close() }
}

async function checkMp4Compatibility(browser, base) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block',
    userAgent: 'Mozilla/5.0 Android 15 Chrome/135.0 Mobile Safari/537.36 XiaoMi/MiuiBrowser/20.28.1020918' })
  const created = [], deleted = []
  let hlsRequests = 0
  try {
    await context.addInitScript(() => {
      for (const name of ['MediaSource', 'ManagedMediaSource', 'WebKitMediaSource']) {
        Object.defineProperty(window, name, { configurable: true, value: undefined })
      }
      const original = HTMLMediaElement.prototype.canPlayType
      HTMLMediaElement.prototype.canPlayType = function (type) {
        return /mpegurl/i.test(type) ? 'maybe' : original.call(this, type)
      }
      // 复现真机缺失 duration/loadedmetadata，不能用合成 Chromium 的正常元数据掩盖。
      Object.defineProperty(HTMLMediaElement.prototype, 'duration', { configurable: true, get: () => NaN })
      window.addEventListener('loadedmetadata', event => event.stopImmediatePropagation(), true)
      window.testBlobCreated = []; window.testBlobRevoked = []
      const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL)
      URL.createObjectURL = blob => { const url = create(blob); window.testBlobCreated.push(url); return url }
      URL.revokeObjectURL = url => { window.testBlobRevoked.push(url); revoke(url) }
    })
    await context.route('**/assets/hls*.js', route => { hlsRequests++; return route.abort() })
    const plan = { seg: 'mp4', clip_seconds: 30, vcopy: false, acopy: false }
    await context.route('**/api/stream/81/decide', route => route.fulfill({ json: {
      method: 'video_transcode', subtitle_mode: 'webvtt', direct_url: directFixtureUrl, reasons: ['mp4_compatibility'], plan,
      media: { width: 320, height: 180, duration: 1200, vcodec: 'hevc', bit_depth: 10, container: 'mkv',
        audio: [{ codec: 'dts', channels: 6 }], subs: [{ index: 0, codec: 'webvtt', lang: 'eng', default: 1 }] },
    } }))
    await context.route('**/api/stream/81/sub/0.vtt', route => route.fulfill({ contentType: 'text/vtt',
      body: 'WEBVTT\n\n00:00:41.000 --> 00:00:44.000\nMP4 offset caption\n\n00:08:21.000 --> 00:08:25.000\nMP4 seek caption' }))
    await context.route('**/api/stream/81/sessions', route => {
      const body = route.request().postDataJSON()
      created.push(body)
      assert.equal(body.caps.mp4_compat, true)
      return route.fulfill({ json: { session_id: 'mp4-' + created.length, method: 'video_transcode',
        media_url: directFixtureUrl, playlist_url: '', media_start: body.start, initial_time: 0,
        clip_duration: 40,
        complete: true, plan, reasons: ['mp4_compatibility'] } })
    })
    await context.route(/\/api\/stream\/sessions\/mp4-/, route => {
      if (route.request().method() === 'DELETE') deleted.push(route.request().url().split('/').pop())
      return route.fulfill({ json: { ok: true, running: false, segments: 0 } })
    })
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(String(error)))
    await page.goto(base + '/player-harness.html')
    await page.getByRole('button', { name: '打开预览播放器', exact: true }).click()
    await page.waitForFunction(() => window.__jzPlayerDebug?.().engine === 'mp4' && document.querySelector('video')?.readyState >= 2)
    assert.equal(hlsRequests, 0)
    assert.equal(await page.locator('video').evaluate(v => v.hasAttribute('controls')), false)
    assert.equal(await page.locator('video').evaluate(v => v.disableRemotePlayback), true)
    assert.equal(await page.locator('video').evaluate(v => v.currentSrc.startsWith('blob:')), true)
    await page.waitForFunction(() => window.__jzPlayerDebug().mp4Prefetch?.ready)
    assert.equal(created.length, 2, 'only the next clip is prefetched')
    assert.equal(created[1].start, 40, 'prefetch uses the actual finite clip duration')
    await page.evaluate(() => {
      const video = document.querySelector('video')
      video.pause(); video.currentTime = 39.9
      const source = video.currentSrc
      Object.defineProperty(video, 'ended', { configurable: true, get() { return this.currentSrc === source } })
      // 不发 ended 事件：原生接口只更新 ended 属性时，轮询也必须衔接下一片。
    })
    await page.waitForFunction(() => window.__jzPlayerDebug().pos >= 40 && window.__jzPlayerDebug().mp4End === 80)
    await page.locator('video').evaluate(video => { video.pause(); video.currentTime = 2 })
    await page.getByText('MP4 offset caption', { exact: true }).waitFor()
    await page.locator('video').evaluate(video => video.dispatchEvent(new Event('ended')))
    // 旧源晚到的 ended 不能在新片段 2 秒处跳过剩余内容。
    assert.ok((await page.evaluate(() => window.__jzPlayerDebug().pos)) >= 42)
    assert.equal(await page.locator('#player-events').textContent(), 'watched=0;ended=0', 'clip ending is not movie ending')
    const seek = page.getByRole('slider', { name: '播放进度', exact: true })
    await seek.evaluate(input => { input.value = '500'; input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })) })
    await page.waitForFunction(() => window.__jzPlayerDebug().mp4End === 540)
    await page.locator('video').evaluate(video => { video.pause(); video.currentTime = 2 })
    await page.getByText('MP4 seek caption', { exact: true }).waitFor()
    const state = await page.evaluate(() => window.__jzPlayerDebug())
    assert.ok(state.pos >= 502 && state.pos < 505)
    assert.equal(state.recoverCount, 0)
    assert.equal(state.error, '')
    assert.equal(state.timelineDetached, false)
    await page.getByRole('button', { name: '播放设置', exact: true }).click()
    const menuOnTop = await page.locator('.pd-set').evaluate(menu => {
      const r = menu.getBoundingClientRect()
      return menu.contains(document.elementFromPoint(r.left + r.width / 2, r.top + 20))
    })
    assert.equal(menuOnTop, true, 'settings menu is above the inline Blob video')
    await page.waitForFunction(() => window.__jzPlayerDebug().mp4Prefetch?.ready)
    const all = created.map((_body, i) => 'mp4-' + (i + 1))
    const closes = all.filter(sid => !deleted.includes(sid)).map(sid => page.waitForResponse(r => r.url().endsWith('/sessions/' + sid) && r.request().method() === 'DELETE'))
    await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
    await Promise.all(closes)
    assert.deepEqual([...new Set(deleted)].sort(), all.sort(), 'closing releases active and prefetched leases')
    assert.deepEqual(await page.evaluate(() => [...new Set(window.testBlobCreated)].sort()),
      await page.evaluate(() => [...new Set(window.testBlobRevoked)].sort()), 'all clip Blob URLs are revoked')
  } finally { await context.close() }
}

async function checkAssFullscreen(browser, base) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2, isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  page.on('pageerror', error => errors.push(String(error)))
  const directory = path.join(root, 'output/playwright/ass-fullscreen')
  await mkdir(directory, { recursive: true })
  const checks = []
  const consoleErrors = []
  page.on('console', event => { if (event.type() === 'error') consoleErrors.push(event.text()) })
  async function ready() {
    await page.waitForFunction(() => window.__jzPlayer?.ass()
      || window.__jzPlayer?.snapshot().events.some(event => event.k === 'ass:error-vtt' || event.k === 'ass:init-error'))
    assert.equal(await page.evaluate(() => !!window.__jzPlayer.ass()), true,
      JSON.stringify(await page.evaluate(() => window.__jzPlayer.snapshot())))
    await page.evaluate(async () => { await window.__jzPlayer.ass().ready })
    await page.evaluate(() => new Promise(resolve => {
      const video = document.querySelector('video')
      video.requestVideoFrameCallback(() => video.requestVideoFrameCallback(resolve))
    }))
  }
  async function pixels(scene, styled = true) {
    const geometry = await page.locator('video').evaluate(video => {
      const rect = video.getBoundingClientRect(), ratio = video.videoWidth / video.videoHeight
      const width = Math.min(rect.width, rect.height * ratio), height = width / ratio
      return { x: rect.x + (rect.width - width) / 2, y: rect.y + (rect.height - height) / 2,
        width, height, viewportWidth: innerWidth }
    })
    const png = await page.screenshot({ path: path.join(directory, scene + '.png'), animations: 'disabled' })
    const counts = await page.evaluate(async ({ png, rect }) => {
      const image = new Image()
      image.src = 'data:image/png;base64,' + png
      await image.decode()
      const canvas = document.createElement('canvas')
      canvas.width = image.width; canvas.height = image.height
      const ctx = canvas.getContext('2d')
      ctx.drawImage(image, 0, 0)
      const scale = image.width / rect.viewportWidth
      const data = ctx.getImageData(Math.round(rect.x * scale), Math.round(rect.y * scale),
        Math.floor(rect.width * scale), Math.floor(rect.height * scale)).data
      let blue = 0, red = 0, yellow = 0
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i], g = data[i + 1], b = data[i + 2]
        if (Math.abs(r - 50) < 10 && Math.abs(g - 91) < 10 && Math.abs(b - 112) < 10) blue++
        if (r > 170 && g < 80 && b < 80) red++
        if (r > 170 && g > 170 && b < 80) yellow++
      }
      return { blueFraction: blue / (data.length / 4), red, yellow }
    }, { png: png.toString('base64'), rect: geometry })
    assert.ok(counts.blueFraction > 0.6, `${scene}: video must remain visible under the subtitle layer: ${JSON.stringify(counts)}`)
    if (styled) assert.ok(counts.red > 20 && counts.yellow > 20,
      `${scene}: real ASS must retain both styled text colors: ${JSON.stringify(counts)}`)
    checks.push({ scene, ...counts })
  }
  async function revealControls() {
    if (!await page.locator('.player-corner-controls').evaluate(element => element.classList.contains('show'))) {
      await page.locator('.pv-wrap').tap({ position: { x: 5, y: 5 } })
    }
  }
  try {
    await page.goto(base + '/player-harness.html')
    await page.getByRole('button', { name: '打开 ASS 播放器', exact: true }).tap()
    await ready()
    const attributes = await page.evaluate(async () => window.__jzPlayer.ass().renderer._gpurender.gl.getContextAttributes())
    assert.equal(attributes.alpha, true, 'real JASSUB WebGL context must remain transparent')
    assert.equal(attributes.desynchronized, false, 'real worker must use compositor-synchronized rendering')
    const mainAttributes = await page.evaluate(() => new OffscreenCanvas(1, 1).getContext('2d', { desynchronized: true }).getContextAttributes())
    assert.equal(mainAttributes.desynchronized, true, 'worker policy must not patch the page realm')
    const src = await page.locator('video').evaluate(video => video.currentSrc)
    await page.locator('.player-toast').waitFor({ state: 'hidden' })
    await pixels('window')
    await revealControls()
    await page.getByRole('button', { name: '全屏', exact: true }).tap()
    await page.waitForFunction(() => document.fullscreenElement && !document.querySelector('.pv-ctl.show'))
    await pixels('fullscreen-portrait')
    // Headless Chrome 不能调整处于原生全屏状态的窗口尺寸；退出、旋转视口后再进入。
    await revealControls()
    await page.getByRole('button', { name: '退出全屏', exact: true }).tap()
    await page.waitForFunction(() => !document.fullscreenElement)
    await page.setViewportSize({ width: 844, height: 390 })
    await ready()
    await revealControls()
    await page.getByRole('button', { name: '全屏', exact: true }).tap()
    await page.waitForFunction(() => document.fullscreenElement && !document.querySelector('.pv-ctl.show'))
    await pixels('fullscreen-landscape')
    await revealControls()
    await page.getByRole('button', { name: '播放设置', exact: true }).tap()
    await page.getByText('字幕调整', { exact: true }).tap()
    const compatibility = page.getByLabel('兼容模式（使用简化字幕）', { exact: true })
    await compatibility.check()
    await page.locator('canvas.JASSUB').waitFor({ state: 'detached' })
    await page.locator('.sub-layer').getByText('ASS compatibility text', { exact: true }).waitFor()
    await page.getByRole('button', { name: '关闭设置', exact: true }).tap()
    await page.waitForFunction(() => !document.querySelector('.pv-ctl.show'))
    await pixels('fullscreen-compatible', false)
    await revealControls()
    await page.getByRole('button', { name: '播放设置', exact: true }).tap()
    await page.getByText('字幕调整', { exact: true }).tap()
    await compatibility.uncheck()
    await ready()
    assert.equal(await page.locator('.sub-layer').count(), 0)
    assert.equal(await page.locator('canvas.JASSUB').count(), 1, 'switching back must not leave duplicate canvases')
    await page.getByRole('button', { name: '关闭设置', exact: true }).tap()
    await page.waitForFunction(() => !document.querySelector('.pv-ctl.show'))
    await pixels('fullscreen-ass-restored')
    assert.equal(await page.locator('video').evaluate(video => video.currentSrc), src, 'subtitle switching must reuse the playing video')
    await revealControls()
    await page.getByRole('button', { name: '退出全屏', exact: true }).tap()
    await page.waitForFunction(() => !document.fullscreenElement)
    await page.locator('.player-toast').waitFor({ state: 'hidden' })
    await pixels('fullscreen-exited')
    await page.evaluate(() => { window.closedAssVideo = document.querySelector('video') })
    await page.getByRole('button', { name: '关闭播放器', exact: true }).tap()
    assert.equal(await page.locator('canvas.JASSUB').count(), 0)
    assert.equal(await page.evaluate(() => window.closedAssVideo.paused && !window.closedAssVideo.hasAttribute('src')), true)
    await writeFile(path.join(directory, 'checks.json'), JSON.stringify({ syntheticMedia: true,
      phoneLayoutSimulation: true, physicalAndroidDevice: false, attributes, mainAttributes, checks }, null, 2) + '\n')
  } catch (error) {
    await page.screenshot({ path: path.join(directory, 'failure.png') }).catch(() => {})
    const snapshot = await page.evaluate(() => window.__jzPlayer?.snapshot()).catch(() => null)
    await writeFile(path.join(directory, 'failure.json'), JSON.stringify({ snapshot, consoleErrors }, null, 2))
    throw error
  } finally { await context.close() }
}
async function checkPreviewMaintenance(browser, base, { baseline = false } = {}) {
  const directory = path.join(root, 'output/playwright/preview-maintenance-unified')
  await mkdir(directory, { recursive: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  const original = structuredClone({ queue: previewQueue, running: previewRunning, completed: previewCompleted, failed: previewFailed })
  const checks = [], releases = [], scenes = {}
  const gate = () => {
    let release, seen
    const promise = new Promise(resolve => { release = resolve })
    const requested = new Promise(resolve => { seen = resolve })
    releases.push(release)
    return { promise, release, requested, seen }
  }
  let completedGate = null, queueGate = null, failedGate = null, retryGate = null, batchCancelGate = null
  let failCompleted = false, failQueue = false, failFailed = false, failRetry = false
  let failCurrentCancel = false, failBatchCancel = false
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== base) return route.abort()
    if (route.request().method() === 'GET' && ['/api/stream/previews/completed', '/api/stream/previews/queue', '/api/stream/previews/failed'].includes(url.pathname)) {
      const completed = url.pathname.endsWith('/completed'), failureList = url.pathname.endsWith('/failed')
      const delayed = completed ? completedGate : failureList ? failedGate : queueGate
      const failed = completed ? failCompleted : failureList ? failFailed : failQueue
      const result = mock(url, 'GET', null)
      if (delayed) { delayed.seen(); await delayed.promise }
      return route.fulfill({ status: failed ? 503 : 200,
        json: failed ? { detail: completed ? '模拟成品清单读取失败' : failureList ? '模拟失败清单读取失败' : '模拟处理队列读取失败' } : result }).catch(() => {})
    }
    if (route.request().method() === 'POST' && /\/api\/stream\/previews\/failed\/[^/]+\/\d+\/retry$/.test(url.pathname)) {
      const failed = failRetry, body = route.request().postDataJSON()
      if (failed) requests.push({ key: url.pathname, method: 'POST', body, query: Object.fromEntries(url.searchParams) })
      const result = failed ? { detail: '模拟重新生成入队失败' } : mock(url, 'POST', body)
      if (retryGate) { retryGate.seen(); await retryGate.promise }
      return route.fulfill({ status: failed ? 503 : 200, json: result }).catch(() => {})
    }
    const currentCancel = route.request().method() === 'DELETE' && /\/previews\/queue\/[^/]+\/\d+$/.test(url.pathname)
    const batchCancel = route.request().method() === 'POST' && /\/previews\/batches\/[^/]+\/cancel$/.test(url.pathname)
    if (currentCancel || batchCancel) {
      const failed = currentCancel ? failCurrentCancel : failBatchCancel
      const method = route.request().method(), body = route.request().postDataJSON()
      if (failed) requests.push({ key: url.pathname, method, body, query: Object.fromEntries(url.searchParams) })
      const result = failed ? { detail: currentCancel ? '模拟当前影片取消失败' : '模拟批次取消失败' } : mock(url, method, body)
      if (batchCancel && batchCancelGate) { batchCancelGate.seen(); await batchCancelGate.promise }
      return route.fulfill({ status: failed ? 503 : 200, json: result }).catch(() => {})
    }
    return route.continue()
  })
  const page = await context.newPage()
  page.setDefaultTimeout(12000)
  page.on('pageerror', error => errors.push(String(error)))
  const preview = page.getByRole('region', { name: '播放进度缩略图', exact: true })
  const queue = preview.getByRole('region', { name: '处理队列', exact: true })
  const completed = preview.getByRole('region', { name: '已生成缩略图清单', exact: true })
  const toggle = preview.getByRole('button', { name: '已生成缩略图', exact: true })
  const refresh = preview.getByRole('button', { name: '刷新已生成缩略图', exact: true })
  const failed = preview.getByRole('region', { name: '生成失败清单', exact: true })
  const failedToggle = preview.getByRole('button', { name: '生成失败', exact: true })
  const failedRefresh = preview.getByRole('button', { name: '刷新失败清单', exact: true })
  const completedRequests = () => requests.filter(request => request.key === '/api/stream/previews/completed')
  const failedRequests = () => requests.filter(request => request.key === '/api/stream/previews/failed')
  const retryRequests = () => requests.filter(request => /\/previews\/failed\/[^/]+\/\d+\/retry$/.test(request.key))
  async function openLibrary(id = 1) {
    await page.goto(base + '/settings?sec=sec-libtools&library=' + id + '&media=1')
    await page.locator('.tool-views:visible').getByRole('button', { name: '资料维护', exact: true }).click()
  }
  async function captureViewports(scene) {
    const viewports = []
    // Keep keyboard focus in this maintenance tool, away from the global skip link.
    const captureFocus = baseline ? toggle : failedToggle
    await captureFocus.focus()
    // Tall element captures can include fixed content outside the viewport clip.
    // Hide only the unfocused, offscreen skip link while taking screenshots.
    const screenshotStyle = '.skip-link:not(:focus) { visibility: hidden !important; }'
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
      await preview.scrollIntoViewIfNeeded()
      assert.equal(await captureFocus.evaluate(el => el === document.activeElement), true, `${scene} captures with focus in the preview tool`)
      assert.equal(await page.locator('.skip-link').evaluate(el => el === document.activeElement), false, `${scene} skip link is not focused`)
      await preview.screenshot({ path: path.join(directory, `${scene}-${width}.png`), animations: 'disabled', style: screenshotStyle })
      await page.screenshot({ path: path.join(directory, `${scene}-${width}-viewport.png`), animations: 'disabled', style: screenshotStyle })
      const metrics = await preview.evaluate(el => ({
        headings: [...el.querySelectorAll('h5')].map(h => ({ text: h.textContent, fontSize: getComputedStyle(h).fontSize })),
        rowCount: el.querySelectorAll('.queue-row').length,
        overflow: document.documentElement.scrollWidth > innerWidth,
        focusedAction: document.activeElement?.getAttribute('aria-label'),
        skipLinkFocused: !!document.querySelector('.skip-link:focus'),
      }))
      assert.equal(metrics.overflow, false, `${scene} does not overflow at ${width}px`)
      if (width < 700) {
        for (const locator of [preview.locator('.select-item').first(), preview.locator('.queue-action').first(),
          queue.getByRole('group').getByRole('button').first(), failed.getByRole('button', { name: /^重新生成 / }).first()]) {
          if (!await locator.count()) continue
          const box = await locator.boundingBox()
          assert.ok(box.width >= 44 && box.height >= 44, `${scene} phone queue targets are at least 44px`)
        }
      }
      viewports.push({ width, ...metrics })
    }
    scenes[scene] = viewports
    return viewports
  }
  try {
    if (!baseline) queueGate = gate()
    await openLibrary()
    if (queueGate) {
      await queueGate.requested
      await queue.getByText(/正在加载/).waitFor()
      assert.equal(await queue.getByText('当前视频库没有待处理影片。', { exact: true }).count(), 0)
      queueGate.release(); queueGate = null
      checks.push('unresolved initial queue request is loading rather than empty')
    }
    await preview.getByText('处理样本', { exact: true }).waitFor()
    await preview.getByText('队列样本 1', { exact: true }).waitFor()
    const viewports = await captureViewports(baseline ? 'before' : 'after')
    if (!baseline) {
      assert.equal(await queue.locator('.queue-list > li').first().getAttribute('class').then(value => value.includes('is-running')), true)
      assert.equal(await queue.locator('.is-running input[type="checkbox"]').count(), 0, 'current item cannot enter pending selection')
      assert.equal(await queue.locator('.is-running progress').getAttribute('value'), '4')
      assert.equal(await queue.locator('.is-running progress').getAttribute('max'), '10')
      await queue.locator('.is-running').getByText('4/10 张 · 40%', { exact: true }).waitFor()
      assert.equal(requests.filter(request => request.key === '/api/stream/previews/jobs/latest').length, 0,
        'maintenance status comes from the persistent queue rather than the latest single worker job')
      assert.doesNotMatch(await preview.innerText(), /\b0\s*\/\s*1\b/, 'single worker counters never become the queue progress')
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false')
      assert.equal(completedRequests().length, 0, 'collapsed completed list is lazy')
      assert.equal(await failedToggle.getAttribute('aria-expanded'), 'false')
      assert.equal(failedRequests().length, 0, 'collapsed failed list is lazy')
      checks.push('one queue with pinned current item, frame progress, current excluded from selection; completed lazy')

      previewRunning.frames = 0
      previewRunning.frame_total = 0
      await queue.locator('.is-running').getByText('准备中', { exact: true }).waitFor()
      assert.equal(await queue.locator('.is-running progress').getAttribute('value'), null,
        'an unknown frame count uses an indeterminate progress indicator')
      assert.doesNotMatch(await queue.locator('.is-running').innerText(), /\d+%|0\s*\/\s*1/,
        'preparation never invents a percentage or a one-item job total')
      await captureViewports('queue-unknown-frames')
      previewRunning.frames = original.running.frames
      previewRunning.frame_total = original.running.frame_total
      await queue.locator('.is-running').getByText('4/10 张 · 40%', { exact: true }).waitFor()
      checks.push('known frame count includes percentage; unknown count stays indeterminate on desktop and phones, with no latest-job request or 0/1 summary')

      previewRunning.waiting = true
      await queue.locator('.is-running').getByText('等待播放转码释放资源，随后继续生成。', { exact: true }).waitFor()
      checks.push('playing-transcode wait reason updates in current row')
      completedGate = gate()
      await toggle.focus()
      await page.keyboard.press('Space')
      await completedGate.requested
      await completed.getByText(/正在加载/).waitFor()
      assert.equal(await completed.getByText(/还没有.*|没有已生成.*|暂无.*/).count(), 0, 'unresolved completed request is not empty')
      assert.equal(await toggle.evaluate(el => el === document.activeElement), true, 'keyboard expansion retains focus')
      await page.screenshot({ path: path.join(directory, 'completed-loading-320.png'), animations: 'disabled' })
      completedGate.release(); completedGate = null
      await completed.getByText('成品样本 1', { exact: true }).waitFor()
      await completed.getByText('旧版可用', { exact: true }).waitFor()
      const callsAfterExpand = completedRequests().length
      await page.waitForResponse(response => new URL(response.url()).pathname === '/api/stream/previews/queue')
      assert.equal(completedRequests().length, callsAfterExpand, 'queue polling does not poll completed manifests')
      await completed.getByRole('button', { name: '已生成下一页', exact: true }).click()
      await completed.getByText('成品样本 53', { exact: true }).waitFor()
      assert.equal(await queue.getByText('队列样本 1', { exact: true }).count(), 1, 'completed pagination preserves queue page')
      await completed.getByRole('button', { name: '已生成上一页', exact: true }).click()
      await completed.getByText('成品样本 1', { exact: true }).waitFor()
      checks.push('keyboard toggle, loading distinct from empty, old complete status, independent pagination, no completed polling')

      previewRunning.waiting = false
      previewRunning.frames = previewRunning.frame_total
      await queue.locator('.is-running').getByText('抽帧完成，正在整理缩略图。', { exact: true }).waitFor()
      assert.equal(await completed.getByText('处理样本', { exact: true }).count(), 0, '100 percent extraction is not a complete manifest')
      previewCompleted.unshift({ ...original.completed[0], id: 91, title: '处理样本', frames: 10 })
      previewRunning = null
      await queue.getByText('处理样本', { exact: true }).waitFor({ state: 'hidden' })
      await completed.getByText('处理样本', { exact: true }).waitFor()
      checks.push('100 percent remains processing until ready; completed transition refreshes an open list')

      failCompleted = true
      await refresh.click()
      await completed.getByRole('alert').filter({ hasText: '模拟成品清单读取失败' }).waitFor()
      await page.screenshot({ path: path.join(directory, 'completed-error-320.png'), animations: 'disabled' })
      failCompleted = false
      await completed.getByRole('button', { name: '重试加载已生成缩略图', exact: true }).click()
      await completed.getByText('成品样本 1', { exact: true }).waitFor()
      previewCompleted = []
      await refresh.click()
      await completed.getByText(/没有.*(生成|缩略)|暂无/).waitFor()
      await page.screenshot({ path: path.join(directory, 'completed-empty-320.png'), animations: 'disabled' })
      previewCompleted = structuredClone(original.completed)
      previewCompleted.unshift({ ...previewCompleted[0], id: 499, title: '刷新后新增成品' })
      await refresh.focus()
      await page.keyboard.press('Enter')
      await completed.getByText('刷新后新增成品', { exact: true }).waitFor()
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '刷新已生成缩略图')
      completedGate = gate()
      await page.keyboard.press('Enter')
      await completedGate.requested
      await toggle.focus()
      const refreshResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/stream/previews/completed')
      completedGate.release(); completedGate = null
      await refreshResponse
      await page.waitForFunction(() => document.querySelector('[aria-label="刷新已生成缩略图"]')?.disabled === false)
      assert.equal(await toggle.evaluate(el => el === document.activeElement), true, 'a completed refresh cannot steal deliberately moved focus')
      checks.push('completed error retry, true empty, explicit refresh sees a new complete item; keyboard focus restores without stealing focus')

      failedGate = gate()
      await failedToggle.focus()
      await page.keyboard.press('Space')
      await failedGate.requested
      await failed.getByText(/正在加载/).waitFor()
      assert.equal(await failed.getByText(/没有.*失败|暂无.*失败/).count(), 0, 'unresolved failures request is not empty')
      assert.equal(await failedToggle.evaluate(el => el === document.activeElement), true, 'failure expansion retains keyboard focus')
      await page.screenshot({ path: path.join(directory, 'failed-loading-320.png'), animations: 'disabled' })
      failedGate.release(); failedGate = null
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      await failed.getByText(original.failed[0].error, { exact: true }).first().waitFor()
      await failed.getByRole('navigation', { name: '生成失败分页', exact: true }).getByRole('button', { name: '失败下一页', exact: true }).click()
      await failed.getByText('失败样本 53', { exact: true }).waitFor()
      assert.ok(failedRequests().some(request => request.query.offset === '50' && request.query.limit === '50' && request.query.library_id === '1'))
      assert.equal(await queue.getByText('队列样本 1', { exact: true }).count(), 1, 'failure pagination preserves the queue page')
      assert.equal(await completed.getByText('刷新后新增成品', { exact: true }).count(), 1, 'failure pagination preserves the completed page')
      await failed.getByRole('button', { name: '失败上一页', exact: true }).click()
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      failFailed = true
      await failedRefresh.click()
      await failed.getByRole('alert').filter({ hasText: '模拟失败清单读取失败' }).waitFor()
      await page.screenshot({ path: path.join(directory, 'failed-error-320.png'), animations: 'disabled' })
      failFailed = false
      await failed.getByRole('button', { name: '重试加载失败清单', exact: true }).click()
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      previewFailed = []
      await failedRefresh.click()
      await failed.getByText(/没有.*失败|暂无.*失败/).waitFor()
      await page.screenshot({ path: path.join(directory, 'failed-empty-320.png'), animations: 'disabled' })
      previewFailed = structuredClone(original.failed)
      await failedRefresh.focus()
      await page.keyboard.press('Enter')
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '刷新失败清单')
      failedGate = gate()
      await page.keyboard.press('Enter')
      await failedGate.requested
      await failedToggle.focus()
      const failedRefreshResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/stream/previews/failed')
      failedGate.release(); failedGate = null
      await failedRefreshResponse
      await page.waitForFunction(() => document.querySelector('[aria-label="刷新失败清单"]')?.disabled === false)
      assert.equal(await failedToggle.evaluate(el => el === document.activeElement), true, 'a failure refresh cannot steal deliberately moved focus')
      previewFailed.unshift({ ...original.failed[0], id: 699, title: '轮询新增失败' })
      await failed.getByText('轮询新增失败', { exact: true }).waitFor()
      previewFailed = previewFailed.filter(item => item.id !== 699)
      await failed.getByText('轮询新增失败', { exact: true }).waitFor({ state: 'hidden' })
      await failedToggle.click()
      const callsAfterCollapse = failedRequests().length
      await page.waitForResponse(response => new URL(response.url()).pathname === '/api/stream/previews/queue')
      assert.equal(failedRequests().length, callsAfterCollapse, 'collapsed failure list stops polling')
      await failedToggle.click()
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      checks.push('failures lazy keyboard expansion, loading/error/empty and retry, per-library 50-item pagination, refresh focus isolation and live updates only while expanded')

      const longTitle = '长名称样本：' + '电影与剧集花絮画面'.repeat(18)
      const longReason = '无法读取视频片源，请检查媒体库连接和文件权限后重新生成。'.repeat(22)
      previewRunning = { ...structuredClone(original.running), title: '当前长名称：' + '影片预览抽帧'.repeat(20) }
      previewQueue[0] = { ...previewQueue[0], title: longTitle, file_name: '长文件名_'.repeat(30) + '.mkv' }
      previewCompleted[0] = { ...previewCompleted[0], title: longTitle, file_name: 'completed-long-name_'.repeat(20) + '.mkv' }
      previewFailed[0] = { ...previewFailed[0], title: longTitle, file_name: 'failed-long-name_'.repeat(25) + '.mkv', error: longReason }
      previewQueue = previewQueue.slice(0, 2)
      previewCompleted = previewCompleted.slice(0, 3)
      previewFailed = previewFailed.slice(0, 2)
      await refresh.click()
      await failedRefresh.click()
      await queue.getByText(longTitle, { exact: true }).waitFor()
      await queue.getByText(previewRunning.title, { exact: true }).waitFor()
      await completed.getByText(longTitle, { exact: true }).waitFor()
      await failed.getByText(longTitle, { exact: true }).waitFor()
      await failed.getByText(longReason, { exact: true }).waitFor()
      assert.equal(await queue.getByText(longTitle, { exact: true }).getAttribute('title'), longTitle)
      assert.equal(await failed.getByText(longTitle, { exact: true }).getAttribute('title'), longTitle)
      const reasonStyle = await failed.getByText(longReason, { exact: true }).evaluate(el => ({
        whiteSpace: getComputedStyle(el).whiteSpace, scroll: el.scrollHeight, client: el.clientHeight,
      }))
      assert.notEqual(reasonStyle.whiteSpace, 'nowrap', 'failure reasons can wrap')
      assert.ok(reasonStyle.scroll <= reasonStyle.client + 1, 'the full failure reason is readable without clipping')
      await captureViewports('after-expanded-long')
      checks.push('long titles, filenames and full multiline failure reasons at desktop 1440 and phones 390/320, 44px actions without overflow')

      previewQueue = structuredClone(original.queue)
      previewCompleted = structuredClone(original.completed)
      previewFailed = structuredClone(original.failed)
      previewRunning = structuredClone(original.running)
      await openLibrary()
      await queue.getByText('队列样本 1', { exact: true }).waitFor()
      await queue.getByRole('checkbox', { name: '选择 队列样本 1', exact: true }).check()
      await queue.getByRole('navigation', { name: '处理队列分页', exact: true }).getByRole('button', { name: '下一页', exact: true }).click()
      await queue.getByRole('checkbox', { name: '选择 队列样本 52', exact: true }).check()
      await queue.getByText('已选 2 项', { exact: true }).waitFor()
      await queue.getByRole('button', { name: '批量移出选中项', exact: true }).click()
      await preview.getByText('已移出 2 项待处理影片。', { exact: true }).waitFor()
      await queue.getByRole('checkbox', { name: '全选本库待处理项', exact: true }).check()
      await queue.getByText('已选 50 项', { exact: true }).waitFor()
      await queue.getByRole('checkbox', { name: '选择 队列样本 2', exact: true }).uncheck()
      await queue.getByText('已选 49 项', { exact: true }).waitFor()
      await queue.getByRole('button', { name: '批量移出选中项', exact: true }).click()
      await preview.getByText('已移出 49 项待处理影片。', { exact: true }).waitFor()
      assert.equal(await queue.getByText('处理样本', { exact: true }).count(), 1, 'bulk pending removal preserves current')
      await queue.getByRole('button', { name: '从待处理队列移除 队列样本 2', exact: true }).click()
      await queue.getByRole('button', { name: '取消当前影片 处理样本', exact: true }).click()
      await queue.getByText('当前视频库没有待处理影片。', { exact: true }).waitFor()
      checks.push('cross-page selection, all-pages select/exclude, bulk removal preserves current, single removal and current cancellation')

      failQueue = true
      await openLibrary()
      await queue.getByRole('alert').filter({ hasText: '模拟处理队列读取失败' }).waitFor()
      failQueue = false
      await queue.getByRole('button', { name: '重试加载处理队列', exact: true }).click()
      await queue.getByText('当前视频库没有待处理影片。', { exact: true }).waitFor()
      checks.push('queue failure retry and real empty')

      previewQueue = structuredClone(original.queue.slice(0, 2))
      previewQueue[1] = { ...previewQueue[1], source: 'retry', auto: false }
      previewRunning = structuredClone(original.running)
      previewFailed = [original.failed[0], ...previewQueue, previewRunning].map(item => ({
        ...item, failed_at: 1780000000, error_code: 'timeout', error: '生成缩略图超时，请稍后重新生成。',
      }))
      await openLibrary()
      await queue.getByText('队列样本 1', { exact: true }).waitFor()
      await failedToggle.click()
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      const retryButton = title => failed.getByRole('button', { name: '重新生成 ' + title, exact: true })
      retryGate = gate()
      const initialRetryCalls = retryRequests().length
      await retryButton('失败样本 1').focus()
      await page.keyboard.press('Enter')
      await retryGate.requested
      assert.equal(await retryButton('失败样本 1').isDisabled(), true, 'the pending retry button is busy')
      assert.equal(await retryButton('失败样本 1').getAttribute('aria-busy'), 'true')
      assert.equal(await retryButton('队列样本 1').isDisabled(), true, 'other retry buttons cannot race the current mutation')
      await retryButton('失败样本 1').evaluate(button => button.click())
      assert.equal(retryRequests().length, initialRetryCalls + 1, 'busy retry ignores repeated activation')
      retryGate.release(); retryGate = null
      await failed.getByRole('status').filter({ hasText: '已将“失败样本 1”加入处理队列末尾。' }).waitFor()
      await queue.getByText('失败样本 1', { exact: true }).waitFor()
      assert.equal(await retryButton('失败样本 1').evaluate(button => button === document.activeElement), true, 'retry restores keyboard focus')
      assert.equal(previewQueue.at(-1).id, original.failed[0].id, 'a fresh retry appends to the queue tail')
      assert.equal(previewRunning.id, original.running.id, 'retry leaves the current task running')
      assert.equal(await failed.getByText('失败样本 1', { exact: true }).count(), 1, 'queueing preserves the unresolved failure')
      assert.deepEqual(retryRequests().at(-1).body, { library_id: 1 })
      await retryButton('队列样本 1').click()
      await failed.getByRole('status').filter({ hasText: '已将“队列样本 1”加入处理队列末尾。' }).waitFor()
      assert.deepEqual(previewQueue.map(item => item.id), [201, 700, 200], 'an auto item upgrades to retry and moves behind earlier retries')
      assert.equal(previewQueue.at(-1).source, 'retry')
      await queue.locator('.queue-file').filter({ hasText: '手动重试' }).first().waitFor()
      await retryButton('队列样本 2').click()
      await failed.getByRole('status').filter({ hasText: '“队列样本 2”已在处理队列中。' }).waitFor()
      assert.deepEqual(previewQueue.map(item => item.id), [201, 700, 200], 'an existing retry keeps its position')
      await retryButton('处理样本').click()
      await failed.getByRole('status').filter({ hasText: '“处理样本”正在处理中。' }).waitFor()
      assert.equal(previewQueue.length, 3, 'a running item is never duplicated')
      failRetry = true
      await retryButton('失败样本 1').click()
      await failed.getByRole('alert').filter({ hasText: '模拟重新生成入队失败' }).waitFor()
      failRetry = false
      assert.equal(await retryButton('失败样本 1').isEnabled(), true, 'failed retry can be attempted again')
      await retryButton('失败样本 1').click()
      await failed.getByRole('status').filter({ hasText: '“失败样本 1”已在处理队列中。' }).waitFor()
      assert.equal(await failed.getByRole('alert').count(), 0, 'successful retry clears the mutation error')
      previewRunning = { ...previewQueue.find(item => item.id === 700), job_id: 'preview-manual-retry', waiting: false, frames: 1, frame_total: 10 }
      previewQueue = previewQueue.filter(item => item.id !== 700)
      await queue.locator('.is-running').getByText('失败样本 1', { exact: true }).waitFor()
      const remainingQueue = structuredClone(previewQueue)
      await queue.getByRole('button', { name: '取消当前影片 失败样本 1', exact: true }).click()
      await queue.locator('.is-running').waitFor({ state: 'hidden' })
      assert.deepEqual(previewQueue, remainingQueue, 'cancelling a running retry preserves other pending tasks')
      assert.equal(await failed.getByText('失败样本 1', { exact: true }).count(), 1, 'cancelling a retry preserves the failure reason')
      checks.push('retry request busy and duplicate guard, queued/already_queued/running feedback, auto upgrade moves to queue tail, failure recovery, focus restoration and cancelling only the running retry')

      previewQueue = [
        { ...original.queue[0], title: '独立闲时样本' },
        { ...original.queue[1], title: '独立重试样本', source: 'retry', auto: false },
      ]
      previewRunning = structuredClone(original.running)
      await openLibrary()
      await queue.getByText('独立闲时样本', { exact: true }).waitFor()
      const startBatch = preview.getByRole('button', { name: '立即批量生成', exact: true })
      const batchGroup = number => queue.getByRole('group', { name: `立即批量生成批次 ${number}`, exact: true })
      const batchCancel = number => batchGroup(number).getByRole('button', { name: `取消本批次 ${number}`, exact: true })
      const batchRequests = () => requests.filter(request => /\/previews\/batches\/[^/]+\/cancel$/.test(request.key))
      const batchDialog = page.getByRole('dialog', { name: '取消本批次', exact: true })
      const confirmBatch = () => batchDialog.getByRole('button', { name: '确认取消本批次', exact: true })
      await startBatch.focus()
      await page.keyboard.press('Enter')
      await batchGroup(1).getByText('本批次剩余 3 项 · 处理中 0 · 等待 3', { exact: true }).waitFor()
      const firstBatchId = previewQueue.find(item => item.batch_id)?.batch_id
      assert.equal(previewRunning.id, original.running.id, 'a new batch appends without replacing the current item')
      assert.equal(await startBatch.isEnabled(), true, 'another batch can be queued while work is running')
      await startBatch.click()
      await batchGroup(2).getByText('本批次剩余 3 项 · 处理中 0 · 等待 3', { exact: true }).waitFor()
      const secondBatchId = previewQueue.find(item => item.batch_id && item.batch_id !== firstBatchId)?.batch_id
      assert.ok(firstBatchId && secondBatchId && firstBatchId !== secondBatchId)
      const jobRequests = requests.filter(request => request.key === '/api/stream/previews/jobs' && request.method === 'POST')
      assert.equal(jobRequests.length, 2)
      assert.ok(jobRequests.every(request => request.body.library_id === 1))
      const firstCurrent = previewQueue.find(item => item.batch_id === firstBatchId)
      previewQueue = previewQueue.filter(item => item.id !== firstCurrent.id)
      previewRunning = { ...firstCurrent, job_id: 'preview-batch-worker-one', frames: 12, frame_total: 100,
        waiting: false, done: 0, total: 1 }
      await queue.locator('.is-running').getByText(firstCurrent.title, { exact: true }).waitFor()
      await batchGroup(1).getByText('本批次剩余 3 项 · 处理中 1 · 等待 2', { exact: true }).waitFor()
      await queue.locator('.is-running').getByText('12/100 张 · 12%', { exact: true }).waitFor()
      assert.doesNotMatch(await preview.innerText(), /\b0\s*\/\s*1\b/)
      await captureViewports('queue-two-batches')

      const firstPending = structuredClone(previewQueue)
      const cancelCurrent = queue.getByRole('button', { name: `取消当前影片 ${firstCurrent.title}`, exact: true })
      failCurrentCancel = true
      await cancelCurrent.focus()
      await page.keyboard.press('Enter')
      await preview.getByRole('alert').filter({ hasText: '模拟当前影片取消失败' }).waitFor()
      assert.deepEqual(previewQueue, firstPending, 'a failed current-item cancellation leaves pending work intact')
      assert.equal(previewRunning.id, firstCurrent.id)
      assert.equal(await cancelCurrent.isEnabled(), true, 'failed current cancellation can be retried')
      failCurrentCancel = false
      await cancelCurrent.focus()
      await page.keyboard.press('Enter')
      await queue.locator('.is-running').waitFor({ state: 'hidden' })
      assert.deepEqual(previewQueue, firstPending, 'cancelling one batch item preserves every other item in that batch')
      const currentDeletes = requests.filter(request => request.key === `/api/stream/previews/queue/movie/${firstCurrent.id}` && request.method === 'DELETE')
      assert.equal(currentDeletes.length, 2)
      assert.ok(currentDeletes.every(request => request.query.library_id === '1'))
      assert.equal(requests.filter(request => /\/previews\/jobs\/[^/]+\/cancel$/.test(request.key)).length, 0,
        'the current-item action always deletes one persistent queue item')

      const secondCurrent = previewQueue.find(item => item.batch_id === secondBatchId)
      previewQueue = previewQueue.filter(item => item.id !== secondCurrent.id)
      previewRunning = { ...secondCurrent, job_id: 'preview-batch-worker-two', frames: 2, frame_total: 10,
        waiting: false, done: 0, total: 1 }
      await queue.locator('.is-running').getByText(secondCurrent.title, { exact: true }).waitFor()
      await batchGroup(2).getByText('本批次剩余 3 项 · 处理中 1 · 等待 2', { exact: true }).waitFor()
      const beforeFirstCancel = structuredClone(previewQueue)
      const callsBeforeEscape = batchRequests().length
      await batchCancel(1).focus()
      await page.keyboard.press('Enter')
      await batchDialog.waitFor()
      await page.waitForFunction(() => document.activeElement?.textContent?.trim() === '继续生成')
      await page.keyboard.press('Tab')
      assert.equal(await batchDialog.evaluate(el => el.contains(document.activeElement)), true, 'batch confirmation traps keyboard focus')
      await page.keyboard.press('Escape')
      await batchDialog.waitFor({ state: 'hidden' })
      assert.equal(batchRequests().length, callsBeforeEscape, 'Escape closes confirmation without cancelling')
      assert.equal(await batchCancel(1).evaluate(el => el === document.activeElement), true, 'Escape restores the batch trigger')
      await batchCancel(1).click()
      failBatchCancel = true
      await confirmBatch().click()
      await batchDialog.getByRole('alert').filter({ hasText: '模拟批次取消失败' }).waitFor()
      assert.deepEqual(previewQueue, beforeFirstCancel)
      assert.equal(previewRunning.id, secondCurrent.id)
      assert.equal(await confirmBatch().isEnabled(), true, 'the failed batch confirmation remains available for retry')
      await page.screenshot({ path: path.join(directory, 'batch-cancel-error-320.png'), animations: 'disabled' })
      failBatchCancel = false
      await confirmBatch().focus()
      await page.keyboard.press('Enter')
      await batchDialog.waitFor({ state: 'hidden' })
      await queue.getByRole('group').getByText('本批次剩余 3 项 · 处理中 1 · 等待 2', { exact: true }).waitFor()
      await page.waitForFunction(() => document.activeElement?.matches('.queue-heading h5'))
      assert.deepEqual(previewQueue, beforeFirstCancel.filter(item => item.batch_id !== firstBatchId))
      assert.equal(previewRunning.id, secondCurrent.id, 'cancelling the first batch preserves the second batch current item')
      assert.ok(previewQueue.some(item => item.source === 'auto') && previewQueue.some(item => item.source === 'retry'),
        'batch cancellation preserves independent auto and retry work')
      assert.equal(batchRequests().at(-1).key, `/api/stream/previews/batches/${firstBatchId}/cancel`)
      assert.deepEqual(batchRequests().at(-1).body, { library_id: 1 })

      const independentPending = structuredClone(previewQueue.filter(item => !item.batch_id))
      batchCancelGate = gate()
      await batchCancel(1).click() // The remaining batch is relabelled, but retains its real batch_id.
      await confirmBatch().click()
      await batchCancelGate.requested
      assert.equal(await confirmBatch().isDisabled(), true, 'a pending batch cancellation prevents duplicate confirmation')
      await confirmBatch().evaluate(button => button.click())
      assert.equal(batchRequests().filter(request => request.key.includes(secondBatchId)).length, 1)
      await page.keyboard.press('Escape')
      assert.equal(await batchDialog.isVisible(), true, 'a pending cancellation keeps its confirmation open')
      batchCancelGate.release(); batchCancelGate = null
      await batchDialog.waitFor({ state: 'hidden' })
      await queue.locator('.is-running').waitFor({ state: 'hidden' })
      await page.waitForFunction(() => document.activeElement?.matches('.queue-heading h5'))
      assert.deepEqual(previewQueue, independentPending)
      assert.equal(batchRequests().at(-1).key, `/api/stream/previews/batches/${secondBatchId}/cancel`,
        'the second displayed batch cancels its own stable ID after the first one disappears')
      assert.deepEqual(batchRequests().at(-1).body, { library_id: 1 })
      await captureViewports('queue-batches-cancelled')
      checks.push('persistent batch acknowledgements append while running; frame percentage and remaining summaries; current cancellation failure/retry removes one item; two distinct batches cancel independently via captured IDs with confirmation, Escape/focus, API retry and busy duplicate guard; auto/retry work survives')

      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
      await completed.getByText('成品样本 1', { exact: true }).waitFor()
      if (await failedToggle.getAttribute('aria-expanded') !== 'true') await failedToggle.click()
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      completedGate = gate()
      await refresh.click()
      await completedGate.requested
      failedGate = gate()
      await failedRefresh.click()
      await failedGate.requested
      previewCompleted.push({ ...original.completed[0], id: 900, library_id: 2, kind: 'episode', title: '库二成品' })
      previewFailed.push({ ...original.failed[0], id: 901, library_id: 2, kind: 'episode', title: '库二失败', error: '模拟库二片源暂不可用。' })
      await page.locator('.lib-tabs').getByRole('button', { name: '演示媒体库 · 剧集', exact: true }).click()
      await page.locator('.tool-views:visible').getByRole('button', { name: '资料维护', exact: true }).click()
      completedGate.release(); completedGate = null
      failedGate.release(); failedGate = null
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false', 'new library completed list starts collapsed')
      assert.equal(await failedToggle.getAttribute('aria-expanded'), 'false', 'new library failure list starts collapsed')
      await toggle.click()
      await completed.getByText('库二成品', { exact: true }).waitFor()
      assert.equal(await completed.getByText('成品样本 1', { exact: true }).count(), 0, 'late first-library response cannot replace second-library results')
      await failedToggle.click()
      await failed.getByText('库二失败', { exact: true }).waitFor()
      assert.equal(await failed.getByText('失败样本 1', { exact: true }).count(), 0, 'late first-library failure response cannot replace current library results')
      retryGate = gate()
      await retryButton('库二失败').click()
      await retryGate.requested
      await page.locator('.lib-tabs').getByRole('button', { name: '演示媒体库 · 电影', exact: true }).click()
      await page.locator('.tool-views:visible').getByRole('button', { name: '资料维护', exact: true }).click()
      retryGate.release(); retryGate = null
      if (await failedToggle.getAttribute('aria-expanded') !== 'true') await failedToggle.click()
      await failed.getByText('失败样本 1', { exact: true }).waitFor()
      assert.equal(await failed.getByText('库二失败', { exact: true }).count(), 0)
      assert.equal(await failed.getByRole('status').filter({ hasText: '库二失败' }).count(), 0, 'late retry feedback cannot enter the next library')
      checks.push('switching libraries aborts/stales old completed/failure/retry responses, resets collapsed lists and loads current library only')
    }
    await writeFile(path.join(directory, `${baseline ? 'before' : 'after'}-checks.json`), JSON.stringify({
      mocked: true, realVue: true, sourceSha256, viewports, scenes, checks,
      designBaseline: '深色资料维护卡片。当前任务与待处理分别成节；标题和帧数位于左侧，取消/移出为右侧图标。待处理每页50项，复选框与长标题共用行，分页和全库选择位于列表边界。',
      designDirection: '沿用深灰表面与红色当前项标记，17px粗体标题保持层级；当前项置顶并显示帧数与百分比，批次独立显示剩余数和取消范围。手机保留44px操作区域，长标题可访问且不造成横向溢出。',
    }, null, 2) + '\n')
    await Promise.all(['failure.json', 'failure.png'].map(name => rm(path.join(directory, name), { force: true })))
  } catch (error) {
    await page.screenshot({ path: path.join(directory, 'failure.png'), fullPage: true }).catch(() => {})
    await writeFile(path.join(directory, 'failure.json'), JSON.stringify({ checks, message: String(error),
      headings: await page.locator('h4,h5').allTextContents(), text: await preview.allTextContents() }, null, 2) + '\n')
    throw error
  } finally {
    for (const release of releases) release()
    previewQueue = original.queue; previewRunning = original.running; previewCompleted = original.completed; previewFailed = original.failed
    await context.close()
  }
}

async function screenshot(page, name, doc, scene, { animations = 'disabled' } = {}) {
  if (!capture) return
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.evaluate(() => document.fonts.ready)
  const png = path.join(work, name + '.png'), file = 'screenshots/' + name + '.webp'
  await page.screenshot({ path: png, animations })
  execFileSync(await ffmpeg(), ['-hide_banner', '-loglevel', 'error', '-y', '-i', png, '-quality', '86', path.join(root, 'docs/assets', file)])
  const bytes = await readFile(path.join(root, 'docs/assets', file))
  captured.push({ file, page: doc, scene, verified_at: new Date().toISOString().slice(0, 10),
    app_version: JSON.parse(await readFile(path.join(root, 'frontend/package.json'))).version,
    source_commit: execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    source_state: '当前工作区设置、文件管理与播放器回归', source_sha256: sourceSha256,
    source_digest_scope: ['frontend/src/**/*', 'frontend/public/**/*', 'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json'],
    source: '真实 Vue 界面 + 全 API 模拟；虚构资料，无真实配置、数据库或媒体',
    viewport: { ...page.viewportSize(), device_scale_factor: 1 }, recording_script: 'scripts/smoke_settings_ui.mjs --capture-docs',
    fixtures: 'scripts/smoke_settings_ui.mjs 内置内存数据', fixture_sha256: createHash('sha256').update(await readFile(fileURLToPath(import.meta.url))).digest('hex'),
    bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') })
}
async function selectionScreenshot(page, name) {
  if (!capture) return
  const directory = path.join(root, 'output/playwright/file-selection')
  await mkdir(directory, { recursive: true })
  await page.evaluate(() => { window.scrollTo(0, 0); window.getSelection()?.removeAllRanges() })
  await page.screenshot({ path: path.join(directory, name + '.png'), animations: 'disabled', fullPage: true })
}
async function checkFileLoading(browser, base) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  const gates = []
  const gate = () => {
    let release
    const promise = new Promise(resolve => { release = resolve })
    gates.push(release)
    return { promise, release }
  }
  const libraries = gate(), directory = gate(), nextPage = gate(), background = gate()
  let changeSummary = gate(), failLibraries = false, emptyLibraries = false, failDirectory = false
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== base) return route.abort()
    if (url.pathname === '/api/libraries') {
      await libraries.promise
      if (failLibraries) return route.fulfill({ status: 503, json: { detail: '模拟连接失败' } })
      if (emptyLibraries) return route.fulfill({ json: { items: [], default_id: null } })
    }
    if (url.pathname === '/api/fs/list') {
      if (failDirectory) return route.fulfill({ status: 503, json: { detail: '模拟目录读取失败' } })
      await directory.promise
      if (Number(url.searchParams.get('offset')) > 0) await nextPage.promise
    }
    if (url.pathname === '/api/fs/changes') await changeSummary.promise
    if (['/api/settings', '/api/jobs/stats', '/api/tv/stats', '/api/metadata/providers'].includes(url.pathname)) await background.promise
    await route.continue()
  })
  const page = await context.newPage()
  page.setDefaultTimeout(12000)
  page.on('pageerror', e => errors.push(String(e)))
  const rows = page.getByRole('table', { name: '当前目录文件' }).locator('tbody tr')
  const workspace = page.locator('.library-workspace')
  const noFalseEmpty = async () => {
    assert.equal(await workspace.getByText('还没有视频库。', { exact: false }).count(), 0)
    assert.equal(await page.getByText('此文件夹为空', { exact: true }).count(), 0)
  }
  try {
    await page.goto(base + '/settings?sec=sec-files&library=1')
    await workspace.getByText('正在加载视频库…', { exact: true }).waitFor()
    assert.equal(await page.locator('.library-load-state .jz-spinner').isVisible(), true)
    await noFalseEmpty()
    libraries.release()
    await page.locator('.fs-table-area [role="status"] .jz-spinner').waitFor()
    await noFalseEmpty()
    await page.waitForFunction(() => document.querySelectorAll('.fs-directory-state .jz-spinner').length > 0 && [...document.querySelectorAll('.fs-directory-state .jz-spinner')].every(el =>
      getComputedStyle(el).animationName !== 'none' && Number(getComputedStyle(el).opacity) > 0))
    await screenshot(page, 'file-browser-loading', 'user-guide/files.md', '延迟模拟目录响应，展示首次进入文件管理的加载动画和提示', { animations: 'allow' })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.waitForFunction(() => document.querySelectorAll('.fs-directory-state .jz-spinner').length > 0 && [...document.querySelectorAll('.fs-directory-state .jz-spinner')].every(el =>
      getComputedStyle(el).animationName === 'none' && Number(getComputedStyle(el).opacity) > 0))
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    directory.release()
    await page.getByText('已读取 2 / 3 个文件', { exact: true }).waitFor()
    await noFalseEmpty()
    nextPage.release()
    await rows.nth(4).waitFor()
    assert.equal(await rows.count(), 5, 'file listing does not wait for settings, statistics, providers or file-change summary')
    background.release()
    changeSummary.release()

    // The tool shortcut must mount the directory even while the summary is pending.
    changeSummary = gate()
    await page.goto(base + '/settings?sec=sec-libtools&library=1')
    await page.getByRole('button', { name: '管理文件', exact: true }).click()
    await rows.nth(4).waitFor()
    changeSummary.release()

    failDirectory = true
    await page.getByRole('button', { name: '刷新目录', exact: true }).click()
    await page.getByText('目录加载失败', { exact: true }).waitFor()
    await noFalseEmpty()
    failDirectory = false
    await page.getByRole('button', { name: '重试加载目录', exact: true }).click()
    await rows.nth(4).waitFor()

    failLibraries = true
    await page.goto(base + '/settings?sec=sec-files&library=1')
    await workspace.getByText('视频库加载失败', { exact: true }).waitFor()
    await noFalseEmpty()
    failLibraries = false
    await page.locator('.library-load-state').getByRole('button', { name: '重新加载', exact: true }).click()
    await rows.nth(4).waitFor()
    assert.equal(await page.locator('.library-load-state').count(), 0)

    emptyLibraries = true
    await page.goto(base + '/settings?sec=sec-files')
    await workspace.getByText('还没有视频库。', { exact: false }).waitFor()
    assert.equal(await page.locator('.library-load-state').count(), 0, 'an actually empty library has a distinct settled state')
  } finally {
    for (const release of gates) release()
    await context.close()
  }
}
async function checkCacheSettings(browser, base) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  const gib = 1024 ** 3
  let limits = { data_warning_gb: 20, transcode_cache_gb: 10, preview_cache_gb: 10 }
  let categories = { database: gib, posters: 4 * gib, previews: 10 * gib, transcode: 10 * gib, other: 0 }
  let failLoad = true, failUsage = true, failSave = false, failValidation = false, releaseSave, saveStarted
  let usageReads = 0, usageGate = null
  const usageReleases = []
  const writes = [], cleanCalls = []
  const directory = path.join(root, 'output/playwright/cache-settings')
  await mkdir(directory, { recursive: true })
  const usage = () => {
    const total = Object.values(categories).reduce((sum, value) => sum + value, 0)
    return { ok: true, total_bytes: total, warning_bytes: limits.data_warning_gb * gib,
      over_limit: total >= limits.data_warning_gb * gib, categories, error: '' }
  }
  const reserve = () => {
    const base = (categories.database + categories.posters + categories.other) / gib
    return Math.ceil((base + Math.max(base * 0.1, 1)) * 10 - 1e-8) / 10
  }
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.origin !== base) return route.abort()
    if (url.pathname === '/api/libraries') return route.fulfill({ json: { items: libs.map(item => ({ ...item,
      name: item.name + ' · 家庭录像与超长标题纪录片收藏', media_name: '客厅媒体库与家庭影片收藏' })), default_id: 1 } })
    if (url.pathname === '/api/cache-settings') {
      if (request.method() === 'GET' && failLoad) return route.fulfill({ status: 503, json: { detail: '模拟容量设置加载失败' } })
      if (request.method() === 'PUT') {
        const body = request.postDataJSON()
        writes.push(body)
        if (saveStarted) { saveStarted(); await new Promise(resolve => { releaseSave = resolve }) }
        if (failSave) return route.fulfill({ status: 503, json: { detail: '模拟容量设置保存失败' } })
        if (failValidation || failUsage) return route.fulfill({ status: 503, json: { detail: '模拟保存时完整统计失败' } })
        if (body.transcode_cache_gb <= 0 || body.transcode_cache_gb + body.preview_cache_gb + reserve() > body.data_warning_gb + 1e-8)
          return route.fulfill({ status: 422, json: { detail: '播放缓存、缩略图额度与基础预留超出预算，请重新分配' } })
        limits = body
      }
      return route.fulfill({ json: limits })
    }
    if (url.pathname === '/api/data-usage') {
      usageReads++
      const pending = usageGate
      if (pending) { pending.started(); await pending.promise }
      if (failUsage) return route.fulfill({ status: 503, json: { detail: '模拟完整占用统计失败' } })
      return route.fulfill({ json: usage() })
    }
    const cache = url.pathname === '/api/stream/cache/clean' ? 'transcode'
      : url.pathname === '/api/stream/previews/cache/clean' ? 'previews' : null
    if (cache) {
      const body = request.postDataJSON(), freed = (cache === 'transcode' ? 2 : 3) * gib
      cleanCalls.push({ cache, dry_run: body.dry_run })
      if (!body.dry_run) categories = { ...categories, [cache]: categories[cache] - freed }
      return route.fulfill({ json: { candidates: 2, candidate_bytes: freed, dry_run: body.dry_run,
        removed: body.dry_run ? 0 : 2, freed: body.dry_run ? 0 : freed, total: categories[cache] } })
    }
    return route.continue()
  })
  const page = await context.newPage()
  page.setDefaultTimeout(12000)
  page.on('pageerror', error => errors.push(String(error)))
  const storage = page.locator('.storage-usage-panel')
  const panel = storage.locator('.cache-settings')
  const save = panel.getByRole('button', { name: '保存容量设置', exact: true })
  const warning = panel.getByRole('spinbutton', { name: '应用数据总预算（软上限，GB）', exact: true })
  const quotaLegend = part => panel.locator(`.budget-legend [data-quota="${part}"]`)
  const quotaDialog = part => page.getByRole('dialog', { name: part === 'transcode' ? '精确设置播放缓存额度' : '精确设置进度缩略图额度', exact: true })
  const quotaInput = part => quotaDialog(part).getByLabel(part === 'transcode' ? '播放缓存额度（GB）' : '进度缩略图额度（GB）', { exact: true })
  const quotaControl = part => ({
    inputValue: async () => { await quotaLegend(part).waitFor(); return quotaLegend(part).getAttribute('data-quota-value') },
    fill: async value => {
      if (await quotaLegend(part).getAttribute('data-quota-value') === value) return
      await quotaLegend(part).click()
      await quotaInput(part).fill(value)
      await quotaDialog(part).getByRole('button', { name: '确定', exact: true }).click()
      await quotaDialog(part).waitFor({ state: 'hidden' })
    },
  })
  const transcode = quotaControl('transcode'), previews = quotaControl('previews')
  const refresh = storage.getByRole('button', { name: '刷新占用', exact: true })
  const allocationBar = panel.getByRole('group', { name: '调整缓存预算分配', exact: true })
  const playbackHandle = allocationBar.getByRole('slider', { name: '调整播放缓存额度', exact: true })
  const previewHandle = allocationBar.getByRole('slider', { name: '调整进度缩略图额度', exact: true })
  const navigate = name => page.getByRole('navigation', { name: '设置分区', exact: true }).getByRole('button', { name, exact: true }).click()
  const values = async () => [await warning.inputValue(), await transcode.inputValue(), await previews.inputValue()]
  const expectValues = async expected => assert.deepEqual(await values(), expected, 'budget edits never silently rewrite another allocation')
  const refreshUsage = async () => {
    const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/data-usage')
    await refresh.click()
    await response
  }
  const notifyUsage = async () => {
    const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/data-usage')
    await page.evaluate(() => window.dispatchEvent(new Event('jzmedia:data-usage-changed')))
    await response
  }
  const handleGeometry = async () => {
    const boxes = await Promise.all([playbackHandle.boundingBox(), previewHandle.boundingBox()])
    assert.ok(boxes.every(box => box && box.width >= 44 && box.height >= 44), 'allocation handles provide 44px pointer and touch targets')
    const [upper, lower] = boxes
    assert.ok(upper.x + upper.width <= lower.x || lower.x + lower.width <= upper.x
      || upper.y + upper.height <= lower.y || lower.y + lower.height <= upper.y, 'the two handle hit areas never overlap, including minimum allocations')
  }
  const waitHandleEnabled = async handle => page.waitForFunction(element => !element.disabled, await handle.elementHandle())
  const resetAllocations = async () => { await transcode.fill('40'); await previews.fill('30') }
  const expectApproxAllocations = async (t, p) => {
    const actual = (await values()).slice(1).map(Number)
    assert.ok(Math.abs(actual[0] - t) <= 0.2 && Math.abs(actual[1] - p) <= 0.2,
      `direct manipulation should allocate approximately ${t}/${p} GB; received ${actual.join('/')}`)
  }
  const readScale = async () => allocationBar.evaluate(element => Number(element.dataset.scaleGb || element.querySelector('[data-scale-gb]')?.dataset.scaleGb))
  const tooltip = page.locator('.budget-tooltip[role="tooltip"]')
  const checkTooltip = async (quota, balance) => {
    await tooltip.waitFor()
    assert.match(await tooltip.textContent(), quota)
    if (balance) assert.match(await tooltip.textContent(), balance)
    assert.equal(await tooltip.evaluate(element => element.parentElement === document.body), true, 'the tooltip teleports to the body')
    await page.waitForFunction(() => {
      const element = document.querySelector('.budget-tooltip[role="tooltip"]')
      if (!element) return false
      const rect = element.getBoundingClientRect()
      return rect.left >= 0 && rect.top >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight
    })
  }
  const checkSegmentLabels = async () => {
    const labels = await allocationBar.locator('[data-budget-part] .budget-segment-label').evaluateAll(elements => elements.filter(element =>
      element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden').map(element => {
      const parent = element.closest('[data-budget-part]').getBoundingClientRect(), range = document.createRange()
      range.selectNodeContents(element)
      const text = range.getBoundingClientRect()
      return { value: element.textContent, left: text.left - parent.left, right: parent.right - text.right }
    }))
    assert.ok(labels.every(label => label.left >= -1 && label.right >= -1), 'visible segment labels fit their own segment')
    return labels
  }
  const startMouseDrag = async (handle, deltaGb) => {
    await handle.scrollIntoViewIfNeeded()
    const box = await handle.boundingBox(), track = await allocationBar.locator('.budget-bar-track').boundingBox()
    const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
    await page.mouse.move(point.x, point.y)
    await page.mouse.down()
    assert.equal(await handle.evaluate(element => element.hasPointerCapture(1)), true, 'a real mouse drag captures its pointer')
    const scale = await readScale()
    assert.ok(Number.isFinite(scale) && scale > 0, 'the gesture exposes its current scale in GB')
    await page.mouse.move(point.x + deltaGb / scale * track.width, point.y, { steps: 8 })
    return { point, track, scale }
  }
  const assertUnavailableHandles = async () => {
    for (const handle of [playbackHandle, previewHandle])
      if (await handle.count()) assert.equal(await handle.isDisabled(), true, 'unavailable or invalid allocation cannot be dragged')
  }
  const checkQuotaEditor = async () => {
    const writesBefore = writes.length
    const segment = allocationBar.locator('.budget-segment-edit[data-quota="transcode"]')
    await segment.click()
    await quotaDialog('transcode').waitFor()
    const selected = await quotaInput('transcode').evaluate(input => ({ value: input.value, start: input.selectionStart, end: input.selectionEnd }))
    assert.deepEqual(selected, { value: '40', start: 0, end: 2 }, 'opening exact edit selects the complete value for replacement')
    assert.equal(await save.isDisabled(), true, 'the final save is disabled while an exact-edit popup is open')
    await quotaInput('transcode').fill('45')
    await expectValues(['100', '40', '30'])
    await quotaInput('transcode').press('Enter')
    await quotaDialog('transcode').waitFor({ state: 'hidden' })
    await expectValues(['100', '45', '30'])
    assert.equal(writes.length, writesBefore, 'Enter confirms a local quota without submitting the enclosing settings form')
    assert.equal(await segment.evaluate(element => element === document.activeElement), true, 'closing exact edit restores its segment opener')
    await resetAllocations()

    for (const part of ['transcode', 'previews']) {
      const entry = quotaLegend(part)
      await entry.focus()
      await page.keyboard.press('Enter')
      await quotaInput(part).waitFor()
      for (const invalid of ['', '0', '-1', '1.23', '102400.1', 'abc']) {
        await quotaInput(part).fill(invalid)
        const confirm = quotaDialog(part).getByRole('button', { name: '确定', exact: true })
        if (await confirm.isEnabled()) await confirm.click()
        await quotaDialog(part).waitFor()
        await expectValues(['100', '40', '30'])
      }
      await quotaDialog(part).getByRole('button', { name: '取消', exact: true }).click()
      await quotaDialog(part).waitFor({ state: 'hidden' })
      assert.equal(await entry.evaluate(element => element === document.activeElement), true, 'canceling exact edit restores the legend opener')
    }
    for (const mode of ['cancel', 'escape', 'close']) {
      await quotaLegend('transcode').click()
      await quotaInput('transcode').fill('46')
      await page.locator('.jz-dialog-mask').last().click({ position: { x: 2, y: 2 } })
      await quotaDialog('transcode').waitFor()
      assert.equal(await quotaInput('transcode').inputValue(), '46', 'clicking the backdrop never discards exact input')
      if (mode === 'escape') await page.keyboard.press('Escape')
      else await quotaDialog('transcode').getByRole('button', { name: mode === 'close' ? '取消额度编辑' : '取消', exact: true }).click()
      await quotaDialog('transcode').waitFor({ state: 'hidden' })
      await expectValues(['100', '40', '30'])
    }
    await segment.click()
    await quotaInput('transcode').fill('0.1')
    await quotaDialog('transcode').getByRole('button', { name: '确定', exact: true }).click()
    await quotaDialog('transcode').waitFor({ state: 'hidden' })
    assert.equal(await quotaLegend('transcode').evaluate(element => element === document.activeElement), true, 'when a segment shrinks away focus falls back to its permanent legend entry')
    await resetAllocations()

    await quotaLegend('previews').click()
    await quotaInput('previews').fill('44.4')
    failUsage = true
    await notifyUsage()
    await storage.getByText(/模拟完整占用统计失败/).waitFor()
    assert.equal(await quotaInput('previews').inputValue(), '44.4', 'failed statistics refresh preserves the local input buffer')
    await expectValues(['100', '40', '30'])
    failUsage = false
    await notifyUsage()
    await storage.locator('.storage-total').getByText('25.00G', { exact: false }).waitFor()
    assert.equal(await quotaInput('previews').inputValue(), '44.4')
    await quotaDialog('previews').getByRole('button', { name: '取消', exact: true }).click()

    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
      await quotaLegend('transcode').click()
      const editor = quotaDialog('transcode')
      await quotaInput('transcode').waitFor()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.screenshot({ path: path.join(directory, `capacity-quota-editor-${width}.png`), animations: 'disabled' })
      if (width < 700) {
        const controls = await editor.locator('input,button').evaluateAll(elements => elements.map(element => element.getBoundingClientRect()).map(rect => ({ width: rect.width, height: rect.height })))
        assert.ok(controls.every(rect => rect.width >= 44 && rect.height >= 44), 'phone exact-edit controls have 44px targets')
        // Reversible visualViewport stub models the visible area after a software
        // keyboard opens; it does not claim to drive a physical phone keyboard.
        await page.evaluate(() => {
          const viewport = window.visualViewport
          for (const [key, value] of Object.entries({ height: 360, offsetTop: 180 }))
            Object.defineProperty(viewport, key, { configurable: true, value })
          viewport.dispatchEvent(new Event('resize'))
          viewport.dispatchEvent(new Event('scroll'))
        })
        await page.waitForFunction(() => {
          const editor = document.querySelector('.budget-quota-editor')
          if (!editor) return false
          const rect = editor.getBoundingClientRect(), viewport = window.visualViewport
          return rect.top >= viewport.offsetTop && rect.bottom <= viewport.offsetTop + viewport.height
        })
        await page.screenshot({ path: path.join(directory, `capacity-quota-keyboard-${width}.png`), animations: 'disabled' })
        await page.evaluate(() => {
          const viewport = window.visualViewport
          delete viewport.height; delete viewport.offsetTop
          viewport.dispatchEvent(new Event('resize'))
          viewport.dispatchEvent(new Event('scroll'))
        })
      }
      await editor.getByRole('button', { name: '取消', exact: true }).click()
    }
    await page.setViewportSize({ width: 1440, height: 1000 })
    await expectValues(['100', '40', '30'])
    assert.equal(writes.length, writesBefore)
  }
  const checkQuotaLeave = async () => {
    const writesBefore = writes.length
    await navigate('概览')
    await navigate('系统维护')
    await quotaLegend('transcode').click()
    await quotaInput('transcode').fill('45')
    await expectValues(['100', '40', '30'])
    await page.evaluate(() => history.back())
    const leave = page.getByTestId('settings-leave-dialog')
    await leave.waitFor()
    await leave.getByRole('button', { name: '继续编辑', exact: true }).click()
    await quotaInput('transcode').waitFor()
    assert.equal(await quotaInput('transcode').inputValue(), '45', 'staying after browser Back restores unconfirmed local input')
    assert.equal(await quotaInput('transcode').evaluate(element => element === document.activeElement), true)
    await quotaDialog('transcode').getByRole('button', { name: '取消', exact: true }).click()
    await transcode.fill('45')
    await quotaLegend('transcode').click()
    await quotaInput('transcode').fill('46')
    await page.evaluate(() => history.back())
    await leave.waitFor()
    await leave.getByRole('button', { name: '放弃修改并离开', exact: true }).click()
    await page.locator('.settings-heading h2').getByText('概览', { exact: true }).waitFor()
    await quotaDialog('transcode').waitFor({ state: 'hidden' })
    await navigate('系统维护')
    await expectValues(['100', '40', '30'])
    assert.equal(writes.length, writesBefore, 'discarding both editor input and confirmed unsaved allocation never writes settings')
  }
  const checkDirectAllocation = async () => {
    assert.equal(await panel.locator('.budget-inputs').count(), 0, 'fixed quota inputs are replaced by compact exact-edit entries')
    assert.equal(await panel.locator('.budget-legend button[data-quota]').count(), 2, 'both quotas always have an exact-edit legend entry')
    assert.equal(await panel.locator('.cache-budget-row input').count(), 0, 'actual usage rows do not scatter capacity inputs')
    const actionsBox = await save.boundingBox(), baseBox = await panel.locator('.budget-base').boundingBox()
    assert.ok(actionsBox.y + actionsBox.height <= baseBox.y, 'capacity save actions stay with configuration before the actual usage details')
    await waitHandleEnabled(playbackHandle)
    await handleGeometry()
    assert.equal((await allocationBar.locator('.budget-bar-track').boundingBox()).height, 48, 'the allocation track is 48px high')
    await checkSegmentLabels()
    for (const [part, title, quota] of [['transcode', '播放缓存', '40'], ['previews', '进度缩略图', '30']]) {
      const label = allocationBar.locator(`[data-budget-part="${part}"] .budget-segment-label`)
      await label.waitFor()
      assert.match(await label.textContent(), new RegExp(title))
      assert.match(await label.textContent(), new RegExp(quota + '(?:\\.0)?\\s*GB'))
    }
    const writesBefore = writes.length
    await warning.focus()
    await playbackHandle.hover()
    await checkTooltip(/40(?:\.0)?\s*GB/, /剩余\s*24(?:\.0)?\s*GB/)
    assert.equal(await warning.evaluate(element => element === document.activeElement), true, 'hovering a handle shows its tooltip without taking focus')
    await page.mouse.move(0, 0)
    await tooltip.waitFor({ state: 'hidden' })
    for (const focused of [warning, save]) {
      await focused.focus()
      await allocationBar.locator('[data-budget-part="transcode"]').hover()
      await checkTooltip(/40(?:\.0)?\s*GB/, /剩余\s*24(?:\.0)?\s*GB/)
      await page.evaluate(() => {
        window.budgetEscapePrevented = null
        window.addEventListener('keydown', event => { window.budgetEscapePrevented = event.defaultPrevented }, { once: true })
      })
      await page.keyboard.press('Escape')
      await tooltip.waitFor({ state: 'hidden' })
      assert.equal(await page.evaluate(() => window.budgetEscapePrevented), false, 'closing a hover tooltip from another focused control does not consume Escape')
      assert.equal(await focused.evaluate(element => element === document.activeElement), true, 'Escape closes a segment hover tooltip while preserving the current control focus')
      await page.mouse.move(0, 0)
    }
    for (const [handle, amount] of [[playbackHandle, /40(?:\.0)?\s*GB/], [previewHandle, /30(?:\.0)?\s*GB/]]) {
      await handle.focus()
      await checkTooltip(amount, /剩余\s*24(?:\.0)?\s*GB/)
      await handle.hover()
      await checkTooltip(amount, /剩余\s*24(?:\.0)?\s*GB/)
      await page.mouse.move(0, 0)
      await checkTooltip(amount, /剩余\s*24(?:\.0)?\s*GB/)
      assert.equal(await handle.evaluate(element => element === document.activeElement), true, 'leaving a hovered but keyboard-focused handle restores its focus tooltip')
      await warning.focus()
      await tooltip.waitFor({ state: 'hidden' })
    }
    await startMouseDrag(playbackHandle, 10)
    await expectApproxAllocations(50, 30)
    assert.equal(await previews.inputValue(), '30', 'the playback handle changes only the playback quota')
    await page.mouse.up()
    await resetAllocations()
    await startMouseDrag(previewHandle, 10)
    await expectApproxAllocations(40, 40)
    await page.mouse.up()
    assert.equal(await transcode.inputValue(), '40', 'the preview handle changes only the preview quota')
    assert.equal(writes.length, writesBefore, 'dragging only updates the unsaved draft')
    await resetAllocations()

    await warning.fill('76')
    await previewHandle.focus()
    await checkTooltip(/30(?:\.0)?\s*GB/, /预算刚好用满/)
    await warning.fill('100')
    const crossing = await startMouseDrag(playbackHandle, 24)
    await expectApproxAllocations(64, 30)
    await checkTooltip(/64(?:\.0)?\s*GB/, /预算刚好用满/)
    await page.mouse.move(crossing.point.x + crossing.track.width * 0.25, crossing.point.y, { steps: 4 })
    await expectApproxAllocations(65, 30)
    assert.equal(await readScale(), crossing.scale, 'crossing the total freezes the scale for the entire gesture')
    assert.equal(await save.isDisabled(), true, 'temporary over-allocation disables only saving')
    assert.equal(await playbackHandle.isEnabled(), true)
    await checkTooltip(/65(?:\.0)?\s*GB/, /超出预算\s*1(?:\.0)?\s*GB/)
    await page.mouse.move(crossing.point.x + crossing.track.width * 0.1, crossing.point.y, { steps: 8 })
    await expectApproxAllocations(50, 30)
    assert.equal(await readScale(), crossing.scale)
    assert.equal(await save.isEnabled(), true, 'pulling back within budget keeps the same gesture active')
    await page.mouse.up()
    await resetAllocations()

    const beyond = await startMouseDrag(playbackHandle, 80)
    await expectApproxAllocations(120, 30)
    assert.equal(await readScale(), 100, 'moving beyond the rail continues increasing quota at the captured scale')
    assert.equal(await previews.inputValue(), '30')
    assert.equal(await save.isDisabled(), true)
    await checkTooltip(/120(?:\.0)?\s*GB/, /超出预算\s*56(?:\.0)?\s*GB/)
    const [barBox, handleBox] = await Promise.all([allocationBar.boundingBox(), playbackHandle.boundingBox()])
    assert.ok(handleBox.x >= barBox.x - 1 && handleBox.x + handleBox.width <= barBox.x + barBox.width + 1, 'an off-rail drag clamps its handle within the visible bar')
    await page.mouse.up()
    const overflowScale = await readScale()
    assert.equal(overflowScale, reserve() + Number(await transcode.inputValue()) + Number(await previews.inputValue()), 'release normalizes the scale to the over-allocated total')
    const trackBox = await allocationBar.locator('.budget-bar-track').boundingBox()
    const marker = await allocationBar.locator('.budget-limit-marker').boundingBox()
    const overflow = await allocationBar.locator('.budget-overflow-zone').boundingBox()
    assert.ok(Math.abs((marker.x - trackBox.x) / trackBox.width - 100 / overflowScale) < 0.01, 'the limit marker remains at the actual total budget')
    assert.ok(overflow.width > 0 && overflow.x + overflow.width <= trackBox.x + trackBox.width + 2, 'the overflow zone shades the budget-exceeding portion')
    assert.deepEqual(await allocationBar.locator('[data-budget-part]').evaluateAll(elements => elements.map(element => element.dataset.budgetPart)),
      ['base', 'transcode', 'previews', 'free'], 'overflow does not invent an additional data category')
    await waitHandleEnabled(playbackHandle)
    await checkTooltip(/120(?:\.0)?\s*GB/, /超出预算\s*56(?:\.0)?\s*GB/)
    await page.screenshot({ path: path.join(directory, 'capacity-overflow-1440.png'), animations: 'disabled' })
    const returning = await startMouseDrag(playbackHandle, -80)
    await expectApproxAllocations(40, 30)
    assert.equal(returning.scale, overflowScale)
    assert.equal(await readScale(), overflowScale, 'returning from an overflow keeps its start scale until release')
    assert.equal(await save.isEnabled(), true)
    await page.mouse.up()
    assert.equal(await readScale(), 100, 'release restores the total-budget scale after correcting an overflow')
    await expectValues(['100', '40', '30'])

    await warning.fill('6')
    await transcode.fill('0.1')
    await previews.fill('0.1')
    const minimumError = panel.locator('.budget-error')
    assert.match(await minimumError.textContent(), /至少需要 6\.2 GB 总预算/)
    assert.match(await minimumError.textContent(), /仅调低缓存额度仍不足/)
    assert.equal(await panel.getByText(/可直接拖小任一缓存额度/).count(), 0, 'a budget below the base reserve plus minimum quotas never suggests quota reduction alone will fit')
    for (const handle of [playbackHandle, previewHandle]) {
      await waitHandleEnabled(handle)
      await startMouseDrag(handle, 0.5)
      await expectApproxAllocations(handle === playbackHandle ? 0.6 : 0.1, handle === previewHandle ? 0.6 : 0.1)
      await page.mouse.up()
      assert.equal(await save.isDisabled(), true, 'a base reserve that consumes the total keeps saving disabled despite editable quotas')
      await handle.focus()
      await page.keyboard.press('Home')
      await expectValues(['6', '0.1', '0.1'])
      assert.equal(await save.isDisabled(), true, 'both minimum quotas still require at least 6.2 GB')
      assert.match(await minimumError.textContent(), /至少需要 6\.2 GB 总预算/)
      assert.equal(await panel.getByText(/可直接拖小任一缓存额度/).count(), 0)
    }
    await warning.fill('100')
    await resetAllocations()

    // Keep a real primary mouse drag active while simulating a different
    // pointer's terminal event. This checks pointerId isolation, not physical multitouch.
    for (const [handle, moved, continued] of [[playbackHandle, [50, 30], [60, 30]], [previewHandle, [40, 40], [40, 50]]]) {
      for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
        await resetAllocations()
        const { point, track } = await startMouseDrag(handle, 10)
        await expectApproxAllocations(...moved)
        await handle.dispatchEvent(type, { pointerId: 999, pointerType: 'touch', isPrimary: false, button: 0, bubbles: true })
        await expectApproxAllocations(...moved)
        assert.equal(await handle.evaluate(element => element.hasPointerCapture(1)), true, 'a foreign pointer termination preserves primary pointer capture')
        await page.mouse.move(point.x + track.width * 0.2, point.y, { steps: 8 })
        await expectApproxAllocations(...continued)
        await page.mouse.up()
        await expectApproxAllocations(...continued)
      }
    }
    for (const [handle, moved] of [[playbackHandle, [50, 30]], [previewHandle, [40, 40]]]) {
      await resetAllocations()
      const { point, track } = await startMouseDrag(handle, 10)
      await expectApproxAllocations(...moved)
      await page.keyboard.press('Tab')
      assert.equal(await handle.evaluate(element => element === document.activeElement), false, 'Tab moves focus away from an active drag handle')
      await expectValues(['100', '40', '30'])
      await page.mouse.move(point.x + track.width * 0.2, point.y, { steps: 8 })
      await page.mouse.up()
      await expectValues(['100', '40', '30'])
    }
    await resetAllocations()

    await playbackHandle.focus()
    await page.keyboard.press('ArrowRight')
    await expectValues(['100', '40.1', '30'])
    await checkTooltip(/40\.1\s*GB/, /剩余\s*23\.9\s*GB/)
    assert.equal(await playbackHandle.evaluate(element => element === document.activeElement), true, 'a changing tooltip preserves handle focus')
    await page.keyboard.press('Shift+ArrowRight')
    await expectValues(['100', '41.1', '30'])
    await page.keyboard.press('PageUp')
    await expectValues(['100', '51.1', '30'])
    await page.keyboard.press('PageDown')
    await expectValues(['100', '41.1', '30'])
    await page.keyboard.press('Home')
    await expectValues(['100', '0.1', '30'])
    await page.keyboard.press('End')
    await expectValues(['100', '102400', '30'])
    await resetAllocations()
    await previewHandle.focus()
    await page.keyboard.press('ArrowLeft')
    await expectValues(['100', '40', '29.9'])
    await page.keyboard.press('Shift+ArrowLeft')
    await expectValues(['100', '40', '28.9'])
    await page.keyboard.press('PageDown')
    await expectValues(['100', '40', '18.9'])
    await page.keyboard.press('PageUp')
    await expectValues(['100', '40', '28.9'])
    await page.keyboard.press('Home')
    await expectValues(['100', '40', '0.1'])
    await page.keyboard.press('End')
    await expectValues(['100', '40', '102400'])
    await resetAllocations()

    await startMouseDrag(playbackHandle, 10)
    await expectApproxAllocations(50, 30)
    await page.keyboard.press('Escape')
    await page.mouse.up()
    await expectValues(['100', '40', '30'])
    await startMouseDrag(playbackHandle, 10)
    await expectApproxAllocations(50, 30)
    // Change the total while the handle retains focus so this exercises the
    // scale watcher rather than relying on the separate blur cancellation.
    await warning.evaluate(input => { input.value = '120'; input.dispatchEvent(new Event('input', { bubbles: true })) })
    await expectValues(['120', '40', '30'])
    await page.mouse.move(0, 0)
    await page.mouse.up()
    await expectValues(['120', '40', '30'])
    await warning.fill('100')
    for (const invalidTotal of ['', '0']) {
      await startMouseDrag(playbackHandle, 10)
      await expectApproxAllocations(50, 30)
      await warning.evaluate((input, value) => { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })) }, invalidTotal)
      await expectValues([invalidTotal, '40', '30'])
      await page.mouse.move(0, 0)
      await page.mouse.up()
      await expectValues([invalidTotal, '40', '30'])
      assert.equal(await save.isDisabled(), true, 'invalidating the total during a gesture rolls back allocations and keeps the invalid input')
      await warning.fill('100')
      await waitHandleEnabled(playbackHandle)
    }
    await startMouseDrag(previewHandle, 10)
    await expectApproxAllocations(40, 40)
    let release, started
    const promise = new Promise(resolve => { release = resolve })
    const received = new Promise(resolve => { started = resolve })
    usageReleases.push(release)
    usageGate = { promise, release, started }
    const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/data-usage')
    await page.evaluate(() => window.dispatchEvent(new Event('jzmedia:data-usage-changed')))
    await received
    await assertUnavailableHandles()
    await expectValues(['100', '40', '30'])
    await page.mouse.move(0, 0)
    await page.mouse.up()
    await expectValues(['100', '40', '30'])
    usageGate = null
    release()
    await response
    await waitHandleEnabled(previewHandle)

    for (const [t, p] of [['0.1', '0.1'], ['93.8', '0.1']]) {
      await transcode.fill(t)
      await previews.fill(p)
      await page.setViewportSize({ width: 320, height: 844 })
      await allocationBar.scrollIntoViewIfNeeded()
      await handleGeometry()
      await checkSegmentLabels()
      await (t === '0.1' ? playbackHandle : previewHandle).focus()
      await checkTooltip(/0\.1\s*GB/, /剩余/)
      const narrow = allocationBar.locator('[data-budget-part="previews"] .budget-segment-label')
      assert.equal(await narrow.isVisible(), false, 'a 0.1 GB segment omits its inner label on a narrow screen')
    }
    await resetAllocations()
    await page.setViewportSize({ width: 390, height: 844 })
    // CDP sends trusted Chromium touch input, covering real touch pointer capture
    // and touchCancel -> pointercancel rather than synthetic DOM-only events.
    const touch = await context.newCDPSession(page)
    try {
      await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })
      for (const [handle, expected, cancel] of [[playbackHandle, [50, 30], false], [previewHandle, [40, 40], false], [previewHandle, [40, 40], true]]) {
        await resetAllocations()
        await handle.scrollIntoViewIfNeeded()
        const box = await handle.boundingBox(), track = await allocationBar.locator('.budget-bar-track').boundingBox()
        const point = { x: box.x + box.width / 2, y: box.y + box.height / 2, id: 1, radiusX: 2, radiusY: 2, force: 1 }
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] })
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ ...point, x: point.x + track.width * 0.1 }] })
        await expectApproxAllocations(...expected)
        await checkTooltip(handle === playbackHandle ? /50(?:\.0)?\s*GB/ : /40(?:\.0)?\s*GB/, /剩余/)
        if (handle === playbackHandle && !cancel)
          await page.screenshot({ path: path.join(directory, 'capacity-drag-tooltip-390.png'), animations: 'disabled' })
        await touch.send('Input.dispatchTouchEvent', { type: cancel ? 'touchCancel' : 'touchEnd', touchPoints: [] })
        if (cancel) await expectValues(['100', '40', '30'])
        else await expectApproxAllocations(...expected)
      }
    } finally {
      await touch.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] }).catch(() => {})
      await touch.send('Emulation.setTouchEmulationEnabled', { enabled: false }).catch(() => {})
      await touch.detach()
    }
    await resetAllocations()
    await page.setViewportSize({ width: 1440, height: 1000 })
    assert.equal(writes.length, writesBefore, 'mouse, keyboard and touch allocations remain unsaved until explicitly saved')
  }
  try {
    await page.goto(base + '/settings?sec=sec-index')
    await storage.getByRole('heading', { name: '应用数据与缓存', exact: true }).waitFor()
    assert.equal(await storage.count(), 1, 'usage, budgets and cleanup share one panel')
    await panel.getByText(/模拟容量设置加载失败/).waitFor()
    await storage.getByText(/模拟完整占用统计失败/).waitFor()
    failLoad = false
    await panel.getByRole('button', { name: '重新加载', exact: true }).click()
    await warning.waitFor()
    await expectValues(['20', '10', '10'])
    assert.equal(await save.isDisabled(), true, 'settings retry and usage retry have independent states')
    failUsage = false
    await refreshUsage()
    await storage.locator('.storage-total').getByText('25.00G', { exact: false }).waitFor()
    await panel.locator('.budget-error').getByText(/超出预算/).waitFor()
    assert.equal(await save.isDisabled(), true, 'legacy over-allocation is preserved and requires an explicit edit')
    await waitHandleEnabled(playbackHandle)
    await waitHandleEnabled(previewHandle)
    const originalCategories = categories
    categories = { ...categories, database: Math.floor(0.1 * gib), posters: Math.floor(0.2 * gib), other: 0 }
    await refreshUsage()
    assert.equal(reserve(), 1.3, 'a 20 GB budget can expose the 21.3 GB initial-allocation case')
    for (const [handle, untouched] of [[playbackHandle, previews], [previewHandle, transcode]]) {
      await handle.focus()
      await page.keyboard.press('Shift+ArrowLeft')
      for (let step = 0; step < 3; step++) await page.keyboard.press('ArrowLeft')
      assert.equal(await untouched.inputValue(), '10', 'reducing either quota from a 21.3 GB allocation leaves the other unchanged')
      await panel.locator('.budget-error').waitFor({ state: 'hidden' })
      assert.equal(await save.isEnabled(), true, 'either handle can repair the 20/21.3 GB allocation without first increasing the total')
      await transcode.fill('10')
      await previews.fill('10')
    }
    categories = originalCategories
    await refreshUsage()
    for (const [handle, untouched] of [[playbackHandle, previews], [previewHandle, transcode]]) {
      await handle.focus()
      await page.keyboard.press('Home')
      assert.equal(await untouched.inputValue(), '10', 'correcting an old over-allocation leaves the other quota unchanged')
      await panel.locator('.budget-error').waitFor({ state: 'hidden' })
      assert.equal(await save.isEnabled(), true, 'either handle can directly correct an old over-allocation')
      await transcode.fill('10')
      await previews.fill('10')
    }
    await panel.getByRole('slider', { name: '应用数据总预算', exact: true }).focus()
    await page.keyboard.press('ArrowRight')
    await expectValues(['20.1', '10', '10'])
    assert.equal(writes.length, 0, 'adjusting a slider does not save early')
    await navigate('概览')
    const dialog = page.getByTestId('settings-leave-dialog')
    await dialog.getByText('应用数据与缓存', { exact: true }).waitFor()
    await dialog.getByRole('button', { name: '继续编辑此项', exact: true }).click()
    assert.equal(await warning.evaluate(input => input === document.activeElement), true)
    await warning.fill('100')
    await transcode.fill('40')
    await previews.fill('30')
    await panel.getByLabel('预算分配', { exact: true }).getByText('未保存', { exact: true }).waitFor()
    await panel.locator('.budget-base').getByText(/自动预留/).waitFor()
    const segments = await panel.locator('.budget-bar [data-budget-part]').evaluateAll(elements => elements.map(element => ({
      part: element.dataset.budgetPart, width: element.getBoundingClientRect().width,
      barWidth: element.parentElement.getBoundingClientRect().width, color: getComputedStyle(element).backgroundColor,
    })))
    assert.deepEqual(segments.map(segment => segment.part), ['base', 'transcode', 'previews', 'free'])
    for (const [index, percent] of [6, 40, 30, 24].entries())
      assert.ok(Math.abs(segments[index].width / segments[index].barWidth * 100 - percent) < 0.5, 'allocation segments reflect 6/40/30/24 GB of the 100 GB budget')
    assert.equal(new Set(segments.map(segment => segment.color)).size, 4, 'each allocation has a distinct visible color')
    assert.ok(segments.every(segment => !['transparent', 'rgba(0, 0, 0, 0)'].includes(segment.color)))
    await panel.locator('.budget-legend').getByText(/未分配/).waitFor()
    await checkQuotaEditor()
    await checkDirectAllocation()

    await warning.fill('76')
    assert.equal(await save.isEnabled(), true, 'an exactly allocated total is valid')
    await warning.fill('75.9')
    await panel.locator('.budget-error').getByText(/超出预算/).waitFor()
    assert.equal(await save.isDisabled(), true)
    await expectValues(['75.9', '40', '30'])
    await waitHandleEnabled(playbackHandle)
    await waitHandleEnabled(previewHandle)
    await warning.fill('100')
    categories = { ...categories, posters: 39 * gib }
    await refreshUsage()
    await panel.locator('.budget-error').getByText(/超出预算/).waitFor()
    assert.equal(await save.isDisabled(), true, 'base-data growth is included when validating an edited budget')
    await expectValues(['100', '40', '30'])
    categories = { ...categories, posters: 4 * gib }
    await refreshUsage()
    await storage.locator('.storage-total').getByText('25.00G', { exact: false }).waitFor()
    assert.equal(await save.isEnabled(), true, 'refreshing usage preserves a valid unsaved draft')

    failSave = true
    await save.click()
    await panel.getByText(/模拟容量设置保存失败/).waitFor()
    await expectValues(['100', '40', '30'])
    failSave = false
    failValidation = true
    await save.click()
    await panel.getByText(/模拟保存时完整统计失败/).waitFor()
    await expectValues(['100', '40', '30'])
    assert.deepEqual(limits, { data_warning_gb: 20, transcode_cache_gb: 10, preview_cache_gb: 10 }, 'failed complete statistics do not persist a budget')
    failValidation = false
    const started = new Promise(resolve => { saveStarted = resolve })
    await save.click()
    await started
    assert.equal(await warning.isDisabled(), true)
    await assertUnavailableHandles()
    await navigate('概览')
    await dialog.getByRole('heading', { name: '设置操作尚未完成' }).waitFor()
    assert.equal(await dialog.getByRole('button', { name: '放弃修改并离开' }).count(), 0)
    releaseSave()
    saveStarted = null
    await panel.getByText('容量设置已保存并生效', { exact: true }).waitFor()
    await dialog.waitFor({ state: 'hidden' })
    await storage.locator('.storage-total').getByText('25.00G', { exact: false }).waitFor()
    assert.doesNotMatch(await storage.locator('.storage-total').textContent(), /100\.00G/, 'actual usage remains distinct from the planning budget')
    assert.equal(await page.locator('.app-storage-alert').count(), 0, 'raising the total budget clears the global alert immediately')
    await navigate('概览')
    assert.match(await page.locator('.overview-storage summary').textContent(), /100\.00G/)
    await navigate('系统维护')
    await page.reload()
    await expectValues(['100', '40', '30'])
    await checkQuotaLeave()
    await warning.fill('0')
    assert.equal(await save.isDisabled(), true)
    await assertUnavailableHandles()
    await warning.fill('2')
    await panel.locator('.budget-error').getByText(/超出预算/).waitFor()
    assert.equal(await save.isDisabled(), true)
    await expectValues(['2', '40', '30'])
    await warning.fill('110')
    failUsage = true
    await refreshUsage()
    await storage.getByText(/模拟完整占用统计失败/).waitFor()
    assert.equal(await save.isDisabled(), true, 'failed usage refresh blocks saving against stale base-data measurements')
    await assertUnavailableHandles()
    await expectValues(['110', '40', '30'])
    failUsage = false
    await refreshUsage()
    await storage.locator('.storage-total').getByText('25.00G', { exact: false }).waitFor()
    assert.equal(await save.isEnabled(), true)
    await panel.getByRole('button', { name: '放弃修改', exact: true }).click()
    await expectValues(['100', '40', '30'])

    categories = { ...categories, transcode: 101 * gib }
    await notifyUsage()
    await page.locator('.app-storage-alert').getByText(/100.00G/).waitFor()
    categories = { ...categories, transcode: 10 * gib }
    await notifyUsage()
    await page.locator('.app-storage-alert').waitFor({ state: 'hidden' })
    limits = { data_warning_gb: 100, transcode_cache_gb: 0, preview_cache_gb: 30 }
    await page.reload()
    await expectValues(['100', '0', '30'])
    await panel.locator('.budget-error').getByText(/未限制容量/).waitFor()
    await warning.fill('110')
    assert.equal(await save.isDisabled(), true, 'legacy zero keeps its meaning until the user chooses a positive playback quota')
    await assertUnavailableHandles()
    await warning.fill('100')
    await transcode.fill('40')
    await save.click()
    await panel.getByText('容量设置已保存并生效', { exact: true }).waitFor()

    await warning.fill('8')
    await transcode.fill('1')
    await previews.fill('1')
    assert.equal(await save.isEnabled(), true, 'reclaimable current cache usage can exceed a valid new allocation')
    await save.click()
    await panel.getByText('容量设置已保存并生效', { exact: true }).waitFor()
    assert.deepEqual(limits, { data_warning_gb: 8, transcode_cache_gb: 1, preview_cache_gb: 1 }, 'an exact fit is persisted despite current usage exceeding both cache quotas')
    await warning.fill('100')
    await transcode.fill('40')
    await previews.fill('30')
    await save.click()
    await panel.getByText('容量设置已保存并生效', { exact: true }).waitFor()

    for (const [cache, confirmation, remaining] of [['transcode', '确认清理缓存', '23.00G'], ['previews', '确认清理缩略图', '20.00G']]) {
      const row = panel.locator(`.cache-budget-row[data-cache="${cache}"]`)
      const check = row.getByRole('button', { name: '检查可清理空间', exact: true })
      await check.click()
      await row.getByRole('button', { name: confirmation, exact: true }).waitFor()
      assert.equal(cleanCalls.filter(call => call.cache === cache && !call.dry_run).length, 0, 'checking cleanup only previews reclaimable content')
      await row.getByRole('button', { name: '取消', exact: true }).click()
      await row.getByRole('button', { name: confirmation, exact: true }).waitFor({ state: 'hidden' })
      assert.equal(await check.evaluate(button => button === document.activeElement), true, 'canceling cleanup restores focus to its check button')
      await check.click()
      const readsBefore = usageReads
      await row.getByRole('button', { name: confirmation, exact: true }).click()
      await storage.locator('.storage-total').getByText(remaining, { exact: false }).waitFor()
      assert.ok(usageReads > readsBefore, 'confirmed cleanup refreshes actual usage')
      assert.equal(await check.evaluate(button => button === document.activeElement), true, 'confirmed cleanup restores focus to its check button')
      assert.deepEqual(cleanCalls.filter(call => call.cache === cache).map(call => call.dry_run), [true, true, false])
      await expectValues(['100', '40', '30'])
    }
    categories = { database: gib, posters: 4 * gib, previews: 10 * gib, transcode: 10 * gib, other: 0 }
    await notifyUsage()
    await storage.locator('.storage-total').getByText('25.00G', { exact: false }).waitFor()
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
      await panel.scrollIntoViewIfNeeded()
      await warning.focus()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'long library names and capacity controls fit the viewport')
      const controls = await panel.locator('input, button').evaluateAll(elements => elements.filter(element => element.getClientRects().length).map(element => {
        const rect = element.getBoundingClientRect()
        return { width: rect.width, height: rect.height }
      }))
      if (width < 700) assert.ok(controls.every(rect => rect.width >= 44 && rect.height >= 44), 'phone capacity controls have 44px targets')
      // A full-page screenshot must start at scroll zero: fixed off-screen skip links
      // otherwise appear inside the extended capture despite being outside the viewport.
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.screenshot({ path: path.join(directory, `capacity-${width}.png`), animations: 'disabled', fullPage: true })
    }
    await page.setViewportSize({ width: 1440, height: 1000 })
    await warning.fill('200')
    await navigate('概览')
    await dialog.getByRole('button', { name: '放弃修改并离开', exact: true }).click()
    await page.locator('.settings-heading h2').getByText('概览', { exact: true }).waitFor()
    await navigate('系统维护')
    await expectValues(['100', '40', '30'])
    assert.deepEqual(writes.at(-1), { data_warning_gb: 100, transcode_cache_gb: 40, preview_cache_gb: 30 })
  } catch (error) {
    await page.screenshot({ path: path.join(directory, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally {
    releaseSave?.()
    for (const release of usageReleases) release()
    await context.close()
  }
}

async function checkSettingsDrafts(browser, base) {
  // This context owns its settings and libraries. Failed/delayed writes must not
  // leak into the existing file-management and playback lifecycle fixtures.
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  const localLibs = structuredClone(libs)
  const settings = { tmdb_configured: false, tmdb_language: 'zh-CN', tmdb_proxy: '', tmdb_image_base: '', jzmedia_token_source: 'unset' }
  const ai = { enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'demo', daily_limit: 100, timeout_seconds: 12,
    api_key_set: false, usage: { requests: 0, input_tokens: 0, output_tokens: 0 } }
  const media = [
    { id: 1, name: '演示媒体库', source: 'local', path: '/demo/media', enabled: true, read_only: false, movie_count: 0, episode_count: 0 },
    { id: 2, name: '演示媒体库乙', source: 'smb', path: '/demo/remote', smb_host: 'demo.invalid', smb_share: 'videos', smb_username: 'demo', smb_subpath: '', smb_connect_host: '',
      enabled: true, read_only: false, movie_count: 0, episode_count: 0 },
  ]
  const writes = [], checks = [], releases = []
  let failSettings = false, settingsGate = null
  const gate = () => {
    let release, started
    const promise = new Promise(resolve => { release = resolve })
    const received = new Promise(resolve => { started = resolve })
    releases.push(release)
    return { promise, received, release, started }
  }
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), key = url.pathname, method = request.method()
    if (url.origin !== base) { errors.push('Unexpected settings-draft external request: ' + url.origin); return route.abort() }
    if (key === '/api/settings') {
      if (method === 'PUT') {
        const body = request.postDataJSON()
        writes.push({ key, body })
        if (settingsGate) { const current = settingsGate; current.started(); await current.promise }
        if (failSettings) return route.fulfill({ status: 503, json: { detail: '模拟设置保存失败' } })
        Object.assign(settings, body)
        if (body.tmdb_read_token) { settings.tmdb_configured = true; settings.tmdb_read_token_masked = 'demo…token'; delete settings.tmdb_read_token }
        if (body.jzmedia_token) { settings.jzmedia_token_source = 'db'; settings.jzmedia_token_masked = 'demo…token'; delete settings.jzmedia_token }
      }
      return route.fulfill({ json: settings })
    }
    if (key === '/api/ai/settings') {
      if (method === 'PATCH') { const body = request.postDataJSON(); writes.push({ key, body }); Object.assign(ai, body) }
      return route.fulfill({ json: ai })
    }
    if (key === '/api/libraries') return route.fulfill({ json: { items: localLibs, default_id: 1 } })
    if (/^\/api\/libraries\/\d+$/.test(key) && method === 'PATCH') {
      const body = request.postDataJSON(), library = localLibs.find(l => l.id === Number(key.split('/').at(-1)))
      writes.push({ key, body }); Object.assign(library, body)
      return route.fulfill({ json: library })
    }
    if (key === '/api/media-libraries') return route.fulfill({ json: { smb_driver: 'direct', items: media.map(m => ({ ...m, video_libraries: localLibs.filter(l => l.media_library_id === m.id) })) } })
    if (key === '/api/collections' || key === '/api/collections/suggest') return route.fulfill({ json: { items: [] } })
    if (key === '/api/collections/suggest/backfill/status') return route.fulfill({ json: { state: 'idle' } })
    return route.continue()
  })
  const page = await context.newPage()
  page.setDefaultTimeout(12000)
  page.on('pageerror', error => errors.push(String(error)))
  const unsaved = page.getByRole('dialog', { name: '设置尚未保存', exact: true })
  const busy = page.getByRole('dialog', { name: '设置操作尚未完成', exact: true })
  const token = page.getByLabel('读取令牌（Read Token）', { exact: true })
  const nav = label => page.locator('.desktop-categories').getByRole('button', { name: label, exact: true })
  const section = () => new URL(page.url()).searchParams.get('sec')
  const unloadBlocked = () => page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    return event.defaultPrevented
  })
  const stay = async () => { await unsaved.getByRole('button', { name: '继续编辑', exact: true }).click(); await unsaved.waitFor({ state: 'hidden' }) }
  const discard = async () => { await unsaved.getByRole('button', { name: '放弃修改并离开', exact: true }).click(); await unsaved.waitFor({ state: 'hidden' }) }
  const go = async (label, sec) => { await nav(label).click(); await page.waitForURL(url => url.searchParams.get('sec') === sec) }
  const collectionsLink = () => page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '合集', exact: true })
  try {
    await page.goto(base + '/settings?sec=sec-tmdb')
    await token.waitFor()
    assert.equal(await unloadBlocked(), false, 'loaded settings are initially clean')
    await token.fill('synthetic-unsaved-token')
    assert.equal(await unloadBlocked(), true, 'editing credentials protects browser refresh')
    await nav('智能辅助').click()
    await unsaved.waitFor()
    await page.waitForFunction(() => document.activeElement?.textContent?.trim() === '继续编辑')
    assert.equal(section(), 'sec-tmdb', 'a proposed section change leaves the source route intact')
    await screenshot(page, 'settings-unsaved', 'user-guide/settings.md', '未保存的 TMDB 草稿触发离开确认；默认继续编辑，可明确放弃修改后离开')
    await stay()
    assert.equal(await token.inputValue(), 'synthetic-unsaved-token')
    await nav('智能辅助').click()
    await unsaved.waitFor()
    await page.keyboard.press('Escape')
    await unsaved.waitFor({ state: 'hidden' })
    assert.equal(await nav('智能辅助').evaluate(el => el === document.activeElement), true, 'Escape restores the navigation trigger')
    assert.equal(section(), 'sec-tmdb')
    checks.push('section change: stay, Escape and trigger focus preserve draft')

    // Exercise Chromium's actual refresh dialog in addition to the cancelable
    // event used below to check whether the beforeunload listener was removed.
    const nativeDialog = page.waitForEvent('dialog')
    const reloading = page.reload({ waitUntil: 'domcontentloaded' }).catch(error => error)
    const refresh = await nativeDialog
    assert.equal(refresh.type(), 'beforeunload')
    await refresh.dismiss()
    await reloading
    assert.equal(await token.inputValue(), 'synthetic-unsaved-token', 'dismissed refresh keeps the live input')
    checks.push('native refresh cancellation preserves draft')

    await collectionsLink().click()
    await unsaved.waitFor()
    await stay()
    assert.equal(new URL(page.url()).pathname, '/settings')
    await collectionsLink().click()
    await unsaved.waitFor()
    await discard()
    await page.waitForURL(url => url.pathname === '/collections')
    await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '设置', exact: true }).click()
    await go('在线资料服务', 'sec-tmdb')
    assert.equal(await token.inputValue(), '', 'accepted route leave discards the credentials draft')
    checks.push('route leave: stay and explicit discard')

    await token.fill('synthetic-retry-token')
    failSettings = true
    settingsGate = gate()
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await settingsGate.received
    await nav('智能辅助').click()
    await busy.waitFor()
    settingsGate.release(); settingsGate = null
    await page.getByText('操作失败：503 模拟设置保存失败', { exact: true }).waitFor()
    await unsaved.waitFor()
    assert.equal(section(), 'sec-tmdb', 'a failed save keeps the pending leave blocked')
    assert.equal(await token.inputValue(), 'synthetic-retry-token')
    assert.equal(await unloadBlocked(), true, 'failed save must not clear dirty state')
    await stay()
    failSettings = false
    settingsGate = gate()
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await settingsGate.received
    await nav('智能辅助').click()
    await busy.waitFor()
    assert.equal(await busy.getByRole('button', { name: '放弃修改并离开', exact: true }).count(), 0)
    assert.equal(await unloadBlocked(), true)
    assert.equal(section(), 'sec-tmdb')
    await busy.getByRole('button', { name: '留在设置', exact: true }).click()
    await busy.waitFor({ state: 'hidden' })
    await collectionsLink().click()
    await busy.waitFor()
    assert.equal(new URL(page.url()).pathname, '/settings', 'route links cannot bypass an in-flight save')
    settingsGate.release(); settingsGate = null
    await page.getByText('配置已保存', { exact: true }).waitFor()
    await page.getByTestId('settings-leave-dialog').waitFor({ state: 'hidden' })
    assert.equal(new URL(page.url()).pathname, '/settings', 'completing a save cancels the pending leave')
    assert.equal(section(), 'sec-tmdb')
    assert.equal(await token.inputValue(), '')
    assert.equal(await unloadBlocked(), false, 'successful save unlocks navigation and refresh')
    await go('智能辅助', 'sec-ai')
    checks.push('save failure preserves draft and updates the open dialog; in-flight save blocks both navigation paths; success closes the dialog and unlocks a new navigation')

    await page.getByRole('button', { name: '配置与测试服务', exact: true }).click()
    await page.getByLabel('模型名称', { exact: true }).fill('isolated-draft-model')
    await nav('离线资料').click()
    await unsaved.waitFor()
    await stay()
    assert.equal(await page.getByLabel('模型名称', { exact: true }).inputValue(), 'isolated-draft-model')
    await page.getByRole('button', { name: '保存智能辅助配置', exact: true }).click()
    await page.getByText('配置已保存，智能辅助已关闭', { exact: true }).waitFor()
    assert.equal(ai.model, 'isolated-draft-model')
    await go('匹配规则', 'sec-matching')
    checks.push('AI draft is guarded; successful save unlocks')

    await page.locator('#source-library').selectOption('1')
    await page.getByLabel('NFO 导入', { exact: true }).check()
    await page.locator('#source-library').selectOption('2')
    await page.getByLabel('NFO 导入', { exact: true }).check()
    await page.locator('#source-library').selectOption('1')
    assert.equal(await page.getByLabel('NFO 导入', { exact: true }).isChecked(), true)
    await page.getByRole('button', { name: '保存匹配规则', exact: true }).click()
    await page.getByText(/^已保存：/).waitFor()
    await nav('在线资料服务').click()
    await unsaved.waitFor()
    assert.match(await unsaved.innerText(), /剧集/, 'the unsaved second library is named in the leave dialog')
    await stay()
    await page.locator('#source-library').selectOption('2')
    assert.equal(await page.getByLabel('NFO 导入', { exact: true }).isChecked(), true)
    await nav('在线资料服务').click()
    await unsaved.waitFor()
    await discard()
    await page.waitForURL(/sec=sec-tmdb/)
    await go('匹配规则', 'sec-matching')
    await page.locator('#source-library').selectOption('2')
    assert.equal(await page.getByLabel('NFO 导入', { exact: true }).isChecked(), false, 'discard resets the unsaved library')
    await page.locator('#source-library').selectOption('1')
    assert.equal(await page.getByLabel('NFO 导入', { exact: true }).isChecked(), true, 'discard keeps the separately saved library')
    assert.equal(await unloadBlocked(), false)
    checks.push('multiple library drafts: switching preserves both; saving one does not hide the other; discard only removes unsaved values')

    await go('访问保护', 'sec-auth')
    await page.locator('#access-token').fill('isolated-access-token')
    await nav('概览').click()
    await unsaved.waitFor()
    await stay()
    await page.getByRole('button', { name: '保存并启用保护', exact: true }).click()
    await page.getByText('已启用保护，当前浏览器已记住令牌', { exact: true }).waitFor()
    await go('媒体库连接', 'sec-libraries')
    checks.push('access-token draft is guarded and save clears it')

    await page.getByRole('button', { name: '添加媒体库', exact: true }).click()
    const create = page.locator('.create-library-form')
    await create.getByRole('textbox', { name: '名称', exact: true }).fill('未保存的演示媒体库')
    await create.getByRole('textbox', { name: '根路径', exact: true }).fill('/demo/unsaved')
    await nav('概览').click()
    await unsaved.waitFor()
    await stay()
    assert.equal(await create.getByRole('textbox', { name: '名称', exact: true }).inputValue(), '未保存的演示媒体库')
    await nav('概览').click()
    await unsaved.waitFor()
    await discard()
    await page.waitForURL(/sec=sec-status/)
    await go('媒体库连接', 'sec-libraries')
    if (!await create.isVisible()) await page.getByRole('button', { name: '添加媒体库', exact: true }).click()
    assert.equal(await create.getByRole('textbox', { name: '名称', exact: true }).inputValue(), '')
    await page.getByRole('button', { name: '收起新建表单', exact: true }).click()
    const firstMedia = page.locator('.media-connection').filter({ hasText: '演示媒体库' }).first()
    const firstTitle = firstMedia.locator('.connection-title')
    if (await firstTitle.getAttribute('aria-expanded') !== 'true') await firstTitle.click()
    await firstMedia.locator('.video-library-row').first().getByRole('button', { name: '编辑', exact: true }).click()
    const edit = firstMedia.locator('.video-form')
    await edit.getByRole('textbox', { name: '视频库名称', exact: true }).fill('未保存的视频库名称')
    await nav('概览').click()
    await unsaved.waitFor()
    await stay()
    assert.equal(await edit.getByRole('textbox', { name: '视频库名称', exact: true }).inputValue(), '未保存的视频库名称')
    await edit.getByRole('button', { name: '保存视频库', exact: true }).click()
    await edit.waitFor({ state: 'hidden' })
    await go('概览', 'sec-status')
    assert.equal(localLibs[0].name, '未保存的视频库名称')
    await go('媒体库连接', 'sec-libraries')
    const remoteMedia = page.locator('.media-connection').filter({ hasText: '演示媒体库乙' })
    await remoteMedia.locator('summary').filter({ hasText: '更多' }).click()
    await remoteMedia.getByRole('button', { name: '编辑连接', exact: true }).click()
    await remoteMedia.getByLabel('SMB 用户名', { exact: true }).fill('unsaved-demo-user')
    await nav('概览').click()
    await unsaved.waitFor()
    await discard()
    await page.waitForURL(/sec=sec-status/)
    await go('媒体库连接', 'sec-libraries')
    await remoteMedia.locator('summary').filter({ hasText: '更多' }).click()
    await remoteMedia.getByRole('button', { name: '编辑连接', exact: true }).click()
    assert.equal(await remoteMedia.getByLabel('SMB 用户名', { exact: true }).inputValue(), 'demo', 'discard restores saved connection values')
    await remoteMedia.locator('.path-edit').getByRole('button', { name: '取消', exact: true }).click()
    checks.push('media-library creation, video-library editing and connection editing are protected')

    await go('在线资料服务', 'sec-tmdb')
    await page.setViewportSize({ width: 390, height: 844 })
    await token.fill('isolated-mobile-draft')
    const category = page.getByLabel('设置分类', { exact: true })
    // selectOption dispatches change without focusing like a real interaction.
    await category.focus()
    await category.selectOption('sec-ai')
    await unsaved.waitFor()
    await page.waitForFunction(() => document.activeElement?.textContent?.trim() === '继续编辑')
    const bounds = await unsaved.evaluate(el => {
      const rect = el.getBoundingClientRect()
      return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, innerWidth, innerHeight,
        overflow: el.scrollWidth > el.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1 }
    })
    assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.right <= bounds.innerWidth + 1 && bounds.bottom <= bounds.innerHeight + 1)
    assert.equal(bounds.overflow, false)
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab')
      assert.equal(await unsaved.evaluate(el => el.contains(document.activeElement)), true, 'Tab remains inside the mobile leave dialog')
    }
    await page.keyboard.press('Escape')
    await unsaved.waitFor({ state: 'hidden' })
    assert.equal(await category.inputValue(), 'sec-tmdb', 'cancel restores the mobile category selector')
    assert.equal(await category.evaluate(el => el === document.activeElement), true)
    assert.equal(await token.inputValue(), 'isolated-mobile-draft')
    await category.selectOption('sec-ai')
    await unsaved.waitFor()
    await discard()
    await page.waitForURL(/sec=sec-ai/)
    assert.equal(await unloadBlocked(), false)
    checks.push('390px category cancellation, safe default focus, Tab trap, Escape restore and no overflow')
    const report = path.join(root, 'output/playwright/settings-drafts')
    await mkdir(report, { recursive: true })
    await writeFile(path.join(report, 'checks.json'), JSON.stringify({ mocked: true, realVue: true, checks, writes: writes.map(({ key }) => key) }, null, 2) + '\n')
    console.log('PASS settings drafts: ' + checks.join('; '))
  } catch (error) {
    const report = path.join(root, 'output/playwright/settings-drafts')
    await mkdir(report, { recursive: true })
    await page.screenshot({ path: path.join(report, 'failure.png'), fullPage: true }).catch(() => {})
    console.error('Settings draft checks completed before failure:', checks)
    throw error
  } finally {
    for (const release of releases) release()
    await context.close()
  }
}
async function checkLibraryTools(browser, base) {
  const directory = path.join(root, 'output/playwright/library-tools')
  await mkdir(directory, { recursive: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  const page = await context.newPage()
  page.setDefaultTimeout(12000)
  page.on('pageerror', error => errors.push(String(error)))
  const measurements = []
  const views = page.locator('.tool-views:visible')
  const files = page.getByRole('button', { name: '管理文件', exact: true })
  const readLayout = () => page.locator('.library-tools-header:visible').evaluate(header => {
    const rect = element => {
      const value = element.getBoundingClientRect()
      return { left: value.left, top: value.top, right: value.right, bottom: value.bottom, width: value.width, height: value.height }
    }
    const group = header.querySelector('.tool-views')
    return { header: rect(header), group: rect(group), groupOverflow: group.scrollWidth > group.clientWidth + 1,
      gap: parseFloat(getComputedStyle(header).columnGap), files: rect(header.querySelector('.open-library-files')),
      pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      buttons: [...group.querySelectorAll('button')].map(button => {
        const label = button.querySelector('.tool-view-label')
        return { name: button.getAttribute('aria-label'), pressed: button.getAttribute('aria-pressed'), rect: rect(button),
          labelVisible: label.checkVisibility(), label: rect(label), overflow: button.scrollWidth > button.clientWidth + 1 }
      }) }
  })
  try {
    await page.goto(base + '/settings?sec=sec-libtools&library=1&media=1')
    for (const width of [1440, 320, 375, 390]) {
      for (const fontSize of [16, 20]) {
        await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 })
        await page.evaluate(fontSize => localStorage.setItem('jz.prefs', JSON.stringify({ fontSize, posterMin: 150 })), fontSize)
        for (const library of [{ id: 1, kind: 'movie', labels: ['入库整理', '资料维护', '还原位置'] }, { id: 2, kind: 'tv', labels: ['入库整理', '资料维护'] }]) {
          await page.goto(base + '/settings?sec=sec-libtools&library=' + library.id + '&media=1')
          await views.waitFor()
          assert.equal(await views.getByRole('button').count(), library.labels.length)
          for (const [index, label] of library.labels.entries()) {
            const button = views.getByRole('button', { name: label, exact: true })
            await button.focus()
            assert.equal(await button.evaluate(element => element === document.activeElement), true, 'Every tool has a keyboard focus target')
            await button.press(index % 2 ? 'Space' : 'Enter')
            await page.waitForFunction(label => [...document.querySelectorAll('.tool-views button[aria-pressed="true"]')].some(button => button.checkVisibility() && button.getAttribute('aria-label') === label), label)
            const layout = await readLayout()
            const message = width + 'px font ' + fontSize + ' ' + library.kind + ' ' + label
            assert.equal(layout.pageOverflow || layout.groupOverflow, false, message + ': no page or tool-group horizontal scrolling')
            assert.equal(layout.buttons.filter(button => button.pressed === 'true').length, 1, message + ': one selected tool')
            assert.deepEqual(layout.buttons.map(button => button.name), library.labels, message + ': all tools retain full accessible names')
            for (const [buttonIndex, button] of layout.buttons.entries()) {
              assert.equal(button.labelVisible, width > 700 || button.name === label, message + ': only the selected mobile tool shows its label')
              assert.equal(button.overflow, false, message + ': button content is not clipped')
              assert.ok(button.rect.left >= layout.group.left - 1 && button.rect.right <= layout.group.right + 1, message + ': every tool stays inside its group')
              if (buttonIndex) assert.ok(button.rect.left >= layout.buttons[buttonIndex - 1].rect.right, message + ': adjacent tools do not overlap')
              if (button.labelVisible) assert.ok(button.label.left >= button.rect.left - 1 && button.label.right <= button.rect.right + 1, message + ': full label stays inside its button')
              if (width < 701) assert.ok(button.rect.width >= 44 && button.rect.height >= 44, message + ': tools keep 44px touch targets')
            }
            assert.ok(layout.files.left >= layout.header.left - 1 && layout.files.right <= layout.header.right + 1, message + ': file management stays inside the header')
            if (width < 701) {
              assert.ok(layout.files.width >= 44 && layout.files.height >= 44, message + ': file management keeps a 44px touch target')
              const fits = layout.group.width + layout.files.width + layout.gap <= layout.header.width + 1
              if (fits) {
                assert.ok(Math.abs(layout.group.top + layout.group.height / 2 - layout.files.top - layout.files.height / 2) < 1, message + ': file management shares the row when it fits')
                assert.ok(layout.files.left >= layout.group.right, message + ': file management cannot overlap the tools')
              }
              else {
                assert.ok(layout.files.top >= layout.group.bottom && Math.abs(layout.files.right - layout.header.right) < 1, message + ': file management wraps below to the right')
              }
            }
            measurements.push({ width, fontSize, kind: library.kind, selected: label, ...layout })
            if ((width === 1440 && fontSize === 16 || width === 375 && fontSize === 16 || width === 320 && fontSize === 20) && label === '入库整理') {
              await page.locator('.library-tools-header:visible').scrollIntoViewIfNeeded()
              await page.screenshot({ path: path.join(directory, library.kind + '-' + width + '-font-' + fontSize + '.png'), animations: 'disabled' })
            }
          }
          await views.getByRole('button', { name: library.labels[0], exact: true }).focus()
          await page.keyboard.press('Tab')
          assert.equal(await views.getByRole('button', { name: library.labels[1], exact: true }).evaluate(button => button === document.activeElement), true, 'Tab follows the visible tool order')
          if (width === 320 && fontSize === 20 || width === 375 && fontSize === 16) {
            for (const label of library.labels) {
              await views.getByRole('button', { name: label, exact: true }).click()
              const pending = page.locator('.pipe-toggle:visible').filter({ hasText: '核对匹配' })
              if (label === '入库整理' && await pending.getAttribute('aria-expanded') !== 'true') await pending.click()
              await files.click()
              await page.waitForURL(/sec=sec-files/)
              if (width === 320 && label === '资料维护') await page.reload()
              await page.getByRole('button', { name: '返回扫描与整理', exact: true }).click()
              await page.waitForURL(/sec=sec-libtools/)
              await page.waitForFunction(label => [...document.querySelectorAll('.tool-views button[aria-pressed="true"]')].some(button => button.checkVisibility() && button.getAttribute('aria-label') === label), label)
              assert.equal(new URL(page.url()).searchParams.get('library'), String(library.id), 'Returning from files preserves the video library')
              assert.equal(await views.getByRole('button', { name: label, exact: true }).getAttribute('aria-pressed'), 'true', 'Returning from files restores the selected tool')
              if (label === '入库整理') assert.equal(await pending.getAttribute('aria-expanded'), 'true', 'Returning from files restores the expanded workflow step')
            }
          }
        }
      }
    }
    await writeFile(path.join(directory, 'checks.json'), JSON.stringify({ mocked: true, realVue: true, measurements }, null, 2) + '\n')
    console.log('PASS library tools: desktop labels, compact mobile labels, full accessible names, keyboard activation, 44px targets, adaptive file action and file return at 320/375/390px and font sizes 16/20')
  } catch (error) {
    await page.screenshot({ path: path.join(directory, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally { await context.close() }
}
try {
  const ffmpegPath = await ffmpeg()
  execFileSync(ffmpegPath, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10', '-t', '40', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', path.join(work, 'video.mp4')])
  execFileSync(ffmpegPath, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=0x325b70:size=640x360', '-frames:v', '1', path.join(work, 'image.png')])
  execFileSync(ffmpegPath, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=0x325b70:size=640x360:rate=10', '-t', '40', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', path.join(work, 'ass-video.mp4')])
  videoFixture = await readFile(path.join(work, 'video.mp4'))
  imageFixture = await readFile(path.join(work, 'image.png'))
  assVideoFixture = await readFile(path.join(work, 'ass-video.mp4'))
  assFontFixture = await readFile(path.join(root, 'frontend/node_modules/jassub/dist/default.woff2'))
  const output = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(work, 'cache'), plugins: [vue(), {
    name: 'isolated-player-harness', resolveId: id => id === harnessId ? '\0' + harnessId : null,
    load: id => id === '\0' + harnessId ? harnessSource : null,
  }], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
    rollupOptions: { input: { app: path.join(root, 'frontend/index.html'), playerHarness: harnessId } } } })
  harnessEntry = output.output.find(item => item.type === 'chunk' && item.isEntry && item.name === 'playerHarness').fileName
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost')
      res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self' blob:; media-src 'self' blob:; img-src 'self' data: blob:; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'")
      if (url.pathname === '/player-harness.html') {
        const styles = (await readdir(path.join(dist, 'assets'))).filter(name => name.endsWith('.css'))
        res.writeHead(200, { 'Content-Type': 'text/html' })
        res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">' + styles.map(name => '<link rel="stylesheet" href="/assets/' + name + '">').join('') + '</head><body><div id="app"></div><script type="module" src="/' + harnessEntry + '"></script></body></html>'); return
      }
      if (url.pathname === '/fixtures/ass-font.woff2') {
        res.writeHead(200, { 'Content-Type': 'font/woff2' }); res.end(assFontFixture); return
      }
      if (url.pathname === '/api/stream/82/sub/0.ass' || url.pathname === '/api/stream/82/sub/0.vtt') {
        res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
        res.end(url.pathname.endsWith('.ass') ? assFixture : 'WEBVTT\n\n00:00:00.000 --> 00:00:40.000\nASS compatibility text'); return
      }
      if (url.pathname === '/fixtures/ass-video.mp4') {
        const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '')
        const start = range ? Number(range[1]) : 0
        const end = range?.[2] ? Math.min(Number(range[2]), assVideoFixture.length - 1) : assVideoFixture.length - 1
        res.writeHead(range ? 206 : 200, { 'Content-Type': 'video/mp4', 'Content-Length': end - start + 1, 'Accept-Ranges': 'bytes',
          ...(range ? { 'Content-Range': `bytes ${start}-${end}/${assVideoFixture.length}` } : {}) })
        res.end(assVideoFixture.subarray(start, end + 1)); return
      }
      if (url.pathname === '/api/fs/blob' || url.pathname === '/fixtures/video.mp4') {
        // Match the decoded filename just as the backend does. Double-encoded URLs must fail.
        if (url.pathname === '/fixtures/video.mp4' && url.searchParams.get('name') !== directFixturePath) {
          res.writeHead(404); res.end('invalid encoded media path'); return
        }
        const file = url.searchParams.get('path') || 'video.mp4'
        if (url.pathname.startsWith('/api/')) requests.push({ key: url.pathname, method: req.method, query: Object.fromEntries(url.searchParams) })
        if (file.includes('已消失') || file.includes('离线')) {
          res.writeHead(file.includes('已消失') ? 404 : 503, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: 'synthetic file unavailable' })); return
        }
        if (url.searchParams.get('mode') === 'text') {
          res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8', 'X-Preview-Truncated': 'false' })
          res.end(file.endsWith('.nfo') ? '<movie><title>纯文本演示：<script>不执行</script></title></movie>' : 'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n用于核对的合成字幕'); return
        }
        const data = /\.png$|poster.*\.jpg$/.test(file) ? imageFixture : file.endsWith('.mp4') ? videoFixture : file.endsWith('.pdf') ? syntheticPdf() : Buffer.from('not browser media')
        const type = /\.png$|poster.*\.jpg$/.test(file) ? 'image/png' : file.endsWith('.mp4') ? 'video/mp4' : file.endsWith('.pdf') ? 'application/pdf' : 'application/octet-stream'
        const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '')
        const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(Number(range[2]), data.length - 1) : data.length - 1
        if (start >= data.length) { res.writeHead(416, { 'Content-Range': 'bytes */' + data.length }); res.end(); return }
        res.writeHead(range ? 206 : 200, { 'Content-Type': type, 'Content-Length': end - start + 1, 'Accept-Ranges': 'bytes',
          ...(range ? { 'Content-Range': 'bytes ' + start + '-' + end + '/' + data.length } : {}) })
        res.end(data.subarray(start, end + 1)); return
      }
      if (url.pathname.startsWith('/api/')) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString()
        const result = mock(url, req.method, raw ? JSON.parse(raw) : null)
        res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(result)); return
      }
      const file = /^\/assets\/[\w.-]+$/.test(url.pathname) || url.pathname === '/favicon.svg' ? path.join(dist, url.pathname) : path.join(dist, 'index.html')
      const type = { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml',
        '.wasm': 'application/wasm' }[path.extname(file)] || 'application/octet-stream'
      const data = await readFile(file); res.writeHead(200, { 'Content-Type': type }); res.end(data)
    } catch (error) { errors.push(String(error)); res.writeHead(500); res.end('Fixture error') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  if (toolsOnly || !previewsOnly && !playerOnly && !draftsOnly && !cacheOnly) await checkLibraryTools(browser, base)
  if (!toolsOnly) {
  if (previewsOnly) await checkPreviewMaintenance(browser, base, { baseline: previewBaseline })
  if (!previewsOnly && !playerOnly && !draftsOnly) await checkCacheSettings(browser, base)
  if (!previewsOnly && !playerOnly && !cacheOnly) await checkSettingsDrafts(browser, base)
  if (!previewsOnly && !playerOnly && !draftsOnly && !cacheOnly) await checkFileLoading(browser, base)
  if (!previewsOnly && !draftsOnly && !cacheOnly) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  if (!playerOnly) {
  const page = await context.newPage()
  page.setDefaultTimeout(12000)
  page.on('pageerror', e => errors.push(String(e)))
  const navigate = async sec => { await page.goto(base + '/settings?sec=' + sec); await page.locator('.settings-heading h2').waitFor() }
  await navigate('sec-status')
  await screenshot(page, 'settings-navigation', 'user-guide/settings.md', '三组设置导航、独立文件管理及概览')
  const headingStyle = await page.locator('.side-nav .nav-group').first().evaluate(el => ({ size: parseFloat(getComputedStyle(el).fontSize), weight: Number(getComputedStyle(el).fontWeight) }))
  assert.ok(headingStyle.size >= 14 && headingStyle.weight >= 600)
  const groups = await page.locator('.side-nav .nav-group').evaluateAll(els => els.map(el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height } }))
  assert.ok(groups.every(g => g.width >= 150 && g.height < 35), 'sidebar groups must not wrap character by character')
  assert.ok(groups.every((g, i) => !i || (g.x === groups[i - 1].x && g.y > groups[i - 1].y)), 'sidebar groups must stack vertically')
  await navigate('sec-tmdb')
  assert.equal(await page.locator('.ai-settings:visible').count(), 0)
  await screenshot(page, 'settings-sources', 'user-guide/settings.md', '在线资料服务：TMDB连接与来源状态')
  await navigate('sec-matching')
  await page.locator('#source-library option').first().waitFor({ state: 'attached' })
  await page.getByLabel('NFO 导入', { exact: true }).check()
  await page.locator('#source-library').selectOption('2')
  await page.locator('#source-library').selectOption('1')
  assert.equal(await page.getByLabel('NFO 导入', { exact: true }).isChecked(), true)
  await page.getByRole('button', { name: '上移 TMDB', exact: true }).click()
  await page.getByRole('button', { name: '保存匹配规则', exact: true }).click()
  await page.getByText(/^已保存：/).waitFor()
  assert.deepEqual(JSON.parse(libs[0].metadata_providers), ['tmdb', 'local', 'nfo'])
  await page.getByLabel('电影名称', { exact: true }).fill('演示无结果')
  await page.getByRole('button', { name: '测试已保存规则', exact: true }).click()
  await page.getByText(/^未找到候选/).waitFor()
  assert.equal(requests.filter(r => r.key === '/api/metadata/test-search').at(-1).query.library, '1')
  await screenshot(page, 'settings-matching', 'user-guide/settings.md', '按视频库设置来源顺序、保留草稿并测试匹配')
  await navigate('sec-offline')
  await screenshot(page, 'settings-offline', 'user-guide/settings.md', '独立 IMDb 离线资料导入入口')
  // File management is an explicit navigation action, not a tab that leaves its page.
  await page.goto(base + '/settings?sec=sec-libtools&library=1&media=1')
  const views = page.locator('.tool-views:visible')
  await views.waitFor()
  assert.equal(await views.getByRole('button', { name: '文件管理', exact: true }).count(), 0)
  await screenshot(page, 'file-tools-entry', 'user-guide/settings.md', '独立“管理文件”入口；工具标签只切换页内内容')
  const openFiles = page.getByRole('button', { name: '管理文件', exact: true })
  const returnFiles = page.getByRole('button', { name: '返回扫描与整理', exact: true })
  for (const view of ['资料维护', '还原位置']) {
    await views.getByRole('button', { name: view, exact: true }).click()
    if (view === '资料维护') {
      const preview = page.getByRole('region', { name: '播放进度缩略图' })
      await preview.getByText('处理样本', { exact: true }).waitFor()
      await preview.getByText('队列样本 1', { exact: true }).waitFor()
      const hierarchy = await preview.evaluate(el => ({
        headings: [...el.querySelectorAll('.queue-heading h5')].map(h => parseFloat(getComputedStyle(h).fontSize)),
        item: parseFloat(getComputedStyle(el.querySelector('.queue-detail strong')).fontSize),
      }))
      assert.ok(hierarchy.headings.every(size => size > hierarchy.item), 'task headings are larger than video titles')
      await page.setViewportSize({ width: 390, height: 844 })
      const checkboxTarget = await preview.locator('.select-item').first().boundingBox()
      assert.ok(checkboxTarget.width >= 44 && checkboxTarget.height >= 44, 'queue checkboxes have phone touch targets')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.setViewportSize({ width: 1440, height: 1000 })
      await preview.getByRole('button', { name: '取消当前影片 处理样本', exact: true }).click()
      await preview.getByRole('button', { name: '取消当前影片 处理样本', exact: true }).waitFor({ state: 'hidden' })
      await preview.getByRole('checkbox', { name: '选择 队列样本 1', exact: true }).check()
      await preview.getByRole('button', { name: '下一页' }).click()
      await preview.getByRole('checkbox', { name: '选择 队列样本 52', exact: true }).check()
      await preview.getByText('已选 2 项', { exact: true }).waitFor()
      await preview.getByRole('button', { name: '批量移出选中项' }).click()
      await preview.getByText('已移出 2 项待处理影片。').waitFor()
      await preview.getByRole('checkbox', { name: '全选本库待处理项' }).check()
      await preview.getByText('已选 50 项', { exact: true }).waitFor()
      await preview.getByRole('checkbox', { name: '选择 队列样本 2', exact: true }).uncheck()
      await preview.getByText('已选 49 项', { exact: true }).waitFor()
      await preview.getByRole('button', { name: '批量移出选中项' }).click()
      await preview.getByText('已移出 49 项待处理影片。').waitFor()
      await preview.getByRole('button', { name: '从待处理队列移除 队列样本 2' }).click()
      await preview.getByText('当前视频库没有待处理影片。').waitFor()
      await preview.getByRole('button', { name: '将已有影片加入闲时队列' }).click()
      await preview.getByText('已有影片样本', { exact: true }).waitFor()
      assert.ok(requests.some(r => r.key === '/api/stream/previews/queue' && r.method === 'POST'))
    }
    await openFiles.click()
    await page.waitForURL(/sec=sec-files/)
    if (view === '资料维护') {
      const originUrl = page.url()
      await page.reload()
      await returnFiles.waitFor()
      assert.equal(page.url(), originUrl, 'refresh keeps the explicit return origin')
    }
    await returnFiles.click()
    await page.waitForURL(/sec=sec-libtools/)
    await page.waitForFunction(label => [...document.querySelectorAll('.tool-views button.on')].some(el => el.textContent === label && el.offsetParent), view)
    assert.equal(new URL(page.url()).searchParams.get('library'), '1')
  }
  await views.getByRole('button', { name: '入库整理', exact: true }).click()
  await page.locator('.pipe-toggle:visible').filter({ hasText: '核对匹配' }).click()
  await openFiles.click()
  await returnFiles.click()
  await page.waitForURL(/sec=sec-libtools/)
  assert.equal(await page.locator('.pipe-toggle:visible').filter({ hasText: '核对匹配' }).getAttribute('aria-expanded'), 'true')
  // Direct sidebar navigation must not inherit a stale return source.
  await page.locator('.side-nav').getByRole('button', { name: '文件管理', exact: true }).click()
  await page.waitForURL(/sec=sec-files/)
  assert.equal(await returnFiles.count(), 0)
  await page.goto(base + '/settings?sec=sec-files&library=1')
  const table = page.getByRole('table', { name: '当前目录文件' })
  await table.locator('tbody tr').nth(3).waitFor()
  assert.equal(await table.locator('tbody tr').count(), 5, 'pagination includes last file')
  assert.equal(await page.locator('.fs-tree').evaluate(el => getComputedStyle(el).display), 'block')
  const progressBeforeFiles = requests.filter(r => r.key === '/api/stream/progress').length
  const currentLocation = page.url()
  const movieRow = table.locator('tr').filter({ hasText: '模拟电影 (2020).mkv' })
  const posterRow = table.locator('tr').filter({ hasText: 'poster.jpg' })
  const fileToolbar = page.getByRole('toolbar', { name: '文件操作', exact: true })
  const allFiles = table.getByRole('checkbox', { name: '全选当前目录', exact: true })
  const checkedFiles = () => table.locator('tbody input[type="checkbox"]:checked').evaluateAll(inputs => inputs.map(input => input.closest('tr').dataset.fsRel).sort())
  const expectChecked = async expected => {
    const sorted = [...expected].sort()
    assert.deepEqual(await checkedFiles(), sorted, 'native checkbox state matches expected checks')
    const selected = await table.locator('tbody tr[aria-selected="true"]').evaluateAll(rows => rows.map(row => row.dataset.fsRel).sort())
    assert.deepEqual(selected, sorted, 'row selection state matches native checkboxes')
  }
  const rowRels = await table.locator('tbody tr').evaluateAll(rows => rows.map(row => row.dataset.fsRel))
  await movieRow.click()
  assert.equal(await movieRow.getAttribute('aria-current'), 'true', 'row clicks identify the current file')
  assert.equal(await movieRow.getAttribute('aria-selected'), 'false', 'row clicks do not check files')
  await expectChecked([])
  for (const name of ['复制', '剪切', '删除…']) assert.equal(await fileToolbar.getByRole('button', { name, exact: true }).isDisabled(), true)
  await movieRow.press('ArrowUp')
  await expectChecked([])
  assert.notEqual(await movieRow.getAttribute('aria-current'), 'true', 'ordinary arrows move focus without checking files')
  await movieRow.click()
  await movieRow.press('Space')
  await expectChecked(['模拟电影 (2020).mkv'])
  assert.equal(await allFiles.evaluate(input => input.indeterminate), true, 'some checked files give the header a mixed state')
  await movieRow.press('Space')
  await expectChecked([])
  await movieRow.press('Control+a')
  await expectChecked(rowRels)
  assert.equal(await allFiles.isChecked(), true)
  assert.equal(await allFiles.evaluate(input => input.indeterminate), false)
  await movieRow.press('Escape')
  await expectChecked([])
  assert.equal(await allFiles.isChecked(), false)
  assert.equal(await allFiles.evaluate(input => input.indeterminate), false)
  assert.equal(await movieRow.getAttribute('aria-current'), 'true', 'Escape clears checks but keeps the current row')
  await movieRow.press('Space')
  await expectChecked(['模拟电影 (2020).mkv'])
  await movieRow.press('Escape')
  // The surrounding label, not just the small checkbox, is an explicit target.
  const checkTarget = posterRow.locator('.fs-check-target')
  const targetSize = await checkTarget.boundingBox()
  assert.ok(targetSize.width >= 40 && targetSize.height >= 40, 'desktop checkbox hit area is at least 40px')
  await checkTarget.click({ position: { x: 3, y: targetSize.height / 2 } })
  await expectChecked(['poster.jpg'])
  await checkTarget.dblclick({ position: { x: 3, y: targetSize.height / 2 } })
  assert.equal(await page.locator('.file-preview-dialog, .player-dlg').count(), 0, 'double-clicking a checkbox target never previews the file')
  await posterRow.getByRole('checkbox').check()
  await movieRow.click()
  await selectionScreenshot(page, 'desktop-focus-and-checkbox')
  await movieRow.press('Escape')
  // Focusing a folder changes the open action's label. Its width must stay stable:
  // a toolbar wrap between mousedown and mouseup makes the checkbox miss its click.
  const tableBeforeFolderFocus = await table.boundingBox()
  await table.locator('tbody tr').nth(0).locator('.fs-check-target').click()
  assert.equal((await table.boundingBox()).y, tableBeforeFolderFocus.y, 'folder focus keeps the checkbox table in place')
  await expectChecked(rowRels.slice(0, 1))
  await table.locator('tbody tr').nth(2).locator('.fs-check-target').click({ modifiers: ['Shift'] })
  await expectChecked(rowRels.slice(0, 3))
  await table.locator('tbody tr').nth(2).locator('.fs-check-target').click({ modifiers: ['Shift'] })
  await expectChecked(rowRels.slice(0, 3))
  await movieRow.press('Escape')
  const firstRow = table.locator('tbody tr').first()
  await firstRow.click()
  await firstRow.press('Space')
  await firstRow.press('Shift+ArrowDown')
  await expectChecked(rowRels.slice(0, 2))
  await movieRow.press('Escape')
  await checkTarget.click()
  await table.locator('tr').filter({ hasText: '模拟电影.zh.srt' }).locator('.fs-check-target').click()
  const checkedGroup = ['poster.jpg', '模拟电影.zh.srt']
  const verifyMenuCopy = async (row, title, expected) => {
    await row.click({ button: 'right' })
    const menu = page.getByRole('menu', { name: '文件菜单', exact: true })
    assert.equal(await menu.locator('.fs-menu-target').textContent(), title)
    await expectChecked(checkedGroup)
    await menu.getByRole('menuitem', { name: /^复制\s/ }).click()
    await fileToolbar.getByRole('button', { name: '粘贴', exact: true }).click()
    const confirm = page.getByRole('region', { name: '文件操作确认', exact: true })
    await confirm.getByRole('button', { name: '确认复制到此目录', exact: true }).waitFor()
    assert.deepEqual([...requests.filter(request => request.key === '/api/fs/copy').at(-1).body.from].sort(), [...expected].sort())
    await expectChecked(checkedGroup)
    await confirm.getByRole('button', { name: '取消', exact: true }).click()
  }
  await verifyMenuCopy(movieRow, '模拟电影 (2020).mkv', ['模拟电影 (2020).mkv'])
  await verifyMenuCopy(posterRow, '已勾选 2 项', checkedGroup)
  await table.locator('tr').filter({ hasText: '模拟电影.zh.srt' }).locator('.fs-check-target').click()
  await movieRow.click()
  await movieRow.dblclick()
  await page.getByText('文件预览 · 不记录观看进度', { exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelector('.player-dlg video')?.readyState >= 2)
  assert.equal(requests.filter(r => /\/decide$/.test(r.key)).at(-1).body.kind, 'movie')
  await screenshot(page, 'file-preview-video', 'user-guide/files.md', '选中电影版本使用完整播放器预览，不记录观看进度；画面为合成视频')
  await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
  await page.locator('.player-dlg').waitFor({ state: 'hidden' })
  assert.equal(page.url(), currentLocation)
  assert.equal(await movieRow.getAttribute('aria-current'), 'true')
  assert.equal(await movieRow.getAttribute('aria-selected'), 'false')
  await expectChecked(['poster.jpg'])
  await table.locator('tr').filter({ hasText: '预览样本' }).dblclick()
  await table.getByText('未知格式.bin', { exact: true }).waitFor()
  const previewDialog = name => page.getByRole('dialog', { name: '文件预览：' + name, exact: true })
  const openPreview = async name => {
    await table.locator('tr').filter({ hasText: name }).dblclick()
    await previewDialog(name).waitFor()
    return previewDialog(name)
  }
  const closePreview = async () => {
    await page.getByRole('button', { name: '关闭文件预览', exact: true }).click()
    await page.locator('.file-preview-dialog').waitFor({ state: 'hidden' })
  }
  await page.getByLabel('搜索当前目录', { exact: true }).fill('字幕')
  const subtitleRow = table.locator('tbody tr').first()
  await subtitleRow.click(); await subtitleRow.press('Enter')
  await previewDialog('字幕.vtt').getByText(/用于核对的合成字幕/).waitFor()
  await screenshot(page, 'file-preview-text', 'user-guide/files.md', '文件管理内原地预览合成字幕，关闭保留原目录与当前行，不自动勾选')
  await closePreview()
  assert.equal(await page.getByLabel('搜索当前目录', { exact: true }).inputValue(), '字幕')
  assert.equal(await subtitleRow.getAttribute('aria-current'), 'true')
  assert.equal(await subtitleRow.getAttribute('aria-selected'), 'false')
  await expectChecked([])
  assert.equal(await subtitleRow.evaluate(el => el === document.activeElement), true)
  await page.getByLabel('搜索当前目录', { exact: true }).fill('')
  await openPreview('movie.nfo')
  await previewDialog('movie.nfo').locator('pre').filter({ hasText: '<script>不执行</script>' }).waitFor()
  assert.equal(await previewDialog('movie.nfo').locator('script').count(), 0, 'NFO remains text')
  await closePreview()
  await openPreview('海报.png')
  await page.waitForFunction(() => document.querySelector('.file-preview-dialog img')?.naturalWidth === 640)
  await closePreview()
  await openPreview('说明.pdf')
  assert.match(await previewDialog('说明.pdf').locator('iframe').getAttribute('src'), /inline=1/)
  await closePreview()
  await openPreview('未入库.mp4')
  await page.waitForFunction(() => document.querySelector('.file-preview-dialog video')?.readyState >= 2)
  await page.evaluate(() => { window.closedRawPreview = document.querySelector('.file-preview-dialog video') })
  await closePreview()
  assert.equal(await page.evaluate(() => window.closedRawPreview.paused && !window.closedRawPreview.hasAttribute('src')), true)
  await openPreview('格式不兼容.mkv')
  await previewDialog('格式不兼容.mkv').getByRole('alert').filter({ hasText: /浏览器无法播放/ }).waitFor()
  await closePreview()
  await openPreview('未知格式.bin')
  await previewDialog('未知格式.bin').getByText('此格式暂不支持预览', { exact: true }).waitFor()
  await closePreview()
  await openPreview('已消失.txt')
  await previewDialog('已消失.txt').getByRole('alert').filter({ hasText: /不存在|移走|删除/ }).waitFor()
  await closePreview()
  await openPreview('离线图片.png')
  await previewDialog('离线图片.png').getByRole('alert').filter({ hasText: /离线/ }).waitFor()
  await closePreview()
  await openPreview('离线文档.pdf')
  await previewDialog('离线文档.pdf').getByRole('alert').filter({ hasText: /离线/ }).waitFor()
  assert.equal(await previewDialog('离线文档.pdf').locator('iframe').count(), 0, 'PDF HTTP failures must not render raw error responses')
  await closePreview()
  await table.locator('tr').filter({ hasText: '关联花絮.mp4' }).dblclick()
  await page.getByText('文件预览 · 不记录观看进度', { exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelector('.player-dlg video')?.readyState >= 2)
  assert.equal(requests.filter(r => /\/decide$/.test(r.key)).at(-1).key, '/api/stream/73/decide')
  assert.equal(requests.filter(r => /\/decide$/.test(r.key)).at(-1).body.kind, 'extra', 'extra does not play its parent movie')
  await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
  assert.equal(requests.filter(r => r.key === '/api/stream/progress').length, progressBeforeFiles)
  assert.equal(summary(1).pending, false, 'preview never creates file changes')
  assert.equal(page.url(), currentLocation)
  await page.getByRole('button', { name: '上级目录', exact: true }).click()
  await table.getByText('poster.jpg', { exact: true }).waitFor()
  const row = table.locator('tr').filter({ hasText: 'poster.jpg' })
  await row.click({ button: 'right' })
  await page.getByRole('menuitem', { name: /^重命名/ }).click()
  await page.getByLabel('新文件名', { exact: true }).fill('poster-new.jpg')
  await page.getByRole('button', { name: '预览改名', exact: true }).click()
  await page.getByRole('button', { name: '确认改名', exact: true }).click()
  await table.getByText('poster-new.jpg', { exact: true }).waitFor()
  await page.getByText('1 项文件变更待扫描', { exact: true }).waitFor()
  await table.locator('tr').filter({ hasText: 'poster-new.jpg' }).locator('.fs-check-target').click()
  await movieRow.click()
  await screenshot(page, 'file-browser', 'user-guide/files.md', '目录树、待扫描提醒、当前电影行与独立勾选的海报文件，批量操作只针对勾选项')
  const dialog = page.getByRole('dialog', { name: '文件变更尚未核对' })
  const guardedFileLocation = page.url()
  const mainNav = page.getByRole('navigation', { name: '主导航' })
  await mainNav.getByRole('link', { name: '电影', exact: true }).click()
  await dialog.waitFor()
  await dialog.getByRole('button', { name: '继续管理', exact: true }).click()
  assert.equal(page.url(), guardedFileLocation, 'main navigation respects cancelled file-review guards')
  assert.equal(await page.getByRole('combobox', { name: '切换媒体库' }).inputValue(), '1')
  await mainNav.getByRole('link', { name: '设置', exact: true }).click()
  await dialog.getByRole('button', { name: '稍后处理', exact: true }).click()
  await page.waitForURL(url => url.pathname === '/settings' && !url.searchParams.has('sec'))
  assert.equal(new URL(page.url()).searchParams.get('media'), '1', 'main navigation keeps the selected media scope')
  assert.equal(summary(1).pending, true, 'leaving through the main navigation does not acknowledge file changes')
  await page.goto(guardedFileLocation)
  await table.getByText('poster-new.jpg', { exact: true }).waitFor()
  await page.locator('.side-nav').getByRole('button', { name: '界面显示', exact: true }).click()
  await dialog.waitFor()
  await screenshot(page, 'file-review', 'user-guide/files.md', '离开文件管理前选择扫描核对、稍后处理或继续管理')
  await dialog.getByRole('button', { name: '继续管理', exact: true }).click()
  assert.match(page.url(), /sec=sec-files/)
  await page.getByRole('combobox', { name: '切换媒体库' }).selectOption('2')
  await dialog.getByRole('button', { name: '扫描并核对', exact: true }).click()
  await page.waitForURL(/sec=sec-sync/)
  assert.equal(new URL(page.url()).searchParams.get('library'), '1')
  assert.equal(await page.getByRole('combobox', { name: '切换媒体库' }).inputValue(), '1', 'scan redirect keeps the original media selected')
  await page.getByText(/^扫描完成：/).first().waitFor()
  mark(1, 'rename', 'poster-new.jpg') // A later durable server event must remain after a completed scan.
  await page.locator('.side-nav').getByRole('button', { name: '文件管理', exact: true }).click()
  await table.locator('tbody tr').nth(3).waitFor()
  const tree = page.getByRole('navigation', { name: '视频库目录' })
  const globalMedia = page.getByRole('combobox', { name: '切换媒体库' })
  const sourceLocation = page.url()
  // Tree navigation is proposed first: cancelling its leave guard must preserve
  // the source library, global media selector, remembered tab, and loaded files.
  await tree.getByRole('button', { name: /演示媒体库乙.*备用剧集/ }).click()
  await dialog.waitFor()
  assert.equal(page.url(), sourceLocation)
  assert.equal(await globalMedia.inputValue(), '1')
  await dialog.getByRole('button', { name: '继续管理', exact: true }).click()
  assert.equal(page.url(), sourceLocation)
  assert.equal(await globalMedia.inputValue(), '1')
  assert.equal(await page.evaluate(() => localStorage.getItem('jzmedia.lib')), '1')
  assert.equal(await table.getByText('poster-new.jpg', { exact: true }).count(), 1)
  await tree.getByRole('button', { name: /演示媒体库乙.*备用剧集/ }).click()
  await dialog.getByRole('button', { name: '稍后处理', exact: true }).click()
  await page.waitForURL(/library=4/)
  await table.getByText('演示剧.S01E01.mkv', { exact: true }).waitFor()
  assert.equal(new URL(page.url()).searchParams.get('media'), '2')
  assert.equal(await globalMedia.inputValue(), '2', 'accepted tree navigation updates the global media selector')
  assert.equal(await page.evaluate(() => localStorage.getItem('jzmedia.lib')), '4', 'switching media must preserve a non-first video library')
  assert.equal(await page.evaluate(() => localStorage.getItem('jzmedia.media')), '2')
  assert.equal(summary(1).pending, true, 'changing media must preserve pending changes in the source library')
  // Return to a clean library in the first media; both contexts must follow.
  await tree.getByRole('button', { name: /演示媒体库 · 剧集/ }).click()
  await page.waitForURL(/library=2/)
  await table.getByText('演示剧.S01E01.mkv', { exact: true }).waitFor()
  assert.equal(await globalMedia.inputValue(), '1')
  assert.equal(await page.evaluate(() => localStorage.getItem('jzmedia.lib')), '2')
  assert.equal(summary(1).pending, true)
  await table.locator('tr').filter({ hasText: '演示剧.S01E01.mkv' }).dblclick()
  await page.getByText('文件预览 · 不记录观看进度', { exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelector('.player-dlg video')?.readyState >= 2)
  assert.equal(requests.filter(r => /\/decide$/.test(r.key)).at(-1).body.kind, 'episode')
  await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
  const tvPoster = table.locator('tr').filter({ hasText: 'poster.jpg' })
  await tvPoster.locator('.fs-check-target').click()
  await tvPoster.click(); await tvPoster.press('F2')
  await page.getByLabel('新文件名', { exact: true }).fill('tv-poster.jpg')
  await page.getByRole('button', { name: '预览改名', exact: true }).click()
  await page.getByRole('button', { name: '确认改名', exact: true }).click()
  await table.getByText('tv-poster.jpg', { exact: true }).waitFor()
  await page.locator('.side-nav').getByRole('button', { name: '界面显示', exact: true }).click()
  await dialog.getByRole('button', { name: '扫描并核对', exact: true }).click()
  await page.waitForURL(/sec=sec-sync/)
  assert.equal(requests.filter(r => r.key === '/api/jobs/scan' && r.method === 'POST').at(-1).body.library_id, 2)
  assert.equal(summary(1).pending, true, 'scanning TV must not acknowledge movie changes')
  await page.locator('.side-nav').getByRole('button', { name: '文件管理', exact: true }).click()
  await table.locator('tbody tr').first().waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  const mobileSort = page.getByRole('combobox', { name: '文件排序', exact: true })
  await mobileSort.selectOption('size')
  assert.equal(await table.locator('th.fs-col-size').getAttribute('aria-sort'), 'ascending')
  await page.getByRole('button', { name: '切换为降序', exact: true }).click()
  assert.equal(await table.locator('th.fs-col-size').getAttribute('aria-sort'), 'descending')
  await mobileSort.selectOption('name')
  assert.equal(await table.locator('th.fs-col-name').getAttribute('aria-sort'), 'ascending')
  await page.getByLabel('搜索当前目录', { exact: true }).fill('poster')
  assert.equal(await table.locator('tbody tr').count(), 1)
  const mobileRow = table.locator('tbody tr').first()
  await mobileRow.click()
  await expectChecked([])
  const mobileTarget = mobileRow.locator('.fs-check-target')
  const mobileTargetSize = await mobileTarget.boundingBox()
  assert.ok(mobileTargetSize.width >= 44 && mobileTargetSize.height >= 44, 'mobile checkbox hit area is at least 44px')
  await mobileTarget.click({ position: { x: 3, y: mobileTargetSize.height / 2 } })
  await expectChecked(['tv-poster.jpg'])
  await selectionScreenshot(page, 'mobile-checkbox')
  await page.getByRole('button', { name: '更多文件操作', exact: true }).click()
  await page.getByRole('menu', { name: '文件菜单' }).waitFor()
  await screenshot(page, 'file-browser-mobile', 'user-guide/files.md', '手机文件管理及更多操作菜单')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.getByRole('menuitem', { name: /^预览/ }).click()
  await previewDialog('tv-poster.jpg').waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.keyboard.press('Escape')
  await page.locator('.file-preview-dialog').waitFor({ state: 'hidden' })
  // Returning to tools must still review pending changes, even if a preview is
  // open when the user uses browser Back. The review takes focus after cleanup.
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(base + '/settings?sec=sec-libtools&library=1&media=1')
  await views.getByRole('button', { name: '资料维护', exact: true }).click()
  await openFiles.click()
  await table.getByText('poster-new.jpg', { exact: true }).waitFor()
  await table.locator('tr').filter({ hasText: 'poster-new.jpg' }).dblclick()
  await previewDialog('poster-new.jpg').waitFor()
  await page.evaluate(() => history.back())
  await dialog.waitFor()
  assert.equal(await page.locator('.file-preview-dialog').count(), 0, 'leave review closes preview before taking focus')
  await dialog.getByRole('button', { name: '继续管理', exact: true }).click()
  assert.match(page.url(), /sec=sec-files/)
  await returnFiles.click()
  await dialog.getByRole('button', { name: '稍后处理', exact: true }).click()
  await page.waitForURL(/sec=sec-libtools/)
  await page.waitForFunction(() => [...document.querySelectorAll('.tool-views button.on')].some(el => el.textContent === '资料维护' && el.offsetParent))
  assert.equal(summary(1).pending, true)
  await openFiles.click()
  await returnFiles.click()
  await dialog.getByRole('button', { name: '扫描并核对', exact: true }).click()
  await page.waitForURL(/sec=sec-sync/)
  await page.getByText(/^扫描完成：/).first().waitFor()
  assert.equal(await page.evaluate(() => sessionStorage.getItem('jzmedia.files.origin.v1')), null)
  }
  // Exercise the actual player in preview and ordinary modes, including the
  // parent saveFinal path, timeupdate/ended and the native beforeunload event.
  const playerPage = await context.newPage()
  playerPage.on('pageerror', e => errors.push(String(e)))
  await playerPage.goto(base + '/player-harness.html')
  const progressRequests = () => requests.filter(r => r.key === '/api/stream/progress')
  const beforePreview = progressRequests().length
  await playerPage.getByRole('button', { name: '打开预览播放器', exact: true }).click()
  await playerPage.getByText('文件预览 · 不记录观看进度', { exact: true }).waitFor()
  await playerPage.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
  assert.equal(await playerPage.locator('.resume-bar').count(), 0)
  // The narrow toolbar keeps every action reachable without shrinking touch targets.
  const playerChecks = []
  const playerArtifacts = path.join(root, 'output/playwright/design-player')
  await mkdir(playerArtifacts, { recursive: true })
  for (const width of [1440, 390, 375, 320]) {
    await playerPage.setViewportSize({ width, height: width > 700 ? 1000 : 844 })
    const controls = await playerPage.locator('.pv-ctl').evaluate(element => {
      const outer = element.getBoundingClientRect()
      return [...element.querySelectorAll('button')].filter(button => button.checkVisibility()).map(button => {
        const rect = button.getBoundingClientRect()
        return { label: button.getAttribute('aria-label') || button.textContent.trim(),
          width: rect.width, height: rect.height, inside: rect.left >= outer.left - 1 && rect.right <= outer.right + 1 }
      })
    })
    assert.ok(controls.every(button => button.inside), `${width}px player toolbar must not clip controls: ${JSON.stringify(controls)}`)
    assert.equal(controls.length, 6, `${width}px player retains six direct controls`)
    if (width <= 700) assert.ok(controls.every(button => button.width >= 43.5 && button.height >= 43.5), JSON.stringify(controls))
    await playerPage.getByRole('button', { name: /^播放速度 .* 倍$/ }).click()
    const speedPanel = playerPage.getByRole('region', { name: '播放速度', exact: true })
    await speedPanel.getByRole('button', { name: '1.5×', exact: true }).click()
    assert.equal(await playerPage.locator('video').evaluate(video => video.playbackRate), 1.5)
    await speedPanel.waitFor({ state: 'hidden' })
    await playerPage.getByRole('button', { name: '播放设置', exact: true }).click()
    const panel = playerPage.getByRole('region', { name: '播放设置', exact: true })
    await panel.waitFor()
    const bounds = await panel.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }))
    assert.ok(bounds.scroll <= bounds.width + 1, `${width}px settings must not overflow`)
    if (width <= 700) {
      const small = await panel.locator('button').evaluateAll(buttons => buttons.filter(button => button.checkVisibility()).map(button => {
        const rect = button.getBoundingClientRect()
        return { label: button.textContent.trim(), width: rect.width, height: rect.height }
      }).filter(button => button.width < 43.5 || button.height < 43.5))
      assert.deepEqual(small, [])
    }
    await playerPage.screenshot({ path: path.join(playerArtifacts, `settings-${width}.png`), animations: 'disabled' })
    if (width === 1440) await screenshot(playerPage, 'player-settings', 'user-guide/playback-controls.md', '合成视频预览中的画质、字幕调整与更多设置；不操作真实片源')
    await playerPage.getByRole('button', { name: '关闭设置', exact: true }).click()
    await playerPage.screenshot({ path: path.join(playerArtifacts, `controls-${width}.png`), animations: 'disabled' })
    if (width === 390) await screenshot(playerPage, 'mobile-player', 'user-guide/playback-controls.md', '390px手机模拟：六个44px主控、独立倍速及字幕入口、画面右上角设置与全屏；合成视频预览')
    playerChecks.push({ width, controls, settings: bounds, rate: 1.5 })
  }
  await playerPage.getByRole('button', { name: '播放设置', exact: true }).click()
  const previewPanel = playerPage.getByRole('region', { name: '播放设置', exact: true })
  await previewPanel.getByText('更多选项', { exact: true }).click()
  const startPreview = previewPanel.getByRole('button', { name: '生成预览', exact: true })
  await startPreview.click()
  await previewPanel.getByText(/37\/100 张（37%）/).waitFor()
  await previewPanel.getByText('预览已生成完毕', { exact: true }).waitFor()
  assert.equal(await previewPanel.getByRole('button', { name: '已生成预览', exact: true }).isDisabled(), true)
  assert.equal(requests.filter(r => r.key === '/api/stream/81/previews' && r.method === 'POST').length, 1)
  await playerPage.getByRole('button', { name: '关闭设置', exact: true }).click()
  await playerPage.getByRole('button', { name: '全屏', exact: true }).click()
  await playerPage.waitForFunction(() => document.fullscreenElement)
  await playerPage.getByRole('button', { name: '播放设置', exact: true }).click()
  await playerPage.getByRole('region', { name: '播放设置', exact: true }).waitFor()
  assert.equal(await playerPage.getByRole('button', { name: '播放设置', exact: true }).count(), 1)
  await playerPage.getByRole('button', { name: '关闭设置', exact: true }).click()
  await playerPage.getByRole('button', { name: '退出全屏', exact: true }).click()
  await playerPage.waitForFunction(() => !document.fullscreenElement)
  const touchContext = await browser.newContext({ viewport: { width: 320, height: 844 }, hasTouch: true, serviceWorkers: 'block' })
  await touchContext.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  const touchPage = await touchContext.newPage()
  touchPage.on('pageerror', error => errors.push(String(error)))
  await touchPage.goto(base + '/player-harness.html')
  await touchPage.getByRole('button', { name: '打开预览播放器', exact: true }).tap()
  await touchPage.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
  await touchPage.getByRole('button', { name: /^播放速度 .* 倍$/ }).tap()
  await touchPage.getByRole('region', { name: '播放速度', exact: true }).getByRole('button', { name: '2×', exact: true }).tap()
  assert.equal(await touchPage.locator('video').evaluate(video => video.playbackRate), 2)
  await touchPage.getByRole('button', { name: '音量调节', exact: true }).tap()
  await touchPage.getByRole('button', { name: '静音', exact: true }).tap()
  assert.equal(await touchPage.locator('video').evaluate(video => video.muted), true)
  await touchPage.getByRole('button', { name: '关闭播放器', exact: true }).tap()
  await touchPage.locator('.player-dlg').waitFor({ state: 'hidden' })
  await touchContext.close()
  await writeFile(path.join(playerArtifacts, 'checks.json'), JSON.stringify({ mocked: true, syntheticMedia: true,
    deviceScaleFactor: 1, playerChecks, fullscreenSettings: true, coarsePointer320: ['tap speed', 'tap 2×', 'tap volume', 'tap mute', 'tap close'] }, null, 2) + '\n')
  await playerPage.evaluate(() => {
    const video = document.querySelector('video')
    video.pause()
    window.closedPreviewVideo = video
    Object.defineProperty(video, 'currentTime', { configurable: true, get: () => 1140 })
    video.dispatchEvent(new Event('timeupdate'))
    video.dispatchEvent(new Event('ended'))
    window.dispatchEvent(new Event('beforeunload'))
  })
  assert.equal(await playerPage.locator('#player-events').textContent(), 'watched=0;ended=0')
  await playerPage.getByRole('button', { name: '关闭播放器', exact: true }).click()
  await playerPage.locator('.player-dlg').waitFor({ state: 'hidden' })
  assert.equal(progressRequests().length, beforePreview, 'preview must never read, write or delete viewing progress')
  assert.equal(await playerPage.evaluate(() => window.closedPreviewVideo.paused && !window.closedPreviewVideo.hasAttribute('src')), true)
  await playerPage.getByRole('button', { name: '打开普通播放器', exact: true }).click()
  await playerPage.getByRole('button', { name: '从头开始', exact: true }).click()
  await playerPage.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
  await playerPage.evaluate(() => {
    const video = document.querySelector('video')
    video.pause()
    Object.defineProperty(video, 'currentTime', { configurable: true, get: () => 1140 })
    video.dispatchEvent(new Event('timeupdate'))
    video.dispatchEvent(new Event('ended'))
  })
  await playerPage.waitForFunction(() => window.testPlayerEvents.watched.value > 0 && window.testPlayerEvents.ended.value > 0)
  await playerPage.getByRole('button', { name: '关闭播放器', exact: true }).click()
  await playerPage.locator('.player-dlg').waitFor({ state: 'hidden' })
  assert.ok(progressRequests().some(r => r.method === 'GET'), 'ordinary playback restores progress')
  assert.ok(progressRequests().some(r => r.method === 'DELETE'), 'ordinary restart clears progress')
  assert.ok(progressRequests().some(r => r.method === 'POST' && r.body.position === 1140), 'ordinary playback saves progress')
  await playerPage.close()
  await checkPlaybackCompatibility(browser, base)
  await checkMp4Compatibility(browser, base)
  await checkAssFullscreen(browser, base)
  await context.close()
  }
  }
  assert.deepEqual(unexpected, [], 'all API calls must be mocked explicitly')
  assert.deepEqual(errors, [], 'no browser or fixture errors')
  if (previewsOnly) assert.equal(await frontendFingerprint(), sourceSha256, 'Frontend source changed during preview checks; rerun after it is stable')
  if (capture) {
    assert.equal(await frontendFingerprint(), sourceSha256, 'Frontend source changed during capture; rerun after it is stable')
    const filename = path.join(root, 'docs/assets/manifest.json')
    const manifest = JSON.parse(await readFile(filename, 'utf8'))
    const names = new Set(captured.map(entry => entry.file))
    manifest.assets = manifest.assets.filter(entry => !names.has(entry.file)).concat(captured)
    await writeFile(filename, JSON.stringify(manifest, null, 2) + '\n')
  }
  console.log(toolsOnly ? 'PASS compact library tools isolated browser checks' : cacheOnly ? 'PASS cache budgets: exact-edit popups, local input guards and keyboard viewport, independent quotas, frozen drag scale and overflow recovery, viewport-safe tooltips and segment labels, grouped inputs, real mouse/touch allocation, keyboard bounds, secondary-pointer termination isolation, Tab/drag cancellation, non-overlapping handles, allocation colors/proportions, legacy/zero compatibility, exact/over budgets, base growth, statistics/save failures, cleanup confirmation, persistence, busy/leave guards and 1440/390/320 layouts' : previewsOnly ? 'PASS preview maintenance isolated browser checks' : draftsOnly ? 'PASS settings draft regression' : playerOnly ? 'PASS player: controls, rate, fullscreen, preview/normal lifecycle; native HLS detached clock fallback without repeated recovery, MP4 clip prefetch/continuation/seek/source subtitle offset/lease cleanup and diagnostic copy; real ASS worker and video/subtitle pixels in phone portrait/landscape fullscreen' : 'PASS settings, files and playback: draft/navigation/preview lifecycle; browser playback compatibility, MP4 source timeline and subtitles, ASS fullscreen pixels and cleanup')
} finally {
  if (browser) await browser.close()
  if (server) await new Promise(resolve => server.close(resolve))
  await rm(work, { recursive: true, force: true })
}
