// Real Vue pages, synthetic art and in-memory API only. No backend or media access.
// node scripts/smoke_tv_ingest.mjs [--baseline]
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, readdir, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { artworkFingerprint, createFixture, shows } from './fixtures/uiReviewData.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const baseline = process.argv.includes('--baseline')
assert.ok(process.argv.slice(2).every(arg => arg === '--baseline'))
const output = path.join(root, 'output/playwright/tv-ingest', baseline ? 'before' : 'after')
const temp = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-tv-ingest-ui-'))
const fixture = createFixture(), requests = [], errors = [], unexpected = []
async function sourceFingerprint() {
  const hash = createHash('sha256')
  async function walk(directory) {
    for (const entry of (await readdir(directory, { withFileTypes: true })).sort((left, right) => left.name.localeCompare(right.name))) {
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) await walk(file)
      else if (entry.isFile()) { hash.update(path.relative(root, file)); hash.update(await readFile(file)) }
    }
  }
  for (const directory of ['frontend/src', 'frontend/public']) await walk(path.join(root, directory))
  for (const file of ['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json']) { hash.update(file); hash.update(await readFile(path.join(root, file))) }
  return hash.digest('hex')
}
async function fixtureFingerprint() {
  return createHash('sha256').update(await readFile(fileURLToPath(import.meta.url)))
    .update(await readFile(new URL('./fixtures/uiReviewData.mjs', import.meta.url))).update(artworkFingerprint).digest('hex')
}
const sourceSha256 = await sourceFingerprint(), fixtureSha256 = await fixtureFingerprint()
let browser, server
let preview = 1, decision = '', prepared = false, executed = false, uploadCount = 0, preparedIds = [201]
let conflictOnce = false
const plan = (id = 201) => ({ show_id: id, title: shows[id - 201]?.title || '合成剧集', library_id: 2,
  needs: true, fingerprint: 'synthetic-files-version1', counts: { rename: 8 }, blocked: false, absolute_risk: false,
  groups: [{ action: 'rename', episodes: 8, files: 8, count: 16, to: '群岛之间 (2023)/Season 05',
    samples: [{ from: '群岛之间/Season 05/发布组.长文件名.'.repeat(5) + 'S05E01.mkv', to: '群岛之间 (2023)/Season 05/群岛之间-S05E01-潮汐带来的消息.mkv' }] }],
  dir_totals: [{ dir: '群岛之间 (2023)/Season 05', count: 8 }], manual: [], conflicts: [], warnings: [],
  metadata: { filled: 8, status: 'ok', issues: [] },
})
function summary() { return { preview_id: 'preview-' + preview, plans: executed ? [] : preparedIds.map(id => plan(id)),
  params: { ids: preparedIds, actions: ['seasondir', 'rename'], allow_torrent: false, allow_absolute_shows: [] },
  counts: { rename: preparedIds.length * 8 }, total: executed ? 0 : preparedIds.length * 8, metadata_results: preparedIds.map(show_id => ({ show_id, filled: 8 })), auto_prompt: !decision,
  auto_prompt_ids: decision ? [] : preparedIds } }
function api(url, method, body) {
  const key = url.pathname
  requests.push({ key, method, body })
  if (key === '/api/jobs/tv-ingest' && method === 'POST') { prepared = true; executed = false; preparedIds = body.ids || [201, 202]; preview++; return { job_id: 'ingest' } }
  if (key === '/api/jobs/tv-ingest/ingest') return { state: 'done', phase: 'preview', summary: summary() }
  if (key === '/api/tv/ingestion-reviews') return { items: prepared && !executed && (decision !== 'ignored' || url.searchParams.get('include_ignored') === 'true')
    ? preparedIds.map(show_id => ({ show_id, library_id: 2, preview_id: 'preview-' + preview, state: decision || 'pending', plan: plan(show_id), metadata: plan(show_id).metadata })) : [], count: 0 }
  if (key === '/api/tv/ingestion-reviews/decision') { decision = body.decision; return { ok: true } }
  if (key === '/api/jobs/tv-organize' && method === 'POST') {
    if (conflictOnce) { conflictOnce = false; return { __status: 409, detail: '文件已变化，请重新预览' } }
    executed = true; return { job_id: 'organize' }
  }
  if (key === '/api/jobs/tv-organize/organize') return { state: 'done', summary: { moved: 16, renamed: 0, skipped: 0, failed: 0 }, done: 2, total: 2 }
  if (/\/discover$/.test(key)) return { added: 8, unknown: 0 }
  if (key === '/api/uploads/check-access') return { ok: true }
  if (key === '/api/uploads') return { status: 'tv_ok', show_id: 201, show: '群岛之间', episode_id: 800 + ++uploadCount }
  if (key === '/api/tv/bindings/directories') return { items: [], refreshing: false }
  if (key === '/api/tv/bindings/history') return { items: [] }
  const result = fixture.api(url, method, body)
  if (result === undefined) unexpected.push(method + ' ' + key)
  return result
}
try {
  await mkdir(output, { recursive: true })
  const dist = path.join(temp, 'dist')
  await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(temp, 'cache'),
    plugins: [vue()], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (key.startsWith('/posters/') || /\/(backdrop|still|poster)$/.test(key)) { res.writeHead(200, { 'Content-Type': 'image/svg+xml' }); res.end(fixture.artwork(key)); return }
      if (key.startsWith('/api/')) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString(), result = api(url, req.method, raw && req.headers['content-type']?.includes('application/json') ? JSON.parse(raw) : undefined)
        res.writeHead(result?.__status || (result === undefined ? 501 : 200), { 'Content-Type': 'application/json' }); res.end(JSON.stringify(result ?? { detail: 'Unmocked isolated API' })); return
      }
      const file = key.startsWith('/assets/') || /^\/(favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key) ? path.join(dist, key) : path.join(dist, 'index.html')
      const bytes = await readFile(file)
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' }); res.end(bytes)
    } catch (error) { unexpected.push(String(error)); res.writeHead(500); res.end('Isolated fixture failure') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const width of [1440, 390, 320]) {
    prepared = false; decision = ''; executed = false; preview++
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(String(error)))
    page.setDefaultTimeout(12000)
    await page.goto(base + '/tv/201'); await page.getByRole('navigation', { name: '主导航' }).waitFor()
    if (baseline) {
      await page.locator('summary').filter({ hasText: '更多操作' }).click()
      await page.screenshot({ path: path.join(output, `detail-${width}.png`), fullPage: true })
      await page.getByRole('button', { name: '整理剧集目录', exact: true }).click()
      await page.getByRole('heading', { name: '整理剧集目录', exact: true }).waitFor()
      await page.screenshot({ path: path.join(output, `preview-${width}.png`), fullPage: true })
    } else {
      const dialog = page.getByRole('dialog', { name: '剧集入库与整理预览', exact: true })
      const openManual = async () => {
        await page.locator('.action-menu > summary').click()
        await page.getByRole('button', { name: '检查资料与整理', exact: true }).click()
        await dialog.waitFor()
      }
      await dialog.waitFor()
      assert.equal(await dialog.locator('.ingest-show').count(), 1, 'detail checks only the current show')
      await page.screenshot({ path: path.join(output, `preview-${width}.png`), fullPage: true })
      for (let count = 0; count < 18; count++) {
        await page.keyboard.press(count % 2 ? 'Shift+Tab' : 'Tab')
        assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true, 'Tab stays in the review')
      }
      await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' })
      assert.equal(decision, 'deferred')
      await openManual(); await dialog.getByRole('button', { name: '忽略本次', exact: true }).click()
      await dialog.waitFor({ state: 'hidden' }); assert.equal(decision, 'ignored')
      await Promise.all([page.waitForResponse(response => response.url().endsWith('/api/jobs/tv-ingest/ingest')), page.reload()])
      await page.locator('.show-detail h1').waitFor()
      assert.equal(await dialog.count(), 0, 'same ignored version stays quiet')
      decision = ''; await openManual()
      conflictOnce = true
      await dialog.getByRole('button', { name: '确认并整理', exact: true }).click()
      await dialog.getByRole('alert').filter({ hasText: '文件或资料已变化' }).waitFor()
      assert.equal(await dialog.getByRole('button', { name: '确认并整理', exact: true }).isDisabled(), true)
      await dialog.getByRole('button', { name: '重新预览', exact: true }).click()
      await dialog.getByRole('button', { name: '确认并整理', exact: true }).waitFor({ state: 'visible' })
      const writes = requests.filter(request => request.key === '/api/jobs/tv-organize' && request.method === 'POST').length
      await dialog.getByRole('button', { name: '确认并整理', exact: true }).click()
      await page.getByRole('heading', { name: '剧集整理结果', exact: true }).waitFor()
      assert.equal(requests.filter(request => request.key === '/api/jobs/tv-organize' && request.method === 'POST').length, writes + 1)
      await page.getByRole('button', { name: '完成', exact: true }).click()
      await dialog.waitFor({ state: 'hidden' })
      assert.equal(await page.locator('.action-menu > summary').evaluate(element => element === document.activeElement), true)
      await page.screenshot({ path: path.join(output, `detail-${width}.png`), fullPage: true })
      // A real inline edit holds automatic reviews without a second modal scheduler.
      let release
      const held = new Promise(resolve => { release = resolve })
      await page.route('**/api/jobs/tv-ingest/ingest', async route => { await held; await route.continue() })
      await page.reload(); await page.locator('.action-menu > summary').click()
      await page.getByRole('button', { name: '修改剧名', exact: true }).click()
      release(); await page.waitForResponse(response => response.url().endsWith('/api/jobs/tv-ingest/ingest'))
      await page.getByLabel('剧名', { exact: true }).fill('尚未保存的标题')
      assert.equal(await dialog.count(), 0, 'background results cannot steal focus from an edit')
      await page.locator('.inline-edit').getByRole('button', { name: '取消', exact: true }).click()
      await dialog.waitFor(); await dialog.getByRole('button', { name: '稍后处理', exact: true }).click(); await dialog.waitFor({ state: 'hidden' })
      await page.unroute('**/api/jobs/tv-ingest/ingest')
      decision = ''
      let releaseModal
      const modalHeld = new Promise(resolve => { releaseModal = resolve })
      await page.route('**/api/jobs/tv-ingest/ingest', async route => { await modalHeld; await route.continue() })
      await page.reload(); await page.locator('.action-menu > summary').click()
      await page.getByRole('button', { name: '归属与季号', exact: true }).click()
      const binding = page.getByRole('dialog', { name: '剧集归属与季号', exact: true })
      await binding.waitFor(); releaseModal()
      await page.waitForResponse(response => response.url().endsWith('/api/jobs/tv-ingest/ingest'))
      assert.equal(await dialog.count(), 0, 'an existing modal keeps its focus trap')
      await page.keyboard.press('Escape'); await dialog.waitFor()
      await dialog.getByRole('button', { name: '稍后处理', exact: true }).click(); await dialog.waitFor({ state: 'hidden' })
      await page.unroute('**/api/jobs/tv-ingest/ingest')
      // The settings entry reuses the same batch review and durable decisions.
      await page.goto(base + '/settings?sec=sec-tvorganize&library=2')
      await page.getByRole('button', { name: '检查资料与整理', exact: true }).waitFor()
      if (width === 1440) await page.screenshot({ path: path.join(output, 'tv-organize-panel.png'), fullPage: false })
      await page.getByRole('button', { name: '检查资料与整理', exact: true }).click(); await dialog.waitFor()
      assert.equal(await dialog.locator('.ingest-show').count(), 2, 'library check returns a single batch for both shows')
      await dialog.locator('.ingest-changes > summary').first().click()
      if (width === 1440) await page.screenshot({ path: path.join(output, 'tv-organize-dialog.png'), fullPage: false })
      await dialog.getByRole('button', { name: '稍后处理', exact: true }).click(); await dialog.waitFor({ state: 'hidden' })
      await page.getByLabel('显示已忽略', { exact: true }).check()
      await page.getByRole('button', { name: /查看待办/ }).click(); await dialog.waitFor()
      await dialog.getByRole('button', { name: '稍后处理', exact: true }).click(); await dialog.waitFor({ state: 'hidden' })
      // The upload keeps both successful files in one follow-up request.
      decision = ''; const beforeUploads = requests.length
      await page.goto(base + '/tv?media=1')
      await page.locator('summary').filter({ hasText: '添加剧集' }).click()
      await page.getByRole('button', { name: '上传文件', exact: true }).click()
      const upload = page.getByRole('dialog', { name: '上传剧集', exact: true })
      await upload.getByLabel('多选文件', { exact: true }).check()
      await upload.locator('.tv-target select').selectOption('201')
      await upload.getByLabel('季号', { exact: true }).fill('5')
      await upload.locator('input[type=file]').first().setInputFiles([1, 2].map(number => ({ name: `S05E0${number}.mkv`, mimeType: 'video/x-matroska', buffer: Buffer.from('isolated synthetic media') })))
      await upload.getByRole('button', { name: '开始上传', exact: true }).click(); await dialog.waitFor()
      const uploadJobs = requests.slice(beforeUploads).filter(request => request.key === '/api/jobs/tv-ingest' && request.method === 'POST')
      assert.equal(uploadJobs.length, 1); assert.deepEqual(uploadJobs[0].body.ids, [201])
      assert.equal(await page.getByRole('dialog').count(), 1, 'upload yields its focus trap to the shared review')
      await dialog.getByRole('button', { name: '稍后处理', exact: true }).click(); await dialog.waitFor({ state: 'hidden' })
      await upload.waitFor()
      await upload.getByRole('button', { name: '关闭', exact: true }).waitFor({ state: 'visible' })
      if (width === 1440) await page.screenshot({ path: path.join(output, 'upload-tv.png'), fullPage: false })
      await upload.getByRole('button', { name: '关闭', exact: true }).click()
      // Cancelling the next file still checks files whose responses already succeeded.
      decision = ''; const beforeCancelled = requests.length
      await page.locator('summary').filter({ hasText: '添加剧集' }).click()
      await page.getByRole('button', { name: '上传文件', exact: true }).click()
      await upload.getByLabel('多选文件', { exact: true }).check()
      await upload.locator('.tv-target select').selectOption('201')
      await upload.getByLabel('季号', { exact: true }).fill('5')
      await upload.locator('input[type=file]').first().setInputFiles([3, 4].map(number => ({ name: `S05E0${number}.mkv`, mimeType: 'video/x-matroska', buffer: Buffer.from('isolated synthetic media') })))
      let uploadRequests = 0, secondRequested, releaseUpload
      const requested = new Promise(resolve => { secondRequested = resolve })
      const uploadHeld = new Promise(resolve => { releaseUpload = resolve })
      await page.route('**/api/uploads?*', async route => {
        if (++uploadRequests === 2) { secondRequested(); await uploadHeld; await route.abort().catch(() => {}) }
        else await route.continue()
      })
      await upload.getByRole('button', { name: '开始上传', exact: true }).click()
      await requested; await upload.getByRole('button', { name: '取消上传', exact: true }).click(); releaseUpload()
      await dialog.waitFor()
      const cancelledJobs = requests.slice(beforeCancelled).filter(request => request.key === '/api/jobs/tv-ingest' && request.method === 'POST')
      assert.equal(cancelledJobs.length, 1); assert.deepEqual(cancelledJobs[0].body.ids, [201])
      await dialog.getByRole('button', { name: '稍后处理', exact: true }).click(); await dialog.waitFor({ state: 'hidden' })
      await upload.getByText(/上传已取消：成功 1/).waitFor()
      await upload.getByRole('button', { name: '取消', exact: true }).click()
      await page.unroute('**/api/uploads?*')
      console.log(`PASS ${width}px batch, keyboard, durable decisions, stale snapshot, deferred auto focus, settings and upload`)
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
    await context.close()
  }
  assert.deepEqual(errors, [])
  assert.deepEqual(unexpected, [])
  assert.equal(await sourceFingerprint(), sourceSha256, 'Captured UI sources must remain stable throughout the run')
  assert.equal(await fixtureFingerprint(), fixtureSha256, 'Captured fixtures must remain stable throughout the run')
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ baseline, source_sha256: sourceSha256, fixture_sha256: fixtureSha256,
    artwork_sha256: artworkFingerprint, sources_stable: true, viewports: [1440, 390, 320], requests, errors, unexpected }, null, 2) + '\n')
  console.log(`PASS TV ingestion ${baseline ? 'baseline' : 'UI'}: desktop/390/320; ${output}`)
} finally {
  await browser?.close()
  if (server) await new Promise(resolve => server.close(resolve))
  await rm(temp, { recursive: true, force: true })
}
