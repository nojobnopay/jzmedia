import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { publicPages, htmlPath } from '../.vitepress/public-pages.mjs'
import { createHelpServer } from './server.mjs'

const argument = process.argv.indexOf('--url')
let server
let browser
try {
  let base = argument >= 0 ? process.argv[argument + 1] : null
  if (!base) {
    server = createHelpServer()
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
    base = `http://127.0.0.1:${server.address().port}/help/`
  }
  if (!base.endsWith('/')) base += '/'
  const origin = new URL(base).origin
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const unexpected = []
  const errors = []
  const mediaRequests = []
  await context.route('**/*', route => {
    const url = route.request().url()
    if (/^https?:/.test(url) && new URL(url).origin !== origin) { unexpected.push(url); return route.abort() }
    if (/\.mp4(?:\?|$)/.test(url)) mediaRequests.push(url)
    return route.continue()
  })
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(String(error)))
  page.on('console', message => { if (message.type() === 'error') errors.push(`${page.url()}: ${message.text()}`) })
  const paths = publicPages().map(htmlPath)
  for (const file of paths) {
    const response = await page.goto(new URL(file, base).href)
    assert.equal(response.status(), 200, file)
    await page.locator('#VPContent').waitFor()
    await page.waitForFunction(() => !!document.querySelector('#app')?.__vue_app__)
    await page.locator('img').evaluateAll(images => images.forEach(img => { img.loading = 'eager' }))
    await page.waitForFunction(() => [...document.images].every(img => img.complete), { timeout: 15000 })
    assert.deepEqual(await page.locator('img').evaluateAll(images => images.filter(img => !img.naturalWidth).map(img => img.src)), [], `${file}: broken image`)
    assert.ok(await page.locator('#VPContent').innerText(), `${file}: no readable content`)
    assert.equal(await page.locator('video').evaluateAll(videos => videos.some(video => video.autoplay || video.preload !== 'none')), false)
  }
  assert.deepEqual(mediaRequests, [], '视频不能在用户播放前预加载')
  await page.goto(new URL('user-guide/onboarding.html', base).href)
  await page.waitForFunction(() => !!document.querySelector('#app')?.__vue_app__)
  const figure = page.locator('.doc-figure-open:visible').first()
  await figure.focus()
  await page.keyboard.press('Enter')
  await page.locator('dialog[open]').waitFor()
  assert.equal(await page.locator('dialog[open] a').textContent(), '查看原图 ↗')
  await page.getByRole('button', { name: '放大细节', exact: true }).click()
  assert.ok(await page.locator('.doc-image-scroll').first().evaluate(el => el.scrollWidth > el.clientWidth), '细节模式应允许滚动查看局部')
  await page.getByRole('button', { name: '适合窗口', exact: true }).click()
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => !document.querySelector('dialog[open]'))
  assert.ok(await figure.evaluate(el => el === document.activeElement), '关闭图片应恢复焦点')
  for (const query of ['添加电影', '字幕延迟', '没有声音', 'NAS']) {
    await page.getByRole('button', { name: '搜索帮助', exact: true }).click()
    const input = page.locator('#localsearch-input')
    await input.fill(query)
    await page.locator('.VPLocalSearchBox .result').first().waitFor()
    assert.ok(await page.locator('.VPLocalSearchBox .result').count(), `${query}: 无搜索结果`)
    await page.keyboard.press('Escape')
  }
  await page.setViewportSize({ width: 390, height: 844 })
  for (const file of ['index.html', 'user-guide/onboarding.html', 'user-guide/subtitles.html', 'user-guide/troubleshooting.html']) {
    await page.goto(new URL(file, base).href)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    assert.ok(overflow <= 1, `${file}: 移动视口横向溢出 ${overflow}px`)
  }
  const missing = await page.goto(new URL('not-a-doc.html', base).href)
  assert.equal(missing.status(), 404)
  await page.goto(new URL('assets/previews/onboarding/index.html', base).href)
  await page.waitForURL(new URL('user-guide/onboarding.html', base).href)
  const noJs = await browser.newContext({ javaScriptEnabled: false })
  const plain = await noJs.newPage()
  await plain.goto(new URL('user-guide/onboarding.html', base).href)
  assert.match(await plain.locator('#VPContent').innerText(), /扫描|资料来源/)
  await noJs.close()
  assert.deepEqual(unexpected, [], '帮助站出现外部网络请求')
  // The intentional missing-page probe produces a browser 404 console entry.
  assert.deepEqual(errors.filter(error => !error.includes('404')), [], '浏览器脚本或 CSP 错误')
  console.log(`浏览器检查通过：${paths.length} 页，桌面/390px、图片放大与焦点、四组中英文搜索、离线资源、无 JS 正文与视频延迟加载。`)
} finally {
  await browser?.close()
  if (server) await new Promise(resolve => server.close(resolve))
}
