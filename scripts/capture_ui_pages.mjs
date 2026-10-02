// Reproducible page screenshots against the existing isolated Vue/API demo.
// Run: node scripts/capture_ui_pages.mjs [--label ui-after]
// No real backend, media, .env or model service is opened.
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
assert.ok(!args.length || (args.length === 2 && args[0] === '--label' && /^[a-z0-9-]+$/.test(args[1])),
  'Usage: node scripts/capture_ui_pages.mjs [--label ui-after]')
const label = args[1] || 'ui-after', output = path.join(root, 'output/playwright', label)
const demo = spawn(process.execPath, [path.join(root, 'scripts/smoke_ai_ui.mjs'), '--demo'],
  { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
const results = [], unexpected = []
let browser, startupLog = ''
demo.stderr.on('data', data => { startupLog = (startupLog + data).slice(-10000) })

try {
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Isolated demo did not start within 60 seconds\n' + startupLog)), 60000)
    demo.once('error', error => { clearTimeout(timer); reject(error) })
    demo.once('exit', code => { clearTimeout(timer); reject(new Error(`Isolated demo exited (${code})\n${startupLog}`)) })
    demo.stdout.on('data', data => {
      startupLog = (startupLog + data).slice(-10000)
      const url = startupLog.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0]
      if (url) { clearTimeout(timer); resolve(url) }
    })
  })
  await mkdir(output, { recursive: true })
  browser = await chromium.launch({ headless: true })
  for (const [name, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
    await context.route('**/*', route => {
      const url = new URL(route.request().url())
      if (url.origin !== base) { unexpected.push(route.request().url()); return route.abort() }
      return route.continue()
    })
    const page = await context.newPage()
    page.setDefaultTimeout(15000)
    let errors = []
    page.on('pageerror', error => errors.push(String(error)))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${new URL(response.url()).pathname}`) })
    const scenes = [['movie-wall', '/?media=1'], ['movie-detail', '/m/101'], ['show-detail', '/tv/201'],
      ['settings', '/settings?sec=sec-tmdb'], ['collections', null]]
    for (const [scene, route] of scenes) {
      errors = []
      if (route) await page.goto(base + route)
      else await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '合集', exact: true }).click()
      await page.getByRole('combobox', { name: '切换媒体库' }).waitFor()
      await page.waitForLoadState('networkidle')
      await page.evaluate(() => document.fonts.ready)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
      const file = `${scene}-${name}.png`
      await page.screenshot({ path: path.join(output, file), fullPage: true, animations: 'disabled' })
      results.push({ scene, viewport, path: file, overflow, errors: [...errors] })
      assert.equal(overflow, false, `${scene} at ${viewport.width}px overflows horizontally`)
      assert.deepEqual(errors, [], `${scene} at ${viewport.width}px has browser errors`)
      if (scene === 'movie-wall' || scene === 'settings') {
        const selector = scene === 'movie-wall' ? '.browse-toolbar input, .browse-toolbar > button' : '#tmdb-read-token, .tmdb-settings .bar button'
        const controls = await page.locator(selector).evaluateAll(elements => elements.map(element => ({
          name: element.getAttribute('aria-label') || element.textContent.trim() || element.id,
          height: element.getBoundingClientRect().height,
        })))
        const expected = viewport.width <= 700 ? 44 : 40
        results.at(-1).controls = controls
        assert.ok(controls.length >= 3, 'Expected the actual search or TMDB form controls')
        assert.ok(controls.every(control => Math.abs(control.height - expected) < 1),
          `${scene} controls should align at ${expected}px: ${JSON.stringify(controls)}`)
        if (scene === 'movie-wall' && viewport.width <= 700) {
          const widths = await page.locator('.browse-toolbar').evaluate(element => ({
            toolbar: element.getBoundingClientRect().width,
            search: element.querySelector('.q-wrap').getBoundingClientRect().width,
          }))
          results.at(-1).searchWidths = widths
          assert.ok(widths.search >= widths.toolbar - 1, `Mobile search input should occupy its own full row: ${JSON.stringify(widths)}`)
        }
      }
      console.log(`PASS ${scene} ${viewport.width}px → ${file}`)
    }
    await context.close()
  }
  assert.deepEqual(unexpected, [], 'No external requests are permitted')
  await writeFile(path.join(output, 'manifest.json'), JSON.stringify({
    source: 'Real Vue pages from current working tree; smoke_ai_ui.mjs --demo; all API mocked',
    source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    app_version: JSON.parse(await readFile(path.join(root, 'frontend/package.json'), 'utf8')).version,
    date: new Date().toISOString(), script: 'scripts/capture_ui_pages.mjs', results,
  }, null, 2) + '\n')
  console.log(`页面截图：${output}`)
} finally {
  if (browser) await browser.close()
  if (demo.exitCode === null && demo.signalCode === null) {
    const stopped = new Promise(resolve => demo.once('exit', resolve))
    demo.kill('SIGTERM')
    const timer = setTimeout(() => demo.kill('SIGKILL'), 5000)
    await stopped
    clearTimeout(timer)
  }
}
