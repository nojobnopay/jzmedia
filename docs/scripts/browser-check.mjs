import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { publicPages, htmlPath } from '../.vitepress/public-pages.mjs'
import { createHelpServer } from './server.mjs'
import { site } from './site-config.mjs'

const storyboardPages = [
  ['user-guide/tv-bindings.html', /不会移动视频|不移动文件/],
  ['user-guide/tv-organizing.html', /确认并整理/],
  ['user-guide/playback-controls.html', /手机先暂停并保留/],
]

async function captureStoryboard(page, storyboard, file, viewport, frame, captureRoot) {
  if (!captureRoot) return
  await storyboard.scrollIntoViewIfNeeded()
  await storyboard.locator('img:visible').evaluateAll(images => Promise.all(images.map(image => image.decode())))
  const name = path.basename(file, '.html')
  await storyboard.screenshot({ path: path.join(captureRoot, `${name}-${viewport}-${frame}.png`), animations: 'disabled', style: '.VPNav, .VPLocalNav { visibility: hidden !important; }' })
}

async function checkStoryboards(browser, base, errors, unexpected, captureRoot) {
  // A clock controls its whole context. Each story starts with clean timers, viewport and scroll.
  for (const [file] of storyboardPages) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' })
    await context.route('**/*', route => {
      const url = route.request().url()
      if (/^https?:/.test(url) && new URL(url).origin !== new URL(base).origin) { unexpected.push(url); return route.abort() }
      return route.continue()
    })
    const page = await context.newPage()
    const storyErrors = []
    function recordError(error) {
      const message = `${page.url()}: ${error}`
      errors.push(message)
      storyErrors.push(message)
    }
    page.on('pageerror', recordError)
    page.on('console', message => { if (message.type() === 'error') recordError(message.text()) })
    await page.clock.install({ time: new Date('2026-11-08T04:00:00Z') })
    try {
      await page.goto(new URL(file, base).href)
      const storyboard = page.locator('.doc-storyboard.is-enhanced')
      await storyboard.waitFor()
      // Let page initialization finish naturally, then advance frames with mocked time only.
      await page.clock.pauseAt(new Date(await page.evaluate(() => Date.now()) + 60000))
      const play = storyboard.locator('.doc-storyboard-play')
      const steps = storyboard.locator('.doc-storyboard-steps button')
      const casting = file === 'user-guide/playback-controls.html'
      async function frame(number) {
        await storyboard.locator(`[data-frame="${number}"]`).waitFor({ state: 'visible' })
        assert.equal(await storyboard.locator('.doc-storyboard-frame:visible').count(), 1, `${file}: 只显示当前帧`)
        assert.equal(await steps.nth(number - 1).getAttribute('aria-pressed'), 'true')
      }
      async function stopped() {
        await storyboard.locator('.doc-storyboard-play[aria-pressed="false"]').waitFor()
      }
      await frame(1)
      await stopped()
      await captureStoryboard(page, storyboard, file, 'desktop', '01', captureRoot)
      await page.clock.runFor(12000)
      await frame(1)
      await steps.nth(1).focus()
      await page.keyboard.press('Enter')
      await frame(2)
      await captureStoryboard(page, storyboard, file, 'desktop', '02', captureRoot)
      await steps.nth(0).click()
      await frame(1)
      await steps.nth(2).click()
      await frame(3)
      assert.equal((await play.textContent()).trim(), '重新播放')
      await play.click()
      await frame(1)
      assert.equal(await play.getAttribute('aria-pressed'), 'true')
      await page.clock.runFor(4000)
      await frame(2)
      await page.clock.runFor(4000)
      await frame(3)
      await stopped()
      await page.clock.runFor(12000)
      await frame(3)
      await captureStoryboard(page, storyboard, file, 'desktop', '03', captureRoot)

      await steps.nth(0).click()
      await play.click()
      await page.clock.runFor(1500)
      await play.click()
      await stopped()
      await page.clock.runFor(8000)
      await frame(1)

      // Selecting a step with the keyboard pauses automatic progression.
      await play.click()
      await steps.nth(0).focus()
      await page.keyboard.press('Enter')
      await stopped()
      await page.clock.runFor(8000)
      await frame(1)
      assert.ok(await steps.nth(0).evaluate(el => el === document.activeElement), `${file}: 手选步骤保留键盘焦点`)

      await play.click()
      await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }))
      await stopped()
      await page.clock.runFor(8000)
      await frame(1)

      await steps.nth(0).click()
      await play.click()
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await storyboard.locator('.doc-storyboard-play[disabled]').waitFor()
      await stopped()
      await page.clock.runFor(8000)
      await frame(1)
      await steps.nth(1).click()
      await frame(2)
      assert.ok(await play.isDisabled(), `${file}: 减少动态效果时手选仍可用，自动播放禁用`)
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      // CSS viewport widths drive the layout; device pixel ratio is not a breakpoint.
      for (const width of [320, 390, 430, 1440]) {
        await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
        for (let number = 1; number <= 3; number++) {
          await steps.nth(number - 1).click()
          await frame(number)
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
          assert.ok(overflow <= 1, `${file}: ${width}px 第 ${number} 步横向溢出 ${overflow}px`)
          const current = storyboard.locator('.doc-storyboard-frame:visible')
          if (!casting) {
            assert.equal(await current.locator('img').count(), 0, '目录示意使用网页文字，不能整图缩放')
            const files = current.locator('.doc-binding-file, .doc-directory-file')
            assert.ok(await files.count(), '当前步骤应保留可复制的文件名')
            assert.ok(await files.evaluateAll(nodes => nodes.every(node => parseFloat(getComputedStyle(node).fontSize) >= 14 && getComputedStyle(node).userSelect !== 'none')), `${file}: ${width}px 文件名应保持可读、可选择`)
            for (const row of await current.locator('.doc-binding-row').all()) {
              const source = await row.locator('.doc-binding-source').boundingBox()
              const target = await row.locator('.doc-binding-target').boundingBox()
              assert.ok(width <= 640 ? target.y >= source.y + source.height : target.x >= source.x + source.width,
                `${file}: ${width}px 目录与归属应保持相邻的阅读顺序`)
            }
            const paths = current.locator('.doc-directory-comparison.is-preview > .doc-directory')
            if (await paths.count()) {
              assert.equal(await paths.count(), 2, '整理预览应同时提供原路径与目标路径')
              const before = await paths.nth(0).boundingBox()
              const after = await paths.nth(1).boundingBox()
              assert.ok(width <= 640 ? after.y >= before.y + before.height : after.x >= before.x + before.width,
                `${width}px: 整理前后目录按视口重排`)
              assert.equal(await paths.nth(1).locator('.doc-directory-file').first().textContent(), '群岛之间-S01E01-归航.mp4', '换行标记不能改变可复制的文件名')
            }
          }
          for (const control of await steps.all()) {
            const rect = await control.boundingBox()
            assert.ok(rect && rect.height >= 44 && rect.width >= 44, `${file}: ${width}px 步骤按钮保留 44px 触摸区域`)
          }
          if (casting) {
            const picture = storyboard.locator('.doc-casting-picture')
            assert.equal(await picture.count(), 1, '投屏插图只出现一次')
            const image = picture.locator('img')
            await image.evaluate(image => image.decode())
            const expected = await picture.evaluate((picture, mobile) => {
              const source = mobile ? picture.querySelector('source')?.getAttribute('srcset') : picture.querySelector('img')?.getAttribute('src')
              return source ? new URL(source, document.baseURI).href : null
            }, width <= 640)
            assert.ok(expected, `${width}px: 投屏图片必须提供当前构图`)
            assert.equal(await image.evaluate(image => image.currentSrc), expected, `${width}px: 浏览器应选择对应桌面/手机构图`)
            if (number === 2) {
              const times = storyboard.locator('.doc-storyboard-frame:visible .doc-casting-time')
              assert.ok(await times.count() >= 2, '交接示例在手机和电视上保留同一时间')
              for (const time of await times.all()) {
                assert.equal((await time.innerText()).trim(), '12:34')
                const lines = await time.evaluate(element => {
                  const range = document.createRange()
                  range.selectNodeContents(element)
                  return [...new Set([...range.getClientRects()].map(rect => Math.round(rect.top)))]
                })
                assert.equal(lines.length, 1, `${width}px: 时间 12:34 不能拆行`)
              }
            }

          }
          if (width === 390 || ([320, 430].includes(width) && number === 2)) {
            await captureStoryboard(page, storyboard, file, `${width}px`, `0${number}`, captureRoot)
          }
        }
      }
    } catch (error) {
      const state = await page.evaluate(() => ({
        url: location.href,
        readyState: document.readyState,
        appMounted: !!document.querySelector('#app')?.__vue_app__,
        storyboards: document.querySelectorAll('.doc-storyboard').length,
        enhanced: document.querySelectorAll('.doc-storyboard.is-enhanced').length,
      })).catch(() => ({ url: page.url() }))
      throw new Error(`${file}: 分镜检查失败；页面状态 ${JSON.stringify(state)}；浏览器错误 ${JSON.stringify(storyErrors)}；${error.message}`, { cause: error })
    } finally {
      await context.close()
    }
  }
}

async function checkResponsiveFigures(page, width, resizeWhileOpen = false) {
  for (const figure of await page.locator('.doc-figure:has(source[media]):visible').all()) {
    const image = figure.locator('picture > img')
    await image.scrollIntoViewIfNeeded()
    await image.evaluate(image => image.decode())
    assert.ok(await figure.evaluate(figure => figure.querySelector('.doc-figure-hint').getBoundingClientRect().top >= figure.querySelector('.doc-figure-frame').getBoundingClientRect().bottom), '放大提示不能遮住教程截图里的操作控件')
    const expected = await figure.evaluate((figure, mobile) => {
      const source = mobile ? figure.querySelector('source').getAttribute('srcset') : figure.querySelector('picture > img').getAttribute('src')
      return new URL(source, document.baseURI).href
    }, width <= 640)
    assert.equal(await image.evaluate(image => image.currentSrc), expected, `${width}px: 教程图片应选择当前视口素材`)
    if ([390, 1440].includes(width)) {
      const opener = figure.locator('.doc-figure-open')
      await opener.focus()
      await page.keyboard.press('Enter')
      const dialog = figure.locator('dialog[open]')
      await dialog.waitFor()
      assert.equal(await dialog.locator('img').evaluate(image => image.src), expected, '放大保持当前端截图')
      assert.equal(await dialog.locator('a').evaluate(link => link.href), expected, '查看原图链接保持当前端截图')
      if (resizeWhileOpen) {
        await page.setViewportSize({ width: 1440, height: 1000 })
        assert.equal(await dialog.locator('img').evaluate(image => image.src), expected, '打开后旋转或缩放视口不替换正在查看的素材')
        assert.equal(await dialog.locator('a').evaluate(link => link.href), expected, '视口变化后原图链接仍与弹窗一致')
        await page.setViewportSize({ width, height: 844 })
        await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== 'running'))
      }
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'hidden' })
      assert.ok(await opener.evaluate(button => button === document.activeElement), '关闭响应式图片后返回原按钮')
    }
  }
}

async function checkReadingLayouts(page, base, captureRoot) {
  for (const file of ['libraries', 'onboarding', 'organizing', 'metadata', 'watch-online', 'playback-previews']) {
    await page.goto(new URL(`user-guide/${file}.html`, base).href)
    await page.waitForFunction(() => !!document.querySelector('#app')?.__vue_app__)
    const map = page.locator('.doc-library-map')
    if (await map.count()) await map.evaluate(el => { const disclosure = el.closest('details'); if (disclosure) disclosure.open = true })
    for (const width of [320, 390, 430, 1440]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
      await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== 'running'))
      if (width <= 640) {
        const sidebar = await page.locator('.VPSidebar').boundingBox()
        assert.ok(!sidebar || sidebar.x + sidebar.width <= 1, `${file}: 手机侧栏应退出视口，不能覆盖正文`)
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${file}: ${width}px 不应横向溢出`)
      await checkResponsiveFigures(page, width, file === 'watch-online' && width === 390)
      if (file === 'metadata') {
        const flow = page.locator('.doc-ingestion-flow')
        const steps = flow.locator(':scope > .doc-ingestion-step')
        assert.equal(await steps.count(), 3, '入库保留三个阶段')
        assert.equal(await flow.locator('.doc-ingestion-choices > div').count(), 2, '扫描与上传是可选的两个入口')
        assert.equal(await flow.locator('img').count(), 0, '入库流程不用图片缩放文字')
        const first = await steps.nth(0).boundingBox()
        const second = await steps.nth(1).boundingBox()
        assert.ok(width <= 640 ? second.y >= first.y + first.height : second.x >= first.x + first.width, '入库流程按视口重排')
        assert.ok(await flow.locator('p').evaluateAll(nodes => nodes.every(node => parseFloat(getComputedStyle(node).fontSize) >= 14)), '入库说明保持正常字号')
        if (captureRoot && [320, 390, 1440].includes(width)) {
          await flow.screenshot({ path: path.join(captureRoot, `metadata-${width}px.png`), animations: 'disabled', style: '.VPNav, .VPLocalNav { visibility: hidden !important; }' })
        }
      }
      if (file === 'watch-online') {
        const browse = page.locator('.doc-figure:has(source[media])')
        assert.equal(await browse.count(), 1, '在线播放浏览示例合并为一组响应式图片')
        if (captureRoot && [390, 1440].includes(width)) {
          await browse.screenshot({ path: path.join(captureRoot, `online-${width}px.png`), animations: 'disabled', style: '.VPNav, .VPLocalNav { visibility: hidden !important; }' })
        }
      }
      if (await map.count()) {
        const root = await map.locator('.doc-library-root').boundingBox()
        const children = await map.locator('.doc-library-children').boundingBox()
        assert.ok(width <= 640 ? children.y >= root.y + root.height : children.x >= root.x + root.width,
          `${file}: ${width}px 媒体库层级应按视口重排`)
        assert.ok(await map.locator('code').evaluateAll(nodes => nodes.every(node => parseFloat(getComputedStyle(node).fontSize) >= 14)), '目录文字保持正常阅读尺寸')
        if (captureRoot && file === 'libraries' && [320, 390, 1440].includes(width)) {
          await map.screenshot({ path: path.join(captureRoot, `libraries-${width}px.png`), animations: 'disabled', style: '.VPNav, .VPLocalNav { visibility: hidden !important; }' })
        }
      }
      if (file === 'onboarding') {
        assert.equal(await page.locator('.doc-step').count(), 0, '向导不再把整页截图压入窄栏')
        for (const number of [2, 4]) {
          const picture = page.locator(`.doc-figure:has(img[src*="0${number}-"])`)
          const column = await page.locator('.vp-doc').boundingBox()
          const rect = await picture.boundingBox()
          assert.ok(rect.width >= column.width * .95, `${width}px 第 ${number} 步关键截图使用正文宽度`)
          if (captureRoot && [390, 1440].includes(width)) {
            await page.getByRole('heading', { name: new RegExp(`Step ${number}：`) }).evaluate(el => el.scrollIntoView())
            await picture.locator('img').evaluate(image => image.decode())
            await page.screenshot({ path: path.join(captureRoot, `onboarding-${width}px-step-${number}.png`), animations: 'disabled', style: '.VPNav, .VPLocalNav { visibility: hidden !important; }' })
            await picture.screenshot({ path: path.join(captureRoot, `onboarding-${width}px-step-${number}-figure.png`), animations: 'disabled', style: '.VPNav, .VPLocalNav { visibility: hidden !important; }' })
          }
        }
      }
      if (file === 'organizing') {
        assert.equal(await page.locator('.doc-figure:visible').count(), 1, '电影整理主线只保留一张关键预览')
        assert.equal(await page.locator('video:visible').count(), 0, '补充录屏由读者展开')
      }
    }
    if (file === 'watch-online') {
      for (const width of [640, 641]) {
        await page.setViewportSize({ width, height: 844 })
        await checkResponsiveFigures(page, width)
      }
    }
  }
}

const argument = process.argv.indexOf('--url')
const captureRoot = process.argv.includes('--capture') ? fileURLToPath(new URL('../../output/playwright/docs-storyboards/', import.meta.url)) : null
if (captureRoot) mkdirSync(captureRoot, { recursive: true })
let server
let browser
try {
  let base = argument >= 0 ? process.argv[argument + 1] : null
  if (!base) {
    server = createHelpServer()
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
    base = `http://127.0.0.1:${server.address().port}${site.base}`
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
  await checkStoryboards(browser, base, errors, unexpected, captureRoot)
  await checkReadingLayouts(page, base, captureRoot)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(new URL('user-guide/onboarding.html', base).href)
  assert.equal((await page.reload()).status(), 200, '深层页面刷新后仍可直接访问')
  await page.waitForFunction(() => !!document.querySelector('#app')?.__vue_app__)
  const stepHeadings = page.locator('.vp-doc h3').filter({ hasText: /^Step \d+：/ })
  assert.equal(await stepHeadings.count(), 4, '四个步骤必须进入正文标题层级')
  const firstStepId = await stepHeadings.first().getAttribute('id')
  assert.ok(firstStepId, '步骤标题必须可以直接链接')
  assert.ok(await page.locator(`.VPDocAsideOutline a[href="#${firstStepId}"]`).count(), '步骤必须进入页内目录')
  assert.equal(await page.locator('.doc-step h2').count(), 0, '布局组件不能额外插入同级主题标题')
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
    const result = page.locator('.VPLocalSearchBox a.result').first()
    const target = new URL(await result.getAttribute('href'), page.url())
    assert.ok(target.href.startsWith(base), `${query}: 搜索结果离开部署路径 ${target.href}`)
    await result.click()
    await page.waitForURL(target.href)
    assert.equal((await page.reload()).status(), 200, `${query}: 搜索结果无法直接访问`)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  for (const file of ['index.html', 'getting-started/deployment.html', 'getting-started/operations.html',
    'getting-started/configuration.html', 'getting-started/troubleshooting.html', 'user-guide/onboarding.html',
    'user-guide/subtitles.html', 'user-guide/settings.html', 'user-guide/files.html', 'user-guide/troubleshooting.html',
    ...storyboardPages.map(([file]) => file)]) {
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
  assert.equal(await plain.locator('.vp-doc h3').filter({ hasText: /^Step \d+：/ }).count(), 4, '无 JS 时仍有完整步骤标题')
  for (const [file, keyInstruction] of storyboardPages) {
    await plain.goto(new URL(file, base).href)
    assert.match(await plain.locator('.vp-doc').innerText(), keyInstruction, `${file}: 无 JS 时仍有关键说明`)
    const frames = plain.locator('.doc-storyboard-frame')
    const casting = file === 'user-guide/playback-controls.html'
    assert.equal(await frames.count(), 3, `${file}: 无 JS 时保留完整三步`)
    for (const frame of await frames.all()) {
      assert.ok(await frame.isVisible(), `${file}: 无 JS 时分镜不能隐藏`)
      assert.ok((await frame.innerText()).trim(), `${file}: 每步保留等价说明`)
    }
    assert.equal(await plain.locator('.doc-storyboard img').count(), casting ? 1 : 0, `${file}: 无 JS 时插图数量符合阅读结构`)
    assert.equal(await plain.locator('.doc-storyboard-controls').count(), 0, `${file}: 无 JS 时不显示无效播放按钮`)
    assert.equal(await plain.locator('.doc-storyboard-steps button, .doc-storyboard-play').count(), 0, `${file}: 无 JS 时不显示无效分步交互`)
  }
  await plain.setViewportSize({ width: 390, height: 844 })
  for (const file of ['onboarding', 'watch-online']) {
    await plain.goto(new URL(`user-guide/${file}.html`, base).href)
    const pictures = plain.locator('.doc-figure picture:has(source)')
    assert.ok(await pictures.count(), `${file}: 无 JS 仍提供手机素材`)
    for (const picture of await pictures.all()) {
      const image = picture.locator('img')
      await image.scrollIntoViewIfNeeded()
      await image.evaluate(image => image.decode())
      const expected = await picture.locator('source').evaluate(source => new URL(source.srcset, document.baseURI).href)
      assert.equal(await image.evaluate(image => image.currentSrc), expected, `${file}: 无 JS 仍选用手机截图`)
    }
  }
  await noJs.close()
  assert.deepEqual(unexpected, [], '帮助站出现外部网络请求')
  // The intentional missing-page probe produces a browser 404 console entry.
  assert.deepEqual(errors.filter(error => !error.includes('404')), [], '浏览器脚本或 CSP 错误')
  console.log(`浏览器检查通过：${paths.length} 页，桌面/390px、三组 320/390/430px 重排、投屏独立构图与时间不拆行、三组全部步骤与减少动态效果、图片放大与焦点、四组中英文搜索、离线资源、无 JS 正文与完整步骤、视频延迟加载。`)
  if (captureRoot) console.log(`分镜截图：${captureRoot}`)
} finally {
  await browser?.close()
  if (server) await new Promise(resolve => server.close(resolve))
}
