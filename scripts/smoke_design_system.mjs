// Isolated, real Vue component catalogue. No backend, .env, database or media access.
// Run: node scripts/smoke_design_system.mjs [--capture | --demo]
// --capture saves screenshots under output/playwright/design-system; --demo serves a preview.
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
const args = process.argv.slice(2)
assert.ok(args.length <= 1 && args.every(arg => ['--capture', '--demo'].includes(arg)),
  'Usage: node scripts/smoke_design_system.mjs [--capture | --demo]')
const demo = args.includes('--demo'), capture = args.includes('--capture')
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-design-system-'))
const dist = path.join(work, 'dist')
const artifacts = path.join(root, 'output/playwright/design-system')
const entryId = 'virtual:jzmedia-design-system'
const source = `
import { createApp } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import Preview from ${JSON.stringify(path.join(root, 'scripts/fixtures/DesignSystemPreview.vue'))};
createApp(Preview).mount('#app');`
const errors = [], unexpected = [], checks = []
let browser, server

async function checkBounds(page, label) {
  const bounds = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }))
  assert.ok(bounds.scroll <= bounds.width + 1, `${label}: horizontal overflow ${JSON.stringify(bounds)}`)
}

async function checkTouchTargets(buttons) {
  const small = await buttons.evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect()
    return { name: element.textContent.trim() || element.getAttribute('aria-label'), width: rect.width, height: rect.height }
  }).filter(rect => rect.width < 43.5 || rect.height < 43.5))
  assert.deepEqual(small, [], 'Mobile buttons, including compact and icon controls, need 44px targets')
}

try {
  const result = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: false, cacheDir: path.join(work, 'cache'), logLevel: 'error',
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js') } },
    plugins: [vue(), { name: 'design-system-preview',
      resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null }],
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { catalogue: entryId } } } })
  const entry = result.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const styles = result.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item => item.fileName)
  const html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    + '<title>jzmedia · 组件预览</title><link rel="icon" href="data:,">'
    + styles.map(file => `<link rel="stylesheet" href="/${file}">`).join('')
    + `</head><body><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`
  server = createServer(async (req, res) => {
    res.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'none'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; object-src 'none'; base-uri 'none'")
    const pathname = new URL(req.url, 'http://127.0.0.1').pathname
    if (req.method !== 'GET') { res.writeHead(405); res.end(); return }
    if (pathname === '/') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html); return }
    if (!/^\/assets\/[\w.-]+\.(?:js|css)$/.test(pathname)) { res.writeHead(404); res.end('No fixture at this path'); return }
    try {
      const content = await readFile(path.join(dist, pathname))
      res.writeHead(200, { 'Content-Type': pathname.endsWith('.js') ? 'text/javascript' : 'text/css' }); res.end(content)
    } catch { res.writeHead(404); res.end('Missing preview asset') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  if (demo) {
    console.log(`组件预览：${base}/`)
    console.log('真实 Vue 组件，独立演示状态；不加载 .env、不连接后端或媒体。Ctrl+C 停止。')
    await new Promise(resolve => { process.once('SIGINT', resolve); process.once('SIGTERM', resolve) })
  } else {
    browser = await chromium.launch({ headless: true })
    if (capture) await mkdir(artifacts, { recursive: true })
    for (const width of [1440, 390, 375]) {
      const context = await browser.newContext({ viewport: { width, height: width > 700 ? 1000 : 844 },
        hasTouch: width <= 700, serviceWorkers: 'block' })
      await context.route('**/*', route => {
        const url = new URL(route.request().url())
        if (url.origin !== base) { unexpected.push(url.href); return route.abort() }
        return route.continue()
      })
      const page = await context.newPage()
      page.setDefaultTimeout(10000)
      page.on('pageerror', error => errors.push(String(error)))
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
      await page.goto(base)
      await page.getByRole('heading', { name: '让每一次操作都有一致的体验' }).waitFor()
      await page.evaluate(() => document.fonts.ready)
      await checkBounds(page, `${width}px catalogue`)
      assert.equal(await page.getByRole('button', { name: '暂不可用', exact: true }).isDisabled(), true)
      assert.equal(await page.getByRole('button', { name: '正在保存', exact: true }).isDisabled(), true)
      if (width <= 700) {
        await checkTouchTargets(page.locator('[data-touch-check] button'))
      }
      await page.getByLabel('视频库名称', { exact: true }).fill('我的演示视频库')
      assert.equal(await page.getByLabel('视频库名称', { exact: true }).getAttribute('aria-describedby'), 'library-name-description')
      assert.equal(await page.getByLabel('目录路径（错误示例）', { exact: true }).getAttribute('aria-invalid'), 'true')
      assert.equal(await page.getByLabel('目录路径（错误示例）', { exact: true }).getAttribute('aria-describedby'), 'invalid-example-description')
      await page.getByRole('button', { name: '保存演示表单', exact: true }).click()
      await page.getByText('演示设置已保存，仅在当前页面生效。', { exact: true }).waitFor()
      if (capture) await page.screenshot({ path: path.join(artifacts, `catalogue-${width}.png`), fullPage: true, animations: 'disabled' })

      const trigger = page.locator('#dialog-trigger')
      await trigger.click()
      const dialog = page.getByRole('dialog', { name: '确认演示设置' })
      await dialog.waitFor()
      if (width <= 700) await checkTouchTargets(dialog.locator('button'))
      assert.equal(await dialog.evaluate(element => !document.querySelector('#app').contains(element)), true, 'Dialog must teleport outside the preview root')
      assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true, 'Opening a dialog moves keyboard focus inside')
      const primaryColor = await page.locator('#primary-sample').evaluate(element => getComputedStyle(element).backgroundColor)
      assert.equal(await page.locator('#dialog-primary').evaluate(element => getComputedStyle(element).backgroundColor), primaryColor,
        'Primary action styling must survive Teleport without a settings ancestor')
      assert.notEqual(primaryColor, 'rgba(0, 0, 0, 0)', 'Primary button must have a visible fill')
      await page.locator('#dialog-primary').focus()
      await page.keyboard.press('Tab')
      assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true, 'Tab stays within dialog')
      await checkBounds(page, `${width}px dialog`)
      if (capture) await page.screenshot({ path: path.join(artifacts, `dialog-${width}.png`), animations: 'disabled' })
      const nestedTrigger = page.locator('#nested-trigger')
      await nestedTrigger.click()
      const child = page.getByRole('dialog', { name: '选择演示目录', exact: true })
      await child.waitFor()
      await child.getByRole('button', { name: '使用这个目录' }).focus()
      await page.keyboard.press('Tab')
      assert.equal(await child.evaluate(element => element.contains(document.activeElement)), true, 'Nested dialog owns the focus trap')
      await page.keyboard.press('Escape')
      await child.waitFor({ state: 'hidden' })
      assert.equal(await dialog.isVisible(), true, 'Escape closes only the topmost dialog')
      assert.equal(await nestedTrigger.evaluate(element => document.activeElement === element), true, 'Closing child returns focus to its parent trigger')
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'hidden' })
      assert.equal(await trigger.evaluate(element => document.activeElement === element), true, 'Escape returns focus to the original trigger')

      await page.locator('#busy-trigger').click()
      await dialog.waitFor()
      await page.keyboard.press('Escape')
      assert.equal(await dialog.isVisible(), true, 'An active task cannot be closed with Escape')
      await page.mouse.click(2, 2)
      assert.equal(await dialog.isVisible(), true, 'An active task cannot be closed through the backdrop')
      assert.equal(await page.locator('#dialog-primary').isDisabled(), true)
      await page.locator('#finish-busy').click()
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'hidden' })
      checks.push({ width, passed: ['layout', 'button states', 'form', 'teleport styles', 'focus trap', 'focus return', 'nested dialogs', 'busy guard', ...(width <= 700 ? ['44px touch targets'] : [])] })
      console.log(`PASS ${width}px 组件状态、表单、弹窗 Teleport 样式、焦点与执行中关闭保护`)
      await context.close()
    }
    assert.deepEqual(errors, [], 'No browser runtime/console errors')
    assert.deepEqual(unexpected, [], 'No external requests')
    if (capture) {
      await writeFile(path.join(artifacts, 'manifest.json'), JSON.stringify({ date: new Date().toISOString(),
        source: 'Real Vue components, independent in-memory demo, no backend or media', checks }, null, 2) + '\n')
      console.log(`截图：${artifacts}`)
    }
  }
} finally {
  if (browser) await browser.close()
  if (server?.listening) await new Promise(resolve => server.close(resolve))
  await rm(work, { recursive: true, force: true })
}
