// Real Vue component + isolated in-memory API. Does not open backend, DB or media.
// Run: node scripts/smoke_preview_queue_ui.mjs [--capture-baseline]
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { createServer } from 'node:http'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const baseline = process.argv.includes('--capture-baseline')
assert.ok(process.argv.slice(2).every(arg => arg === '--capture-baseline'))
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-preview-queue-ui-'))
const report = path.join(root, 'output/playwright/preview-queue')
const checks = [], failures = [], requests = [], released = []
const sample = index => ({ kind: 'movie', id: 200 + index, library_id: 1,
  title: index === 0 ? '超长片名用于检查队列在手机上的排版与按钮定位'.repeat(4) : `待处理样本 ${index + 1}`,
  file_name: index === 0 ? 'An.Extremely.Long.Filename.For.The.Isolated.Preview.Queue.Fixture.'.repeat(5) + 'mkv' : `sample-${index + 1}.mkv` })
const runningSample = () => ({ kind: 'movie', id: 91, library_id: 1, title: '当前处理样本', file_name: 'running.mkv',
  job_id: 'manual-fixture', auto: false, waiting: false, frames: 12, frame_total: 100, done: 0, total: 4 })
let pending = [sample(0), sample(1), sample(2)], running = runningSample()
let job = { state: 'running', job_id: 'manual-fixture', done: 0, total: 4 }
let queueFailure = false, holdQueue = null, holdMutation = null, browser, server
const revision = items => createHash('sha256').update(JSON.stringify(items.map(item => [item.kind, item.id]))).digest('hex').slice(0, 16)
function gate() {
  let release, seen
  const result = { promise: new Promise(resolve => { release = resolve }), seen: new Promise(resolve => { seen = resolve }) }
  result.release = release; result.notify = seen; released.push(release)
  return result
}
const harness = `
import {createApp, h, ref} from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import PreviewMaintenance from ${JSON.stringify(path.join(root, 'frontend/src/components/PreviewMaintenance.vue'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/settings.css'))};
createApp({setup(){const libraryId=ref(1),active=ref(true);
window.previewHarness={setLibrary:id=>libraryId.value=id,setActive:value=>active.value=value};
return()=>h('main',{class:'settings-layout',style:'display:block;max-width:1040px'},[
h('div',{class:'settings-main'},[h('h2','资料维护'),h(PreviewMaintenance,{libraryId:libraryId.value,active:active.value,librarySource:'local',libraryEnabled:true})])]);
}}).mount('#app');`

async function screenshot(page, width, phase) {
  await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 })
  await page.screenshot({ path: path.join(report, `${phase}-${width}.png`), fullPage: true })
}

try {
  await mkdir(report, { recursive: true })
  const bundle = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    cacheDir: path.join(work, 'cache'), logLevel: 'error', plugins: [vue(), {
      name: 'preview-queue-harness', resolveId: id => id === 'virtual:preview-queue' ? '\0preview-queue' : null,
      load: id => id === '\0preview-queue' ? harness : null,
    }], build: { outDir: path.join(work, 'dist'), emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { harness: 'virtual:preview-queue' } } } })
  const entry = bundle.output.find(item => item.type === 'chunk' && item.isEntry)
  const css = bundle.output.filter(item => item.fileName.endsWith('.css'))
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost')
      if (!url.pathname.startsWith('/api/')) {
        if (!url.pathname.startsWith('/assets/')) {
          res.writeHead(200, { 'Content-Type': 'text/html' })
          res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">' +
            css.map(item => `<link rel="stylesheet" href="/${item.fileName}">`).join('') +
            `</head><body><div id="app"></div><script type="module" src="/${entry.fileName}"></script></body></html>`)
        } else {
          const data = await readFile(path.join(work, 'dist', url.pathname))
          res.writeHead(200, { 'Content-Type': url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript' }); res.end(data)
        }
        return
      }
      const chunks = []; for await (const chunk of req) chunks.push(chunk)
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null
      const key = url.pathname, method = req.method
      requests.push({ key, method, body, query: Object.fromEntries(url.searchParams) })
      let response = {}, status = 200
      if (key === '/api/stream/previews/jobs/latest') response = job
      else if (key === '/api/stream/previews/auto') response = { mode: 'all', queued: pending.length, running: false,
        start_hour: 3, end_hour: 6, pause_reason: 'scheduled' }
      else if (key === '/api/stream/previews/queue' && method === 'GET') {
        const libraryId = Number(url.searchParams.get('library_id'))
        const items = pending.filter(item => item.library_id === libraryId), offset = Number(url.searchParams.get('offset'))
        response = { items: items.slice(offset, offset + 50), total: items.length, revision: revision(items),
          running: running?.library_id === libraryId ? running : null }
        if (queueFailure) { queueFailure = false; status = 503; response = { detail: '模拟队列暂时不可用' } }
        if (holdQueue) { const current = holdQueue; holdQueue = null; current.notify(); await current.promise }
      } else if (key === '/api/stream/previews/queue/remove' && method === 'POST') {
        if (holdMutation) { const current = holdMutation; holdMutation = null; current.notify(); await current.promise }
        const items = pending.filter(item => item.library_id === body.library_id)
        if (body.revision !== revision(items)) { status = 409; response = { detail: '待处理队列已变化' } }
        else {
          const keyOf = item => `${item.kind}:${item.id}`
          const chosen = new Set((body.all_pending ? body.exclude : body.items).map(keyOf))
          const before = pending.length
          pending = pending.filter(item => item.library_id !== body.library_id ||
            (body.all_pending ? chosen.has(keyOf(item)) : !chosen.has(keyOf(item))))
          response = { removed: before - pending.length }
        }
      } else if (/\/queue\/movie\/\d+$/.test(key) && method === 'DELETE') {
        const before = pending.length
        pending = pending.filter(item => item.id !== Number(key.split('/').at(-1)))
        response = { removed: pending.length !== before }
      } else throw new Error(`Unexpected fixture request: ${method} ${key}`)
      res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(response))
    } catch (error) { failures.push(String(error)); res.writeHead(500); res.end('Fixture error') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  page.setDefaultTimeout(12000)
  page.on('pageerror', error => failures.push(String(error)))
  await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  await page.goto(base)
  const preview = page.getByRole('region', { name: '播放进度缩略图' })
  await preview.locator('.queue-list li').nth(2).waitFor()
  for (const width of [1440, 390, 375]) {
    await screenshot(page, width, baseline ? 'before' : 'after')
    if (baseline) continue
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${width}px has no horizontal overflow`)
    const row = preview.locator('.queue-list li').first()
    const checkbox = await row.locator('.select-item').boundingBox(), action = await row.getByRole('button').boundingBox()
    assert.ok(checkbox.width >= 44 && checkbox.height >= 44, `${width}px checkbox target`)
    if (width <= 700) assert.ok(action.width >= 44 && action.height >= 44, `${width}px action target`)
    assert.ok((await row.boundingBox()).height <= 88, `${width}px long text keeps a compact row`)
    assert.equal(await row.getByRole('button').textContent(), '', 'remove uses the shared icon only')
    assert.match(await row.getByRole('button').getAttribute('aria-label'), /从待处理队列移除/)
    assert.equal(await row.locator('strong').getAttribute('title'), sample(0).title)
  }
  if (!baseline) {
    checks.push('1440/390/375px compact long text, icon names, touch targets and no overflow')
    await page.setViewportSize({ width: 390, height: 844 })
    const firstAction = preview.locator('.queue-list li').first().getByRole('button')
    await firstAction.focus(); await page.keyboard.press('Enter')
    await preview.getByText(`已将“${sample(0).title}”移出队列。`, { exact: true }).waitFor()
    assert.equal(await preview.locator('.queue-list li').count(), 2)
    checks.push('keyboard activation removes a pending item and refreshes the count')

    // A queue can continue changing even if latest job/running are momentarily idle.
    running = null; job = { state: 'idle' }
    await page.reload(); await preview.locator('.queue-list li').nth(1).waitFor()
    pending = [sample(2)]
    await preview.locator('.queue-list li').nth(1).waitFor({ state: 'detached', timeout: 6000 })
    await preview.getByText('待处理样本 3', { exact: true }).waitFor()
    checks.push('pending queue keeps polling through idle job transitions and removes completed entries')

    // Hold a poll snapshot, then begin removal. It must be aborted and no new poll
    // may replace the selection while the removal request is unresolved.
    pending = [sample(0), sample(1), sample(2)]; running = runningSample(); job = { state: 'running', total: 4, done: 0 }
    await page.reload(); await preview.locator('.queue-list li').nth(2).waitFor()
    const pollGate = gate(); holdQueue = pollGate
    await pollGate.seen
    await preview.getByRole('checkbox', { name: '全选本库待处理项' }).check()
    const mutationGate = gate(); holdMutation = mutationGate
    const abortedPoll = page.waitForEvent('requestfailed', { predicate: request => request.method() === 'GET' &&
      new URL(request.url()).pathname === '/api/stream/previews/queue' })
    await preview.getByRole('button', { name: '批量移出选中项' }).click()
    await mutationGate.seen
    await abortedPoll
    pollGate.release()
    const pollCount = requests.filter(request => request.method === 'GET' && request.key === '/api/stream/previews/queue').length
    await page.waitForTimeout(2800)
    assert.equal(requests.filter(request => request.method === 'GET' && request.key === '/api/stream/previews/queue').length, pollCount)
    assert.equal(await preview.getByRole('checkbox', { name: '全选本库待处理项' }).isDisabled(), true)
    const removal = requests.filter(request => request.key === '/api/stream/previews/queue/remove').at(-1).body
    assert.equal(removal.all_pending, true); assert.equal(removal.revision, revision(pending))
    mutationGate.release()
    await preview.getByText('已移出 3 项待处理影片。', { exact: true }).waitFor()
    await preview.getByText('当前视频库没有待处理影片。', { exact: true }).waitFor()
    await preview.getByText('当前处理样本', { exact: true }).waitFor()
    running = null; job = { state: 'done', total: 1, done: 1 }
    await preview.getByText('当前视频库没有正在处理的预览影片。', { exact: true }).waitFor()
    checks.push('bulk removal aborts stale polling, pauses refresh while busy and preserves the current film')

    pending = Array.from({ length: 52 }, (_, index) => sample(index))
    await page.reload(); await preview.locator('.queue-list li').nth(49).waitFor()
    await preview.getByRole('checkbox', { name: '全选本库待处理项' }).check()
    await preview.getByRole('button', { name: '下一页', exact: true }).click()
    await preview.getByRole('checkbox', { name: '选择 待处理样本 52', exact: true }).uncheck()
    await preview.getByText('已选 51 项', { exact: true }).waitFor()
    await preview.getByRole('button', { name: '批量移出选中项' }).click()
    await preview.getByText('已移出 51 项待处理影片。', { exact: true }).waitFor()
    await preview.getByText('待处理样本 52', { exact: true }).waitFor()
    assert.equal(await preview.locator('.queue-list li').count(), 1)
    checks.push('cross-page select all/exclusion and shrinking-page correction')

    await preview.getByRole('checkbox', { name: '选择 待处理样本 52', exact: true }).check()
    pending.push(sample(52))
    await preview.getByRole('button', { name: '批量移出选中项' }).click()
    await preview.getByText('待处理队列已变化，请重新选择。', { exact: true }).waitFor()
    await preview.getByText('已选 0 项', { exact: true }).waitFor()
    assert.equal(await preview.locator('.queue-list li').count(), 2)
    assert.equal(pending.length, 2)
    await preview.getByRole('checkbox', { name: '选择 待处理样本 53', exact: true }).check()
    await preview.getByRole('button', { name: '批量移出选中项' }).click()
    await preview.getByText('已移出 1 项待处理影片。', { exact: true }).waitFor()
    assert.equal(await preview.locator('.queue-list li').count(), 1)
    assert.equal(pending[0].id, 251)
    checks.push('HTTP 409 conflicts reload, clear outdated selection and allow a fresh removal')

    queueFailure = true
    await page.reload()
    await preview.getByRole('alert').filter({ hasText: '模拟队列暂时不可用' }).waitFor()
    assert.equal(await preview.getByText('当前视频库没有待处理影片。', { exact: true }).count(), 0)
    await preview.getByRole('button', { name: '重试', exact: true }).click()
    await preview.getByText('待处理样本 52', { exact: true }).waitFor()
    await page.evaluate(() => window.previewHarness.setLibrary(2))
    await preview.getByText('当前视频库没有待处理影片。', { exact: true }).waitFor()
    assert.equal(await preview.getByText('待处理样本 52', { exact: true }).count(), 0)
    checks.push('initial errors stay distinct from empty state, retry and library scope refresh')
    assert.deepEqual(failures, [])
    await writeFile(path.join(report, 'checks.json'), JSON.stringify({ mocked: true, realVue: true, checks }, null, 2) + '\n')
    console.log('PASS preview queue UI: ' + checks.join('; '))
  } else console.log('Captured preview queue UI baseline at 1440/390/375px')
} catch (error) {
  if (browser) await browser.contexts()[0]?.pages()[0]?.screenshot({ path: path.join(report, 'failure.png'), fullPage: true }).catch(() => {})
  throw error
} finally {
  released.forEach(release => release())
  await browser?.close()
  await new Promise(resolve => server ? server.close(resolve) : resolve())
  await rm(work, { recursive: true, force: true })
}
