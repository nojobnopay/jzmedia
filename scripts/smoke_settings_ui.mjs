// Real Vue UI + in-memory API fixtures. Never opens .env, backend, DB or media.
// Run: node scripts/smoke_settings_ui.mjs [--capture-docs] [--player-only|--drafts-only]
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
assert.ok(args.every(arg => ['--capture-docs', '--player-only', '--drafts-only'].includes(arg)))
const capture = args.includes('--capture-docs')
const playerOnly = args.includes('--player-only')
const draftsOnly = args.includes('--drafts-only')
assert.ok(!(playerOnly && draftsOnly), 'Choose one focused regression scope')
assert.ok(!(capture && draftsOnly), 'Documentation capture requires the complete settings regression')
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
const sourceSha256 = capture ? await frontendFingerprint() : null
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
    h('output',{id:'player-events'},'watched='+watched.value+';ended='+ended.value),
    mode.value ? h(PlayerModal,{key:mode.value,ref:player,versionId:81,title:'合成测试视频',preview:mode.value==='preview',
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
let videoFixture, imageFixture
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
  if (key === '/api/onboarding') return { show_welcome: false, status: 'completed' }
  if (key === '/api/settings') return { tmdb_configured: false, tmdb_language: 'zh-CN', jzmedia_token_source: 'unset' }
  if (key === '/api/ai/settings') return { enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'demo', daily_limit: 100, timeout_seconds: 12, usage: { requests: 0, input_tokens: 0, output_tokens: 0 } }
  if (key === '/api/jobs/stats') return { grouped: 10, versions: 12, no_match: 1, needs_review: 2, missing_files: 0,
    by_library: [{ library_id: 1, pending: 3, missing_files: 0 }] }
  if (key === '/api/tv/stats') return { shows: 3, seasons: 5, episodes: 60, pending: 0, episode_review: 0, by_library: [] }
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
  if (/^\/api\/stream\/\d+\/previews$/.test(key)) return { state: 'missing', pages: [] }
  if (/^\/api\/stream\/\d+\/decide$/.test(key)) return { method: 'direct', direct_url: directFixtureUrl, reasons: [],
    media: { width: 320, height: 180, duration: 1200, audio: [], subs: [] }, plan: {} }
  if (key === '/api/stream/progress') return method === 'GET' ? { position: 25, duration: 1200, position_text: '00:25' } : { ok: true }
  if (key === '/api/stream/previews/jobs/latest') return { state: 'idle' }
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
  const noFalseEmpty = async () => {
    assert.equal(await page.getByText('还没有视频库。', { exact: false }).count(), 0)
    assert.equal(await page.getByText('此文件夹为空', { exact: true }).count(), 0)
  }
  try {
    await page.goto(base + '/settings?sec=sec-files&library=1')
    await page.getByText('正在加载视频库…', { exact: true }).waitFor()
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
    await page.getByText('视频库加载失败', { exact: true }).waitFor()
    await noFalseEmpty()
    failLibraries = false
    await page.locator('.library-load-state').getByRole('button', { name: '重新加载', exact: true }).click()
    await rows.nth(4).waitFor()
    assert.equal(await page.locator('.library-load-state').count(), 0)

    emptyLibraries = true
    await page.goto(base + '/settings?sec=sec-files')
    await page.getByText('还没有视频库。', { exact: false }).waitFor()
    assert.equal(await page.locator('.library-load-state').count(), 0, 'an actually empty library has a distinct settled state')
  } finally {
    for (const release of gates) release()
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
try {
  const ffmpegPath = await ffmpeg()
  execFileSync(ffmpegPath, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10', '-t', '40', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', path.join(work, 'video.mp4')])
  execFileSync(ffmpegPath, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=0x325b70:size=640x360', '-frames:v', '1', path.join(work, 'image.png')])
  videoFixture = await readFile(path.join(work, 'video.mp4'))
  imageFixture = await readFile(path.join(work, 'image.png'))
  const output = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(work, 'cache'), plugins: [vue(), {
    name: 'isolated-player-harness', resolveId: id => id === harnessId ? '\0' + harnessId : null,
    load: id => id === '\0' + harnessId ? harnessSource : null,
  }], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
    rollupOptions: { input: { app: path.join(root, 'frontend/index.html'), playerHarness: harnessId } } } })
  harnessEntry = output.output.find(item => item.type === 'chunk' && item.isEntry && item.name === 'playerHarness').fileName
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost')
      res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'")
      if (url.pathname === '/player-harness.html') {
        const styles = (await readdir(path.join(dist, 'assets'))).filter(name => name.endsWith('.css'))
        res.writeHead(200, { 'Content-Type': 'text/html' })
        res.end('<!doctype html><html><head>' + styles.map(name => '<link rel="stylesheet" href="/assets/' + name + '">').join('') + '</head><body><div id="app"></div><script type="module" src="/' + harnessEntry + '"></script></body></html>'); return
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
      const type = { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream'
      const data = await readFile(file); res.writeHead(200, { 'Content-Type': type }); res.end(data)
    } catch (error) { errors.push(String(error)); res.writeHead(500); res.end('Fixture error') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  if (!playerOnly) await checkSettingsDrafts(browser, base)
  if (!playerOnly && !draftsOnly) await checkFileLoading(browser, base)
  if (!draftsOnly) {
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
    if (width <= 700) assert.ok(controls.every(button => button.width >= 43.5 && button.height >= 43.5), JSON.stringify(controls))
    await playerPage.getByRole('button', { name: '播放设置', exact: true }).click()
    const panel = playerPage.getByRole('region', { name: '播放设置', exact: true })
    await panel.waitFor()
    const bounds = await panel.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }))
    assert.ok(bounds.scroll <= bounds.width + 1, `${width}px settings must not overflow`)
    await panel.getByRole('button', { name: '1.5×', exact: true }).click()
    assert.equal(await playerPage.locator('video').evaluate(video => video.playbackRate), 1.5)
    if (width <= 700) {
      const small = await panel.locator('button').evaluateAll(buttons => buttons.filter(button => button.checkVisibility()).map(button => {
        const rect = button.getBoundingClientRect()
        return { label: button.textContent.trim(), width: rect.width, height: rect.height }
      }).filter(button => button.width < 43.5 || button.height < 43.5))
      assert.deepEqual(small, [])
    }
    await playerPage.screenshot({ path: path.join(playerArtifacts, `settings-${width}.png`), animations: 'disabled' })
    if (width === 1440) await screenshot(playerPage, 'player-settings', 'user-guide/playback-controls.md', '合成视频预览中的统一播放器设置与六档倍速；不操作真实片源')
    await playerPage.getByRole('button', { name: '关闭设置', exact: true }).click()
    await playerPage.screenshot({ path: path.join(playerArtifacts, `controls-${width}.png`), animations: 'disabled' })
    if (width === 390) await screenshot(playerPage, 'mobile-player', 'user-guide/playback-controls.md', '390px手机模拟：44px主控、时间独立一行、倍速从设置进入；合成视频')
    playerChecks.push({ width, controls, settings: bounds, rate: 1.5 })
  }
  await playerPage.getByRole('button', { name: '全屏', exact: true }).click()
  await playerPage.waitForFunction(() => document.fullscreenElement)
  await playerPage.getByRole('button', { name: '播放设置', exact: true }).click()
  await playerPage.getByRole('region', { name: '播放设置', exact: true }).waitFor()
  assert.equal(await playerPage.getByRole('button', { name: '播放设置', exact: true }).count(), 1)
  await playerPage.getByRole('button', { name: '关闭设置', exact: true }).click()
  await playerPage.locator('.ctl-options').getByRole('button', { name: '退出全屏', exact: true }).click()
  await playerPage.waitForFunction(() => !document.fullscreenElement)
  const touchContext = await browser.newContext({ viewport: { width: 320, height: 844 }, hasTouch: true, serviceWorkers: 'block' })
  await touchContext.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  const touchPage = await touchContext.newPage()
  touchPage.on('pageerror', error => errors.push(String(error)))
  await touchPage.goto(base + '/player-harness.html')
  await touchPage.getByRole('button', { name: '打开预览播放器', exact: true }).tap()
  await touchPage.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
  await touchPage.getByRole('button', { name: '播放设置', exact: true }).tap()
  await touchPage.getByRole('button', { name: '2×', exact: true }).tap()
  assert.equal(await touchPage.locator('video').evaluate(video => video.playbackRate), 2)
  await touchPage.getByRole('button', { name: '关闭设置', exact: true }).tap()
  await touchPage.getByRole('button', { name: '静音', exact: true }).tap()
  assert.equal(await touchPage.locator('video').evaluate(video => video.muted), true)
  await touchPage.getByRole('button', { name: '关闭播放器', exact: true }).tap()
  await touchPage.locator('.player-dlg').waitFor({ state: 'hidden' })
  await touchContext.close()
  await writeFile(path.join(playerArtifacts, 'checks.json'), JSON.stringify({ mocked: true, syntheticMedia: true,
    deviceScaleFactor: 1, playerChecks, fullscreenSettings: true, coarsePointer320: ['tap settings', 'tap 2×', 'tap mute', 'tap close'] }, null, 2) + '\n')
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
  await context.close()
  }
  assert.deepEqual(unexpected, [], 'all API calls must be mocked explicitly')
  assert.deepEqual(errors, [], 'no browser or fixture errors')
  if (capture) {
    assert.equal(await frontendFingerprint(), sourceSha256, 'Frontend source changed during capture; rerun after it is stable')
    const filename = path.join(root, 'docs/assets/manifest.json')
    const manifest = JSON.parse(await readFile(filename, 'utf8'))
    const names = new Set(captured.map(entry => entry.file))
    manifest.assets = manifest.assets.filter(entry => !names.has(entry.file)).concat(captured)
    await writeFile(filename, JSON.stringify(manifest, null, 2) + '\n')
  }
  console.log(draftsOnly ? 'PASS settings draft regression' : playerOnly ? 'PASS player: 1440/390/375/320px controls, 44px targets, settings/rate, fullscreen, preview/normal progress lifecycle' : 'PASS settings: draft leave/refresh/save lifecycle, startup loading/error/retry/empty states, independent directory loading, navigation, matching drafts/order/search, complete file listing, independent row focus and explicit checkbox selection, 40px/44px checkbox targets, keyboard/range/mixed selection, context menu copy targets, preview selection preservation, rename, leave guard, accepted cross-media navigation, scoped scan, mobile menu, real player preview/normal progress lifecycle')
} finally {
  if (browser) await browser.close()
  if (server) await new Promise(resolve => server.close(resolve))
  await rm(work, { recursive: true, force: true })
}
