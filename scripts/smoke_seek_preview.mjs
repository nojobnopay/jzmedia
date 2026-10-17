// Real Vue seekbar + synthetic sprite sheets. No backend, DB or media access.
// Run: node scripts/smoke_seek_preview.mjs
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-seek-preview-'))
const dist = path.join(work, 'dist')
const artifacts = path.join(root, 'output/playwright/seek-preview')
const errors = [], unexpected = [], checks = []
const entryId = 'virtual:jzmedia-seek-preview'
const source = `
import { createApp, h, ref } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import Seekbar from ${JSON.stringify(path.join(root, 'frontend/src/components/PlayerSeekbar.vue'))};
createApp({ setup() {
  const manifest = ref(null);
  const color = index => [35 + index * 7, 210 - index * 5, 55 + index * 6];
  function setSource(width) {
    const height = width * 9 / 16, pages = [];
    for (let page = 0; page < 2; page++) {
      const canvas = document.createElement('canvas');
      canvas.width = width * 5; canvas.height = height * 5;
      const ctx = canvas.getContext('2d');
      for (let cell = 0; cell < 25; cell++) {
        const x = cell % 5 * width, y = Math.floor(cell / 5) * height;
        ctx.fillStyle = 'rgb(' + color(page * 25 + cell).join(',') + ')';
        ctx.fillRect(x, y, width, height);
        ctx.fillStyle = '#fff'; ctx.font = width / 10 + 'px monospace';
        ctx.fillText('FRAME ' + (page * 25 + cell), x + width / 16, y + height / 4);
        // Fine lines also make visual review of resampling possible.
        for (let line = 0; line < 40; line++) {
          ctx.fillStyle = line % 2 ? '#fff' : '#111';
          ctx.fillRect(x + width / 16 + line * width / 160, y + height * .7, width / 160, height / 8);
        }
      }
      pages.push(canvas.toDataURL('image/png'));
    }
    manifest.value = { state: 'ready', interval: 10, width, height, columns: 5, rows: 5, count: 27, pages };
  }
  window.seekPreviewFixture = { setSource, color, pending: () => { manifest.value = { ...manifest.value, state: 'running', pages: manifest.value.pages.slice(0, 1) }; } };
  setSource(480);
  return () => h('main', { class: 'pv-ctl', style: 'margin:180px 24px 0;padding:16px;background:#202024;color:white' }, [
    h(Seekbar, { duration: 270, value: 0, manifest: manifest.value }),
  ]);
} }).mount('#app');`
let browser, server

try {
  await mkdir(artifacts, { recursive: true })
  const bundle = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: false, cacheDir: path.join(work, 'cache'), logLevel: 'error', plugins: [vue(), {
      name: 'seek-preview-harness', resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null,
    }], build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { harness: entryId } } } })
  const entry = bundle.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const styles = bundle.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css'))
  const html = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<link rel="icon" href="data:,">' + styles.map(item => `<link rel="stylesheet" href="/${item.fileName}">`).join('')
    + `</head><body style="margin:0;background:#141414"><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`
  server = createServer(async (req, res) => {
    const pathname = new URL(req.url, 'http://127.0.0.1').pathname
    if (pathname === '/') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(html); return }
    if (!/^\/assets\/[\w.-]+\.(?:css|js)$/.test(pathname)) { res.writeHead(404); res.end(); return }
    try {
      res.writeHead(200, { 'Content-Type': pathname.endsWith('.css') ? 'text/css' : 'text/javascript' })
      res.end(await readFile(path.join(dist, pathname)))
    } catch { res.writeHead(404); res.end() }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const dpr of [1, 1.25, 1.5, 2, 3]) {
    const context = await browser.newContext({ viewport: { width: 900, height: 500 }, deviceScaleFactor: dpr, serviceWorkers: 'block' })
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { unexpected.push(route.request().url()); return route.abort() }
      return route.continue()
    })
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(String(error)))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    await page.goto(base)
    await page.getByRole('slider', { name: '播放进度' }).waitFor()
    assert.equal(await page.evaluate(() => devicePixelRatio), dpr)
    for (const sourceWidth of [160, 320, 480]) {
      await page.evaluate(width => window.seekPreviewFixture.setSource(width), sourceWidth)
      for (let index = 0; index < 27; index++) {
        await page.getByRole('slider').evaluate((element, time) => {
          element.value = time; element.dispatchEvent(new Event('input', { bubbles: true }))
        }, index * 10 + 1)
        const image = page.locator('.preview-image')
        await image.waitFor()
        const metrics = await image.evaluate(async element => {
          const style = getComputedStyle(element), rect = element.getBoundingClientRect()
          const source = new Image(); source.src = style.backgroundImage.slice(5, -2); await source.decode()
          return { width: rect.width, height: rect.height, position: style.backgroundPosition,
            size: style.backgroundSize, sourceWidth: source.naturalWidth, sourceHeight: source.naturalHeight }
        })
        const cell = index % 25
        assert.deepEqual(metrics, { width: 160, height: 90,
          position: `${-(cell % 5) * 160}px ${-Math.floor(cell / 5) * 90}px`, size: '800px 450px',
          sourceWidth: sourceWidth * 5, sourceHeight: sourceWidth * 9 / 16 * 5 }, `DPR ${dpr}, source ${sourceWidth}, frame ${index}`)
        // Sample the actual rendered crop: catches wrong rows/pages even when the
        // CSS viewport dimensions look correct.
        const shot = await image.screenshot({ animations: 'disabled' })
        const sampled = await page.evaluate(async url => {
          const image = new Image(); image.src = url; await image.decode()
          const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height
          const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0)
          return { width: image.width, height: image.height, color: Array.from(ctx.getImageData(Math.floor(image.width / 2), Math.floor(image.height / 2), 1, 1).data).slice(0, 3) }
        }, 'data:image/png;base64,' + shot.toString('base64'))
        // Playwright rounds a fractional CSS crop outwards before DPR scaling.
        assert.ok(Math.abs(sampled.width - 160 * dpr) <= Math.ceil(dpr) && Math.abs(sampled.height - 90 * dpr) <= Math.ceil(dpr),
          `DPR ${dpr}, source ${sourceWidth}, frame ${index}: screenshot ${JSON.stringify(sampled)}`)
        assert.deepEqual(sampled.color, await page.evaluate(i => window.seekPreviewFixture.color(i), index))
        if (index === 24) await writeFile(path.join(artifacts, `source-${sourceWidth}-dpr-${dpr}.png`), shot)
      }
      await page.evaluate(() => window.seekPreviewFixture.pending())
      await page.locator('.preview small').getByText('预览生成中…', { exact: true }).waitFor()
      assert.equal(await page.locator('.preview-image').count(), 0, 'Pending pages never display a previous frame')
      checks.push({ dpr, sourceWidth, frames: 27, cssSize: '160×90', pendingPage: 'passed' })
    }
    // Hover uses the same sprite scaling as dragging, including near the end.
    await page.evaluate(() => window.seekPreviewFixture.setSource(480))
    const rail = await page.getByRole('slider').boundingBox()
    await page.mouse.move(rail.x + rail.width - 8, rail.y + rail.height / 2)
    await page.locator('.preview-image').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    const before = await page.locator('.preview-image').evaluate(element => ({
      image: element.style.backgroundImage, size: getComputedStyle(element).backgroundSize,
      width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height,
    }))
    const movedDpr = dpr === 1 ? 2 : 1
    const cdp = await context.newCDPSession(page)
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 900, height: 500, deviceScaleFactor: movedDpr, mobile: false })
    await page.waitForFunction(expected => devicePixelRatio === expected, movedDpr)
    assert.deepEqual(await page.locator('.preview-image').evaluate(element => ({
      image: element.style.backgroundImage, size: getComputedStyle(element).backgroundSize,
      width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height,
    })), before, 'Moving to another screen preserves the sprite and CSS size without reloading the manifest')
    checks.push({ crossMonitor: { from: dpr, to: movedDpr }, cssSizeAndImagePreserved: true })
    await context.close()
    console.log(`PASS DPR ${dpr}: 160/320/480px sources, 27 frames across all rows/pages, fixed CSS size and pending page`)
  }
  assert.deepEqual(errors, [], 'No browser errors')
  assert.deepEqual(unexpected, [], 'No external requests')
  await writeFile(path.join(artifacts, 'manifest.json'), JSON.stringify({ fixture: 'Synthetic canvas sprites, real PlayerSeekbar.vue; no backend or media', checks }, null, 2) + '\n')
} finally {
  if (browser) await browser.close()
  if (server?.listening) await new Promise(resolve => server.close(resolve))
  await rm(work, { recursive: true, force: true })
}
