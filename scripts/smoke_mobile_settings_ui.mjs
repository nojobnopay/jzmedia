// Mobile settings interactions against the isolated ui_visual_review.mjs --demo.
// Usage: node scripts/smoke_mobile_settings_ui.mjs --base http://127.0.0.1:PORT
// All non-GET requests are blocked except locally fulfilled AI-save fixtures.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
assert.equal(process.argv[2], '--base', 'Start the isolated ui_visual_review demo and supply --base')
const address = new URL(process.argv[3])
assert.ok(address.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname), 'Only a local isolated UI demo is supported')
const base = address.origin
const output = path.join(root, 'output/playwright/mobile-settings-after')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const results = [], errors = []
let currentPage
async function visibleActions(page, selector, name, width) {
  await page.evaluate(() => scrollTo(0, 0))
  const actions = page.locator(selector)
  await actions.waitFor()
  const bounds = await actions.boundingBox()
  assert.ok(bounds && bounds.y >= 0 && bounds.y + bounds.height <= page.viewportSize().height + 1,
    `${name} actions stay in the viewport at ${width}px: ${JSON.stringify(bounds)}`)
  const targets = await actions.locator('button:visible').evaluateAll(buttons => buttons.map(button => {
    const r = button.getBoundingClientRect()
    return { name: button.textContent.trim(), width: r.width, height: r.height }
  }))
  assert.ok(targets.every(target => target.width >= 44 && target.height >= 44), `${name} touch targets`)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${name} horizontal overflow`)
  results.push({ name, width, actions: bounds, targets })
  await page.screenshot({ path: path.join(output, `${name}-${width}.png`), animations: 'disabled' })
}
async function focusAccessible(page, inputSelector, actionsSelector) {
  await page.locator(inputSelector).focus()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const positions = await page.evaluate(({ inputSelector, actionsSelector }) => {
    const input = document.querySelector(inputSelector).getBoundingClientRect()
    const actions = document.querySelector(actionsSelector).getBoundingClientRect()
    const header = document.querySelector('.settings-mobile-header').getBoundingClientRect()
    return { inputTop: input.top, inputBottom: input.bottom, headerBottom: header.bottom, actionsTop: actions.top }
  }, { inputSelector, actionsSelector })
  assert.ok(positions.inputTop >= positions.headerBottom && positions.inputBottom <= positions.actionsTop,
    `Focused ${inputSelector} stays between the page header and action area: ${JSON.stringify(positions)}`)
}
async function visualKeyboard(page, inputSelector, actionsSelector, name) {
  await page.locator(inputSelector).focus()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.evaluate(inputSelector => {
    const input = document.querySelector(inputSelector)
    window.scrollBy(0, input.getBoundingClientRect().top - 320)
  }, inputSelector)
  const layoutHeight = await page.evaluate(() => innerHeight)
  await page.evaluate(() => {
    // This models a keyboard changing only the visual viewport. Resizing the
    // Playwright page also changes innerHeight and cannot catch this failure.
    Object.defineProperty(visualViewport, 'height', { value: 400, configurable: true })
    Object.defineProperty(visualViewport, 'offsetTop', { value: 0, configurable: true })
    visualViewport.dispatchEvent(new Event('resize'))
  })
  const measure = () => page.evaluate(({ inputSelector, actionsSelector }) => {
    const input = document.querySelector(inputSelector).getBoundingClientRect()
    const actions = document.querySelector(actionsSelector).getBoundingClientRect()
    const header = document.querySelector('.settings-mobile-header').getBoundingClientRect()
    return { inputTop: input.top, inputBottom: input.bottom, actionsTop: actions.top, actionsBottom: actions.bottom,
      headerBottom: header.bottom, scrollY, layoutHeight: innerHeight, visualHeight: visualViewport.height, visualTop: visualViewport.offsetTop }
  }, { inputSelector, actionsSelector })
  await page.waitForFunction(({ inputSelector, actionsSelector }) => {
    const input = document.querySelector(inputSelector).getBoundingClientRect()
    const actions = document.querySelector(actionsSelector).getBoundingClientRect()
    const header = document.querySelector('.settings-mobile-header').getBoundingClientRect()
    return actions.bottom <= visualViewport.height + visualViewport.offsetTop + 1 && input.bottom <= actions.top - 10
      && input.top >= Math.max(header.bottom, visualViewport.offsetTop)
  }, { inputSelector, actionsSelector })
  const first = await measure()
  assert.equal(first.layoutHeight, layoutHeight, 'Keyboard simulation must preserve the layout viewport')
  assert.equal(first.visualHeight, 400)
  await page.evaluate(() => visualViewport.dispatchEvent(new Event('resize')))
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const repeated = await measure()
  assert.ok(Math.abs(repeated.scrollY - first.scrollY) <= 1, 'Repeated resize must not oscillate the focused field')
  await page.evaluate(() => {
    Object.defineProperty(visualViewport, 'offsetTop', { value: 40, configurable: true })
    visualViewport.dispatchEvent(new Event('scroll'))
  })
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const panned = await measure()
  assert.equal(panned.scrollY, repeated.scrollY, 'Viewport panning must not pull focus back into view')
  assert.ok(panned.actionsBottom <= 441, 'Actions account for a nonzero visual viewport offset')
  results.push({ name, width: page.viewportSize().width, keyboard: { first, repeated, panned } })
  await page.screenshot({ path: path.join(output, `${name}-390.png`), animations: 'disabled' })
  await page.evaluate(() => {
    delete visualViewport.height
    delete visualViewport.offsetTop
    visualViewport.dispatchEvent(new Event('resize'))
  })
  await page.waitForFunction(actionsSelector => document.querySelector(actionsSelector).style.getPropertyValue('--settings-keyboard-inset') === '0px', actionsSelector)
}
try {
  for (const width of [320, 375, 390, 430]) {
    const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url())
      return url.origin === base && request.method() === 'GET' ? route.continue() : route.abort()
    })
    async function pageFor(section) {
      const page = await context.newPage()
      currentPage = page
      page.setDefaultTimeout(10000)
      page.on('pageerror', error => errors.push(String(error)))
      await page.goto(`${base}/settings?sec=${section}`)
      await page.locator('.settings-mobile-title h1').waitFor()
      return page
    }
    let page = await context.newPage()
    currentPage = page
    await page.goto(`${base}/?media=1`)
    await page.locator('.app-nav .nav-secondary > summary').click()
    await page.locator('.app-nav .nav-tools').getByRole('link', { name: '设置', exact: true }).click()
    await page.waitForURL(url => url.pathname === '/settings' && url.searchParams.get('sec') === 'sec-status')
    await page.locator('.settings-directory').waitFor()
    assert.equal(new URL(page.url()).searchParams.get('media'), '1', 'Settings home preserves selected media scope')
    await page.close()
    page = await pageFor('sec-tmdb')
    await page.locator('#tmdb-read-token').waitFor()
    await page.locator('.tmdb-options > summary').click()
    await visibleActions(page, '.tmdb-actions', 'tmdb-expanded', width)
    await focusAccessible(page, '#tmdb-image-base', '.tmdb-actions')
    if (width === 390) {
      await visualKeyboard(page, '#tmdb-proxy', '.tmdb-actions', 'tmdb-visual-keyboard')
      await page.setViewportSize({ width, height: 480 })
      await visibleActions(page, '.tmdb-actions', 'tmdb-short-viewport', width)
      await focusAccessible(page, '#tmdb-proxy', '.tmdb-actions')
    }
    await page.close()

    page = await pageFor('sec-ai')
    await page.getByRole('button', { name: '配置与测试服务', exact: true }).click()
    await page.locator('.ai-advanced > summary').click()
    await visibleActions(page, '.ai-actions', 'ai-expanded', width)
    let writes = 0
    await page.route('**/api/ai/settings', async route => {
      if (route.request().method() === 'GET') return route.continue()
      assert.equal(route.request().method(), 'PATCH')
      writes++
      const draft = route.request().postDataJSON()
      if (writes === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '模拟保存失败，请重试' }) })
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...draft, api_key_set: false, usage: { requests: 0, input_tokens: 0, output_tokens: 0 } }) })
    })
    await page.locator('#ai-model').fill('')
    await page.locator('#ai-timeout').fill('1')
    await page.locator('.ai-advanced > summary').click()
    await page.getByRole('button', { name: '保存智能辅助配置', exact: true }).click()
    assert.equal(writes, 0, 'Invalid configuration never reaches the API')
    assert.equal(await page.locator('#ai-model').getAttribute('aria-invalid'), 'true')
    assert.equal(await page.locator('.ai-advanced').evaluate(element => element.open), true, 'Invalid advanced group opens')
    assert.equal(await page.evaluate(() => document.activeElement.id), 'ai-model', 'First invalid field receives focus')
    await focusAccessible(page, '#ai-timeout', '.ai-actions')
    await page.locator('#ai-model-description').filter({ hasText: '请填写模型名称' }).waitFor()
    await page.locator('#ai-model').fill('mobile-test-model')
    await page.locator('#ai-timeout').fill('12')
    await page.getByRole('button', { name: '保存智能辅助配置', exact: true }).click()
    await page.locator('.ai-result').filter({ hasText: '模拟保存失败' }).waitFor()
    assert.equal(await page.locator('#ai-model').inputValue(), 'mobile-test-model', 'Failed save preserves draft')
    await page.getByRole('button', { name: '保存智能辅助配置', exact: true }).click()
    await page.locator('.ai-result').filter({ hasText: '配置已保存' }).waitFor()
    assert.equal(writes, 2)
    await page.close()

    page = await pageFor('sec-auth')
    await page.locator('#protection-new').fill('isolated-example')
    await visibleActions(page, '.protection-settings .settings-form-actions', 'protection-draft', width)
    if (width === 390) await visualKeyboard(page, '#protection-repeat', '.protection-settings .settings-form-actions', 'protection-visual-keyboard')
    await page.getByRole('button', { name: '放弃修改', exact: true }).click()
    assert.equal(await page.locator('#protection-new').inputValue(), '')
    await page.close()

    page = await pageFor('sec-index')
    await page.locator('.capacity-task').waitFor()
    assert.equal(await page.locator('.capacity-task').evaluate(element => element.open), false, 'Mobile capacity task starts collapsed')
    assert.equal(await page.locator('[data-cache="transcode"]').evaluate(element => element.open), false, 'Cleanup task starts collapsed')
    await page.locator('.capacity-task > summary').click()
    await visibleActions(page, '.capacity-actions', 'capacity-expanded', width)
    await page.locator('.capacity-task > summary').click()
    await page.locator('[data-cache="transcode"] > summary').click()
    assert.equal(await page.locator('[data-cache="transcode"] .cache-task-body').isVisible(), true)
    assert.equal(await page.locator('.capacity-actions').isVisible(), false, 'Only the active task exposes its actions')
    await page.screenshot({ path: path.join(output, `cache-cleanup-${width}.png`), animations: 'disabled' })
    await page.close()
    await context.close()
  }
  assert.deepEqual(errors, [], 'No browser runtime errors')
  console.log('PASS mobile settings: 320/375/390/430px, settings navigation, visible form actions, short and visual-only keyboard viewports, stable focus/panning, validation/focus, failure/retry, discard and separate cache tasks')
} catch (error) {
  await currentPage?.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
  throw error
} finally {
  await writeFile(path.join(output, 'metrics.json'), JSON.stringify({ results, errors }, null, 2) + '\n')
  await browser.close()
}
