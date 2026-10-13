// Actual Vue PlayerModal + isolated FastAPI/FFmpeg + loopback SOAP renderer. No real media/LAN.
import assert from 'node:assert/strict'
import { cp, mkdir, mkdtemp, readFile, symlink, writeFile, rm } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { createServer } from 'node:net'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
const sourceArg = args.indexOf('--source')
const source = sourceArg >= 0 ? path.resolve(args[sourceArg + 1] || '') : null
const captureOnly = args.includes('--capture-only')
assert.ok(args.every((arg, i) => arg === '--capture-only' || arg === '--source' || i === sourceArg + 1 && sourceArg >= 0))
assert.ok(!source || captureOnly, '--source is for comparable visual captures of an earlier UI')
const temp = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-cast-ui-'))
const output = path.join(root, 'output/playwright/casting', captureOnly ? (source ? 'before' : 'after') : '')
let backend, browser, backendOutput = ''

async function insidePlayer(page, locator, label) {
  const bounds = await locator.evaluate(element => {
    const rect = element.getBoundingClientRect(), player = element.closest('.pv-wrap').getBoundingClientRect()
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
      player: { left: player.left, right: player.right, top: player.top, bottom: player.bottom },
      width: element.clientWidth, scroll: element.scrollWidth }
  })
  assert.ok(bounds.left >= bounds.player.left - 1 && bounds.right <= bounds.player.right + 1
    && bounds.top >= bounds.player.top - 1 && bounds.bottom <= bounds.player.bottom + 1,
  `${label} must stay inside the player: ${JSON.stringify(bounds)}`)
  assert.ok(bounds.scroll <= bounds.width + 1, `${label} must not overflow horizontally`)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
}

async function escapePanel(page, panel, trigger, fullscreen = false) {
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  assert.equal(await trigger.evaluate(element => document.activeElement === element), true, 'Escape must restore trigger focus')
  assert.equal(await page.evaluate(() => !!document.fullscreenElement), fullscreen, 'Escape closes the panel before leaving fullscreen')
  assert.equal(await page.locator('.player-dlg').isVisible(), true)
}

async function overlayControls(page, width, fullscreen = false) {
  const player = page.locator('.player-dlg'), video = player.locator('video')
  const corners = player.locator('.player-corner-controls'), toolbar = player.locator('.pv-ctl')
  const scrim = player.locator('.pv-top-scrim')
  const shown = () => corners.evaluate(element => element.classList.contains('show'))
  const surface = { x: 8, y: 80 }
  const tapSurface = async () => {
    if (width <= 390) await video.tap({ position: surface })
    else {
      // A normal desktop click first moves the mouse, which legitimately wakes
      // controls before pointerdown. Keep the cursor still for this hidden-click
      // branch; phone contexts above use an actual touchscreen tap.
      await video.dispatchEvent('pointerdown', { pointerType: 'mouse', bubbles: true, clientX: 8, clientY: 80 })
      await video.dispatchEvent('click', { bubbles: true, detail: 1 })
    }
  }
  const blur = () => page.evaluate(() => document.activeElement?.blur?.())
  const waitHidden = () => page.waitForFunction(() => !document.querySelector('.player-corner-controls')?.classList.contains('show'))
  await blur()
  await video.evaluate(async element => { element.currentTime = 12; element.playbackRate = 1; await element.play() })
  await page.waitForFunction(() => document.querySelector('video') && !document.querySelector('video').paused)
  await video.dispatchEvent('pointerdown', { pointerType: 'mouse', bubbles: true, clientX: 8, clientY: 80 })
  await page.waitForFunction(() => document.querySelector('.player-corner-controls')?.classList.contains('show'))
  const style = await corners.locator('button').evaluateAll(buttons => buttons.filter(button => button.checkVisibility()).map(button => {
    const computed = getComputedStyle(button), bounds = button.getBoundingClientRect()
    return { background: computed.backgroundColor, blur: computed.backdropFilter,
      color: computed.color, width: bounds.width, height: bounds.height }
  }))
  assert.ok(style.every(button => button.background === 'rgba(0, 0, 0, 0)' && button.blur === 'none'
    && button.width >= 43.5 && button.height >= 43.5), `Corner icons keep clear 44px targets without individual dark blocks: ${JSON.stringify(style)}`)
  const gradient = await scrim.evaluate(element => getComputedStyle(element).backgroundImage)
  assert.match(gradient, /linear-gradient/, 'One shared gradient supports the white icons over bright video')
  assert.equal(await scrim.getAttribute('aria-hidden'), 'true', 'The decorative gradient stays out of the accessibility tree')
  if (width === 390) {
    await page.screenshot({ path: path.join(output, `overlay-${fullscreen ? 'fullscreen-' : ''}active-${width}.png`) })
    // A pure white surrogate exposes the worst-case icon contrast without
    // fetching footage or changing the isolated synthetic media file.
    const originalStyle = await video.evaluate(element => {
      const wrap = element.closest('.pv-wrap')
      const previous = { visibility: element.style.visibility, background: wrap.style.background }
      element.style.visibility = 'hidden'; wrap.style.background = '#fff'
      return previous
    })
    try {
      await page.screenshot({ path: path.join(output, `overlay-${fullscreen ? 'fullscreen-' : ''}bright-${width}.png`) })
    } finally {
      await video.evaluate((element, previous) => {
        element.style.visibility = previous.visibility; element.closest('.pv-wrap').style.background = previous.background
      }, originalStyle)
    }
  }
  await page.mouse.move(0, 0)
  await waitHidden()
  assert.equal(await corners.evaluate(element => element.inert), true, 'Hidden corner controls cannot take keyboard focus')
  assert.equal(await corners.getAttribute('aria-hidden'), 'true')
  assert.equal(await corners.evaluate(element => getComputedStyle(element).pointerEvents), 'none', 'Hidden icons do not intercept video taps')
  assert.equal(await scrim.evaluate(element => element.classList.contains('show')), false, 'The gradient fades with its icons')
  assert.equal(await toolbar.evaluate(element => element.classList.contains('show')), !fullscreen,
    'The window footer stays visible; the fullscreen footer follows the picture controls')
  assert.equal(await toolbar.evaluate(element => element.inert), fullscreen)
  await corners.locator('button').first().evaluate(element => element.focus())
  assert.equal(await corners.evaluate(element => element.contains(document.activeElement)), false, 'Inert corner buttons cannot steal focus')
  await video.dispatchEvent('pointermove', { pointerType: 'touch', bubbles: true, clientX: 160, clientY: 110 })
  await page.waitForTimeout(250)
  assert.equal(await shown(), false, 'Touch movement does not wake hidden picture controls')
  if (width === 390) await page.screenshot({ path: path.join(output, `overlay-${fullscreen ? 'fullscreen-' : ''}idle-${width}.png`) })
  if (fullscreen) {
    const settings = player.getByRole('button', { name: '播放设置', exact: true })
    await page.keyboard.press('Tab')
    assert.equal(await settings.evaluate(element => document.activeElement === element), true,
      'Tab wakes fullscreen controls and focuses the first reachable tool')
    await page.waitForTimeout(3200)
    assert.equal(await shown(), true, 'Fullscreen keyboard focus stays visible beyond the idle deadline')
    await blur()
    await waitHidden()
    await page.keyboard.press('Shift+Tab')
    const tracks = player.getByRole('button', { name: '字幕与音轨', exact: true })
    assert.equal(await tracks.evaluate(element => document.activeElement === element), true,
      'Shift+Tab wakes fullscreen controls and focuses the last reachable tool')
    await blur()
    await waitHidden()
  }
  await tapSurface()
  await page.waitForTimeout(350)
  assert.equal(await shown(), true, 'The first surface tap wakes hidden controls')
  assert.equal(await video.evaluate(element => element.paused), false, 'The first tap keeps playback running')
  // Real mouse activity refreshes the inactivity deadline. Touch movement above
  // deliberately did not wake controls; meaningful mouse movement does.
  await page.waitForTimeout(2100)
  await video.dispatchEvent('pointermove', { pointerType: 'mouse', bubbles: true, clientX: 18, clientY: 90 })
  await page.waitForTimeout(1300)
  assert.equal(await shown(), true, 'Meaningful mouse movement starts a fresh three-second window')
  await waitHidden()
  await tapSurface()
  await page.waitForTimeout(600)
  await tapSurface()
  await page.waitForFunction(() => document.querySelector('video')?.paused)
  await page.waitForTimeout(3200)
  assert.equal(await shown(), true, 'Paused playback keeps picture controls visible')
  assert.equal(await page.evaluate(() => !!document.fullscreenElement), fullscreen, 'Visible single-tap pause does not change fullscreen')
  await player.locator('.main-play').click()
  await page.waitForFunction(() => document.querySelector('video') && !document.querySelector('video').paused)
  const speed = player.getByRole('button', { name: /^播放速度 .* 倍$/ })
  await speed.click()
  const speedPanel = player.getByRole('region', { name: '播放速度', exact: true })
  await speedPanel.waitFor()
  await page.waitForTimeout(3200)
  assert.equal(await shown(), true, 'An open menu prevents inactivity from hiding controls')
  await escapePanel(page, speedPanel, speed, fullscreen)
  await page.keyboard.press('Tab')
  await speed.focus()
  assert.equal(await speed.evaluate(element => element.matches(':focus-visible')), true)
  await page.waitForTimeout(3200)
  assert.equal(await shown(), true, 'Visible keyboard focus preserves reachable controls')
  await blur()
  await video.dispatchEvent('pointerdown', { pointerType: 'mouse', bubbles: true, clientX: 8, clientY: 80 })
  await page.waitForFunction(() => document.querySelector('.player-corner-controls')?.classList.contains('show'))
  const seek = player.getByRole('slider', { name: '播放进度', exact: true })
  const seekBounds = await seek.boundingBox()
  await page.mouse.move(seekBounds.x + seekBounds.width * .35, seekBounds.y + seekBounds.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(3200)
  assert.equal(await shown(), true, 'Dragging progress keeps picture controls visible')
  await page.mouse.up()
  await page.mouse.move(0, 0)
  await blur()
  if (!fullscreen && width === 390) {
    await page.waitForFunction(() => !document.querySelector('.seek-ov'))
    await waitHidden()
    await video.dispatchEvent('error')
    await player.locator('.error-card').waitFor()
    await page.waitForTimeout(3200)
    assert.equal(await shown(), true, 'An error keeps picture controls visible while the media element is still playing')
    await video.dispatchEvent('playing')
    await player.locator('.error-card').waitFor({ state: 'hidden' })
  }
  const pause = player.locator('.main-play[aria-label="暂停"]')
  if (await pause.count()) await pause.click()
  await page.waitForFunction(() => document.querySelector('video')?.paused)
  await page.waitForFunction(() => document.querySelector('.player-corner-controls')?.classList.contains('show'))
  await writeFile(path.join(output, `overlay-${fullscreen ? 'fullscreen-' : ''}${width}.json`), JSON.stringify({ fullscreen, style, gradient,
    windowFooterAlwaysVisible: !fullscreen, idleDelay: 3000, touchMovementWakes: false, firstTapPreservesPlayback: true }, null, 2) + '\n')
  console.log(`PASS ${width}px ${fullscreen ? 'fullscreen' : 'window'} shared gradient, auto-hide/inert, first tap, pointer activity, pause, menus, focus and progress drag`)
}

async function fullscreenTitleSurface(page) {
  const player = page.locator('.player-dlg'), video = player.locator('video')
  await player.getByRole('button', { name: '全屏', exact: true }).click()
  await page.waitForFunction(() => !!document.fullscreenElement)
  const pause = player.locator('.main-play[aria-label="暂停"]')
  if (await pause.count()) await pause.click()
  await page.waitForFunction(() => document.querySelector('video')?.paused)
  const title = await player.locator('.pv-heading').boundingBox()
  assert.ok(title?.width > 0, 'Fullscreen title is displayed')
  await page.mouse.click(title.x + Math.min(title.width / 2, 30), title.y + title.height / 2)
  await page.waitForFunction(() => document.querySelector('video') && !document.querySelector('video').paused)
  assert.equal(await page.evaluate(() => !!document.fullscreenElement), true, 'A single title-area click does not exit fullscreen')
  await player.locator('.main-play[aria-label="暂停"]').click()
  await page.waitForFunction(() => document.querySelector('video')?.paused)
  await player.getByRole('button', { name: '退出全屏', exact: true }).click()
  await page.waitForFunction(() => !document.fullscreenElement)
  console.log(`PASS ${page.viewportSize().width}px fullscreen title and gradient let surface playback clicks through`)
}

async function centralPlayControl(page, width, fullscreen = false) {
  const player = page.locator('.player-dlg'), central = player.locator('.center-play')
  await central.waitFor()
  const geometry = await central.evaluate(button => {
    const wrap = button.closest('.pv-wrap'), video = wrap.querySelector('video')
    const rect = element => {
      const bounds = element.getBoundingClientRect()
      return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom,
        width: bounds.width, height: bounds.height, cx: bounds.left + bounds.width / 2, cy: bounds.top + bounds.height / 2 }
    }
    return { button: rect(button), video: rect(video),
      corners: [...wrap.querySelectorAll('.player-corner-controls button')].filter(element => element.checkVisibility()).map(rect),
      toolbar: rect(wrap.querySelector('.pv-ctl')) }
  })
  const { button, video, corners, toolbar } = geometry
  assert.ok(button.left >= video.left - 1 && button.right <= video.right + 1
    && button.top >= video.top - 1 && button.bottom <= video.bottom + 1,
  `${width}px central play stays in the picture: ${JSON.stringify(geometry)}`)
  assert.ok(Math.abs(button.cx - video.cx) <= 2, 'Central play aligns to the video horizontally')
  if (!fullscreen && width <= 390) {
    assert.ok(button.cy - video.cy >= 8, `${width}px phone central play moves below the picture center: ${JSON.stringify(geometry)}`)
  } else {
    assert.ok(Math.abs(button.cy - video.cy) <= 2, `${width}px desktop/fullscreen central play aligns to the video center: ${JSON.stringify(geometry)}`)
  }
  for (const [index, obstacle] of [...corners, toolbar].entries()) {
    const dx = Math.max(obstacle.left - button.right, button.left - obstacle.right, 0)
    const dy = Math.max(obstacle.top - button.bottom, button.top - obstacle.bottom, 0)
    assert.ok(Math.hypot(dx, dy) >= 8, `Central play keeps an 8px gap from tools and progress controls (${index}): ${JSON.stringify(geometry)}`)
  }
  await central.hover()
  const hovered = await central.boundingBox()
  assert.ok(Math.abs(hovered.x + hovered.width / 2 - button.cx) <= .5
    && Math.abs(hovered.y + hovered.height / 2 - button.cy) <= .5
    && Math.abs(hovered.width - button.width) <= .5 && Math.abs(hovered.height - button.height) <= .5,
  'Hover keeps the central play target in the same position and size')
  await player.locator('.pv-wrap').evaluate(wrap => {
    window.castSmokeCentralEvents = { click: 0, dblclick: 0, fullscreen: 0 }
    window.castSmokeCentralClick = event => { if (event.target.closest?.('.center-play')) window.castSmokeCentralEvents.click++ }
    window.castSmokeCentralDoubleClick = event => { if (event.target.closest?.('.center-play')) window.castSmokeCentralEvents.dblclick++ }
    window.castSmokeCentralFullscreen = () => { window.castSmokeCentralEvents.fullscreen++ }
    window.castSmokeCentralHosts = [wrap, wrap.querySelector('.pause-state')]
    for (const host of window.castSmokeCentralHosts) {
      host.addEventListener('click', window.castSmokeCentralClick)
      host.addEventListener('dblclick', window.castSmokeCentralDoubleClick)
    }
    document.addEventListener('fullscreenchange', window.castSmokeCentralFullscreen)
  })
  try {
    // A real central-button click starts playback without activating the surface.
    await central.click()
    // The button disappears after play starts. A rapid second tap can land on
    // the underlying video; its click/double-click must respect the short guard.
    await player.locator('video').dispatchEvent('click', { bubbles: true, detail: 2 })
    await player.locator('video').dispatchEvent('dblclick', { bubbles: true })
    await page.waitForFunction(() => document.querySelector('video') && !document.querySelector('video').paused)
    await page.waitForTimeout(260)
    assert.equal(await page.evaluate(() => !!document.fullscreenElement), fullscreen)
    assert.equal(await page.evaluate(() => window.castSmokeCentralEvents.click), 0, 'Central click does not bubble to the video surface')
    await player.locator('.main-play[aria-label="暂停"]').click()
    await central.waitFor()
    await central.evaluate(button => {
      const stage = button.closest('.pause-state')
      if (window.castSmokeCentralHosts.includes(stage)) return
      window.castSmokeCentralHosts.push(stage)
      stage.addEventListener('click', window.castSmokeCentralClick)
      stage.addEventListener('dblclick', window.castSmokeCentralDoubleClick)
    })
    // Keep the target attached while testing a bubbling double-click; playing
    // removes the central button, so a real double-click would lose its target.
    await central.dispatchEvent('dblclick', { bubbles: true })
    await page.waitForTimeout(260)
    assert.equal(await page.evaluate(() => !!document.fullscreenElement), fullscreen)
    assert.equal(await player.locator('video').evaluate(video => video.paused), true)
    assert.deepEqual(await page.evaluate(() => window.castSmokeCentralEvents), { click: 0, dblclick: 0, fullscreen: 0 },
      'Central double-click stays within the button and does not toggle fullscreen or play state')
  } finally {
    await player.locator('.pv-wrap').evaluate(() => {
      for (const host of window.castSmokeCentralHosts) {
        host.removeEventListener('click', window.castSmokeCentralClick)
        host.removeEventListener('dblclick', window.castSmokeCentralDoubleClick)
      }
      document.removeEventListener('fullscreenchange', window.castSmokeCentralFullscreen)
    })
  }
  await writeFile(path.join(output, `central-play-${fullscreen ? 'fullscreen-' : ''}${width}.json`), JSON.stringify(geometry, null, 2) + '\n')
  console.log(`PASS ${width}px ${fullscreen ? 'fullscreen' : 'window'} central play geometry and click/double-click isolation`)
}

async function quickControls(page, width, screenshotPrefix = '') {
  const player = page.locator('.player-dlg')
  const speed = () => player.getByRole('button', { name: /^播放速度 .* 倍$/ })
  const speedPanel = () => player.getByRole('region', { name: '播放速度', exact: true })
  const volume = () => player.getByRole('button', { name: '音量调节', exact: true })
  const volumePanel = () => player.getByRole('region', { name: '音量调节', exact: true })
  const tracks = () => player.getByRole('button', { name: '字幕与音轨', exact: true })
  const tracksPanel = () => player.getByRole('region', { name: '字幕与音轨', exact: true })
  const settings = () => player.getByRole('button', { name: '播放设置', exact: true })
  const settingsPanel = () => player.getByRole('region', { name: '播放设置', exact: true })
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
  if (await player.locator('.resume-bar').count()) {
    await player.getByRole('button', { name: '继续观看', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2 && !document.querySelector('.seek-ov'))
  }
  const pause = player.locator('.main-play[aria-label="暂停"]')
  if (await pause.count()) await pause.click()
  await centralPlayControl(page, width)
  const controls = await player.locator('.ctl-row').locator('button').evaluateAll(buttons => buttons.filter(button => button.checkVisibility()).map(button => {
    const rect = button.getBoundingClientRect(), row = button.closest('.ctl-row').getBoundingClientRect()
    return { name: button.getAttribute('aria-label'), width: rect.width, height: rect.height,
      inside: rect.left >= row.left - 1 && rect.right <= row.right + 1 }
  }))
  assert.equal(controls.length, 6, `${width}px bottom row contains six controls`)
  assert.ok(controls.every(button => button.inside && button.width >= 43.5 && button.height >= 43.5), JSON.stringify(controls))
  assert.equal(await player.locator('.pd-head').getByRole('button', { name: '投屏', exact: true }).count(), 0)
  for (const trigger of [settings(), player.getByRole('button', { name: '投屏', exact: true }), player.getByRole('button', { name: '全屏', exact: true })]) {
    assert.equal(await trigger.evaluate(element => !!element.closest('.pv-wrap')), true, 'Corner controls belong to the picture')
    const rect = await trigger.boundingBox()
    assert.ok(rect.width >= 43.5 && rect.height >= 43.5, 'Corner control has a 44px target')
  }
  await page.screenshot({ path: path.join(output, `${screenshotPrefix}controls-${width}.png`) })
  await volume().click()
  await volumePanel().waitFor()
  await insidePlayer(page, volumePanel(), `${width}px volume panel`)
  const slider = volumePanel().getByRole('slider', { name: '音量', exact: true })
  assert.equal(await slider.getAttribute('aria-orientation'), 'vertical')
  const sliderBounds = await slider.boundingBox()
  assert.ok(sliderBounds.height > sliderBounds.width, 'Volume slider is vertical')
  await slider.fill('40')
  assert.equal(await page.locator('video').evaluate(video => video.volume), 0.4)
  await volumePanel().getByRole('button', { name: '静音', exact: true }).click()
  assert.equal(await page.locator('video').evaluate(video => video.muted), true)
  await volumePanel().getByRole('button', { name: '取消静音', exact: true }).click()
  assert.equal(await page.locator('video').evaluate(video => video.muted), false)
  // Real pointer input must increase volume upwards and reduce it downwards.
  await page.mouse.move(sliderBounds.x + sliderBounds.width / 2, sliderBounds.y + sliderBounds.height * .75)
  await page.mouse.down()
  await page.mouse.move(sliderBounds.x + sliderBounds.width / 2, sliderBounds.y + sliderBounds.height * .2, { steps: 8 })
  await page.mouse.up()
  assert.ok(await page.locator('video').evaluate(video => video.volume >= .65), 'Dragging up raises the volume')
  await page.mouse.move(sliderBounds.x + sliderBounds.width / 2, sliderBounds.y + sliderBounds.height * .8)
  await page.mouse.down()
  await page.mouse.up()
  assert.ok(await page.locator('video').evaluate(video => video.volume <= .35), 'Dragging down lowers the volume')
  await page.screenshot({ path: path.join(output, `${screenshotPrefix}volume-${width}.png`) })
  await escapePanel(page, volumePanel(), volume())
  if (width === 1440) {
    await volume().evaluate(element => element.blur())
    await page.mouse.move(0, 0)
    await volume().hover()
    await volumePanel().waitFor()
    const triggerBounds = await volume().boundingBox(), panelBounds = await volumePanel().boundingBox()
    await page.mouse.move(triggerBounds.x + triggerBounds.width / 2, (triggerBounds.y + panelBounds.y + panelBounds.height) / 2)
    await page.waitForTimeout(60)
    await page.mouse.move(panelBounds.x + panelBounds.width / 2, panelBounds.y + 20)
    await page.waitForTimeout(260)
    assert.equal(await volumePanel().isVisible(), true, 'Crossing the small gap keeps the hover volume panel open')
    await page.mouse.move(0, 0)
    await volumePanel().waitFor({ state: 'hidden' })
    await volume().hover()
    await volumePanel().waitFor()
    const hoveredSlider = volumePanel().getByRole('slider', { name: '音量', exact: true })
    const hoveredSliderBounds = await hoveredSlider.boundingBox()
    await page.mouse.move(hoveredSliderBounds.x + hoveredSliderBounds.width / 2, hoveredSliderBounds.y + hoveredSliderBounds.height * .6)
    await page.mouse.down()
    await page.mouse.move(hoveredSliderBounds.x + hoveredSliderBounds.width / 2, hoveredSliderBounds.y + hoveredSliderBounds.height * .3)
    await page.mouse.up()
    await page.mouse.move(0, 0)
    await volumePanel().waitFor({ state: 'hidden' })
    await volume().hover()
    await volumePanel().waitFor()
    await volume().click()
    await page.mouse.move(0, 0)
    await page.waitForTimeout(260)
    assert.equal(await volumePanel().isVisible(), true, 'Clicking pins the hover volume panel after the pointer leaves')
    await escapePanel(page, volumePanel(), volume())
    console.log('PASS desktop volume hover closes on leave, tolerates its gap and stays open when pinned')
  }
  await speed().click()
  await speedPanel().waitFor()
  assert.equal(await settingsPanel().count(), 0, 'Speed opens a focused menu')
  await insidePlayer(page, speedPanel(), `${width}px speed panel`)
  await speedPanel().getByRole('button', { name: '1.5×', exact: true }).click()
  assert.equal(await page.locator('video').evaluate(video => video.playbackRate), 1.5)
  await speed().click()
  await speedPanel().waitFor()
  await tracks().click()
  await tracksPanel().waitFor()
  assert.equal(await speedPanel().count(), 0, 'Opening tracks closes speed')
  assert.equal(await settingsPanel().count(), 0, 'Tracks opens a focused panel')
  await insidePlayer(page, tracksPanel(), `${width}px tracks panel`)
  const subtitles = tracksPanel().getByLabel('字幕轨道', { exact: true })
  await subtitles.selectOption('-1')
  await subtitles.selectOption('0')
  assert.equal(await subtitles.inputValue(), '0')
  if (width === 1440) {
    const audio = tracksPanel().getByLabel('音轨', { exact: true })
    await audio.selectOption('1')
    await page.waitForFunction(() => window.__jzPlayerDebug?.().engine === 'hls'
      && document.querySelector('video')?.readyState >= 2 && window.__jzPlayer?.hls()?.audioTrack === 1)
    // Opening volume by click must switch away from an already open tracks panel.
    await volume().click()
    await volumePanel().waitFor()
    assert.equal(await tracksPanel().count(), 0)
    await volumePanel().getByRole('slider', { name: '音量', exact: true }).fill('37')
    await volumePanel().getByRole('button', { name: '静音', exact: true }).click()
    await escapePanel(page, volumePanel(), volume())
    await page.locator('video').evaluate(video => {
      window.castSmokeOldVideo = video
      video.pause()
      Object.defineProperty(video, 'error', { configurable: true, get: () => ({ code: 2, message: 'Synthetic media error for element recovery' }) })
      video.dispatchEvent(new Event('error'))
    })
    await page.waitForFunction(() => document.querySelector('video') !== window.castSmokeOldVideo
      && document.querySelector('video')?.readyState >= 2, null, { timeout: 15000 })
    await player.locator('.error-card').waitFor({ state: 'hidden' })
    const restored = await page.locator('video').evaluate(video => ({ volume: video.volume, muted: video.muted, rate: video.playbackRate }))
    assert.deepEqual(restored, { volume: .37, muted: true, rate: 1.5 }, 'Replacing the video element preserves volume, mute and speed')
    assert.equal(await page.evaluate(() => window.castSmokeOldVideo.paused), true)
    await tracks().click()
    await tracksPanel().waitFor()
    await tracksPanel().getByLabel('音轨', { exact: true }).selectOption('0')
    await page.waitForFunction(() => window.__jzPlayer?.hls()?.audioTrack === 0)
    assert.equal(await page.locator('video').evaluate(video => video.playbackRate), 1.5, 'Audio switching keeps the chosen speed')
    assert.equal(await page.locator('video').evaluate(video => video.volume === .37 && video.muted), true)
    console.log('PASS HLS element recovery preserves volume, mute and speed')
  }
  await page.screenshot({ path: path.join(output, `${screenshotPrefix}tracks-${width}.png`) })
  await escapePanel(page, tracksPanel(), tracks())
  for (const [trigger, panel] of [[speed(), speedPanel()], [tracks(), tracksPanel()]]) {
    await trigger.click()
    await panel.waitFor()
    await volume().click()
    await volumePanel().waitFor()
    assert.equal(await panel.count(), 0, 'Clicking volume switches from the existing quick menu')
    await escapePanel(page, volumePanel(), volume())
  }
  await volume().click()
  await volumePanel().waitFor()
  await settings().click()
  await settingsPanel().waitFor()
  assert.equal(await volumePanel().count(), 0, 'Settings and quick panels are mutually exclusive')
  await insidePlayer(page, settingsPanel(), `${width}px settings panel`)
  await escapePanel(page, settingsPanel(), settings())
  // A click on the video surface dismisses the quick panel without pausing it.
  await speed().click()
  await speedPanel().waitFor()
  const pausedBeforeDismiss = await page.locator('video').evaluate(video => video.paused)
  await player.locator('video').click({ position: { x: 8, y: 8 } })
  await speedPanel().waitFor({ state: 'hidden' })
  assert.equal(await page.locator('video').evaluate(video => video.paused), pausedBeforeDismiss)
  console.log(`PASS ${width}px corner controls, six 44px buttons, vertical volume drag/mute, speed, tracks and panel focus`)
}

async function fullscreenControls(page) {
  const player = page.locator('.player-dlg')
  await player.getByRole('button', { name: '全屏', exact: true }).click()
  await page.waitForFunction(() => !!document.fullscreenElement)
  if (page.viewportSize().width === 1440) await overlayControls(page, 1440, true)
  await player.locator('video').evaluate(video => video.pause())
  await centralPlayControl(page, page.viewportSize().width, true)
  for (const [name, label] of [[/^播放速度 .* 倍$/, '播放速度'], ['字幕与音轨', '字幕与音轨'], ['音量调节', '音量调节'], ['播放设置', '播放设置']]) {
    const trigger = player.getByRole('button', { name, exact: typeof name === 'string' })
    await trigger.click()
    const panel = player.getByRole('region', { name: label, exact: true })
    await panel.waitFor()
    await insidePlayer(page, panel, `Fullscreen ${label}`)
    assert.equal(await panel.evaluate(element => document.fullscreenElement.contains(element)), true)
    await escapePanel(page, panel, trigger, true)
  }
  await player.getByRole('button', { name: '投屏', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '投屏到电视', exact: true })
  await picker.waitFor()
  assert.equal(await page.evaluate(() => !!document.fullscreenElement), true)
  assert.equal(await picker.evaluate(element => document.fullscreenElement.contains(element)), true)
  await page.screenshot({ path: path.join(output, 'fullscreen-casting.png') })
  await page.keyboard.press('Escape')
  await picker.waitFor({ state: 'hidden' })
  assert.equal(await page.evaluate(() => !!document.fullscreenElement), true)
  await player.getByRole('button', { name: '退出全屏', exact: true }).click()
  await page.waitForFunction(() => !document.fullscreenElement)
  console.log('PASS fullscreen quick panels, settings and casting remain inside fullscreen')
}

try {
  await mkdir(output, { recursive: true })
  let component = path.join(root, 'frontend/src/components/PlayerModal.vue')
  if (source) {
    await cp(path.join(source, 'src'), path.join(temp, 'snapshot/src'), { recursive: true })
    await symlink(path.join(root, 'frontend/node_modules'), path.join(temp, 'snapshot/node_modules'), 'dir')
    component = path.join(temp, 'snapshot/src/components/PlayerModal.vue')
  }
  await writeFile(path.join(temp, 'index.html'), '<html lang="zh-CN"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="app"></div><script type="module" src="/main.js"></script></html>')
  const template = `<button @click="kind='movie';preview=false;opened=true">合成电影</button><button @click="kind='episode';preview=false;opened=true">合成剧集</button><button @click="preview=true;kind='movie';opened=true">文件预览</button><PlayerModal v-if="opened" :version-id="kind==='movie'?fixture.movie_id:fixture.episode_id" :kind="kind" :preview="preview" title="合成视频 · 投屏验证" @close="opened=false" />`
  await writeFile(path.join(temp, 'main.js'), `import { createApp, ref } from '${root}/frontend/node_modules/vue/dist/vue.esm-bundler.js';
import PlayerModal from ${JSON.stringify(component)};
import '${root}/frontend/src/styles/tokens.css'; import '${root}/frontend/src/styles/base.css';
const fixture = await (await fetch('/__fixture/info')).json();
createApp({components:{PlayerModal},setup(){return {opened:ref(false),kind:ref('movie'),preview:ref(false),fixture}},
template:${JSON.stringify(template)}}).mount('#app');`)
  await build({ root: temp, configFile: false, envFile: false, plugins: [vue()], logLevel: 'error',
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.esm-bundler.js') } },
    worker: { format: 'es' }, build: { target: 'esnext', outDir: path.join(temp, 'dist'), reportCompressedSize: false } })
  const socket = createServer()
  await new Promise((resolve, reject) => { socket.once('error', reject); socket.listen(0, '127.0.0.1', resolve) })
  const port = socket.address().port
  await new Promise(resolve => socket.close(resolve))
  const base = `http://127.0.0.1:${port}`
  backend = spawn(path.join(root, '.venv/bin/python'), ['scripts/fixtures/casting_server.py', '--root', temp, '--port', String(port)], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
  backend.stdout.on('data', data => { backendOutput += data })
  backend.stderr.on('data', data => { backendOutput += data })
  for (let attempt = 0; attempt < 150; attempt++) {
    if (backend.exitCode != null) throw new Error(backendOutput)
    try { if ((await fetch(base + '/__fixture/info')).ok) break } catch { /* Wait for synthetic media. */ }
    if (attempt === 149) throw new Error('Isolated backend did not start: ' + backendOutput)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  const fixture = await (await fetch(base + '/__fixture/info')).json()
  const mode = async body => { assert.ok((await fetch(base + '/__fixture/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).ok) }
  browser = await chromium.launch({ headless: true })
  for (const width of [1440, 390, 375, 320]) {
    console.log(`Checking ${width}px casting flow`)
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: width <= 390, reducedMotion: 'reduce' })
    const page = await context.newPage(), errors = [], writes = []
    page.on('pageerror', error => errors.push(String(error)))
    page.on('request', request => { if (request.method() === 'POST') writes.push(new URL(request.url()).pathname) })
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
    page.setDefaultTimeout(12000)
    await page.goto(base)
    const player = () => page.getByRole('dialog', { name: '合成视频 · 投屏验证', exact: true })
    const picker = () => page.getByRole('dialog', { name: '投屏到电视', exact: true })
    if (captureOnly) await fetch(`${base}/api/stream/progress?version_id=${fixture.movie_id}`, { method: 'DELETE' })
    await page.getByRole('button', { name: '合成电影', exact: true }).click()
    if (captureOnly) {
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
      await page.locator('video').evaluate(video => { video.pause(); video.currentTime = 20 })
      await page.waitForFunction(() => document.querySelector('video')?.currentTime >= 20
        && !document.querySelector('video').seeking && document.querySelector('video').readyState >= 2)
      assert.equal(await player().locator('.resume-bar').count(), 0, 'Comparison captures show the controls without a resume prompt')
      await page.screenshot({ path: path.join(output, `controls-${width}.png`) })
      await player().getByRole('button', { name: '播放设置', exact: true }).click()
      await player().getByRole('region', { name: '播放设置', exact: true }).waitFor()
      await page.screenshot({ path: path.join(output, `settings-${width}.png`) })
      await player().getByRole('button', { name: '关闭设置', exact: true }).click()
      if (!source && (width === 1440 || width === 390)) await fullscreenTitleSurface(page)
      assert.deepEqual(errors, [])
      await context.close()
      continue
    }
    await quickControls(page, width)
    if (width === 1440 || width === 390) await overlayControls(page, width)
    if (width === 1440) await fullscreenControls(page)
    else {
      await player().getByRole('button', { name: '全屏', exact: true }).click()
      await page.waitForFunction(() => !!document.fullscreenElement)
      if (width === 390) await overlayControls(page, width, true)
      await player().locator('video').evaluate(video => video.pause())
      await centralPlayControl(page, width, true)
      await player().getByRole('button', { name: '退出全屏', exact: true }).click()
      await page.waitForFunction(() => !document.fullscreenElement)
    }
    await player().getByRole('button', { name: '投屏', exact: true }).click()
    await picker().getByRole('radio').waitFor()
    const start = picker().getByRole('button', { name: '开始投屏', exact: true })
    assert.equal(await start.isDisabled(), true)
    await picker().getByRole('radio').check()
    if (width <= 390) {
      const sizes = await picker().locator('button').evaluateAll(buttons => buttons.filter(button => button.offsetHeight).map(button => ({ width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height })))
      assert.ok(sizes.every(size => size.width >= 44 && size.height >= 44), 'Cast controls must retain 44px phone targets')
    }
    await page.screenshot({ path: path.join(output, `picker-${width}.png`) })
    await start.click()
    await picker().getByText('电视正在播放', { exact: true }).waitFor()
    console.log(`PASS ${width}px receiver start`)
    await page.screenshot({ path: path.join(output, `controller-${width}.png`) })
    const localProgressWrites = writes.filter(path => path === '/api/stream/progress').length
    assert.equal(await page.locator('video').evaluate(video => video.paused && !video.getAttribute('src')), true)
    await picker().getByRole('button', { name: '暂停', exact: true }).click()
    await picker().getByText('电视已暂停', { exact: true }).waitFor()
    await picker().getByRole('button', { name: '播放', exact: true }).click()
    await picker().getByText('电视正在播放', { exact: true }).waitFor()
    await picker().getByLabel('跳转到（秒）', { exact: true }).fill('20')
    await picker().getByRole('button', { name: '跳转', exact: true }).click()
    await picker().getByText('进度：0:20 / 1:30', { exact: true }).waitFor()
    console.log(`PASS ${width}px pause/play/seek`)
    await picker().getByLabel('电视音量设置（0–100）', { exact: true }).fill('60')
    await picker().getByRole('button', { name: '设置音量', exact: true }).click()
    await picker().getByRole('button', { name: '关闭', exact: true }).click()
    await player().getByRole('button', { name: '投屏控制', exact: true }).first().waitFor()
    if (width === 390) {
      await page.waitForTimeout(3200)
      assert.equal(await player().locator('.player-corner-controls').evaluate(element => element.classList.contains('show') && !element.inert), true,
        'Casting keeps its phone control entry reachable after inactivity')
    }
    assert.equal(await player().getByRole('button', { name: '播放设置', exact: true }).isDisabled(), true)
    assert.equal(await player().locator('.pv-ctl').count(), 0, 'Phone quick controls are absent while the TV holds playback')
    await player().getByRole('button', { name: '关闭播放器', exact: true }).click()
    const state = await (await fetch(base + '/__fixture/info')).json()
    assert.equal(state.state, 'PLAYING')
    assert.equal(state.fetched, 64)
    assert.deepEqual(state.errors, [])
    await page.getByRole('button', { name: '合成电影', exact: true }).click()
    await player().getByRole('button', { name: '投屏控制', exact: true }).first().waitFor()
    assert.equal(await page.locator('video').evaluate(video => !video.getAttribute('src')), true)
    assert.equal(writes.filter(path => path === '/api/stream/progress').length, localProgressWrites)
    const progress = await (await fetch(`${base}/api/stream/progress?version_id=${fixture.movie_id}`)).json()
    assert.equal(progress.position, 20)
    await player().getByRole('button', { name: '投屏控制', exact: true }).first().click()
    await picker().getByRole('button', { name: '结束投屏，手机播放', exact: true }).click()
    await picker().waitFor({ state: 'hidden' })
    await page.waitForFunction(() => !!document.querySelector('video')?.getAttribute('src'))
    await player().getByRole('button', { name: '投屏', exact: true }).click()
    await picker().getByRole('radio').waitFor()
    await page.keyboard.press('Escape')
    await picker().waitFor({ state: 'hidden' })
    assert.equal(await player().isVisible(), true)
    assert.equal(await player().getByRole('button', { name: '投屏', exact: true }).evaluate(element => document.activeElement === element), true)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    assert.equal(overflow, false)
    await player().getByRole('button', { name: '关闭播放器', exact: true }).click()
    // Movie and episode IDs are separate casting identities.
    await page.getByRole('button', { name: '合成剧集', exact: true }).click()
    await player().getByRole('button', { name: '投屏', exact: true }).click()
    await picker().getByRole('radio').check()
    await picker().getByRole('button', { name: '开始投屏', exact: true }).click()
    await picker().getByText('电视正在播放', { exact: true }).waitFor()
    const current = await (await fetch(`${base}/api/cast/current?version_id=${fixture.episode_id}&kind=episode`)).json()
    assert.equal(current.session.kind, 'episode')
    await picker().getByRole('button', { name: '停止投屏', exact: true }).click()
    await picker().getByText('投屏已停止', { exact: true }).waitFor()
    await picker().getByRole('button', { name: '关闭', exact: true }).click()
    await player().getByRole('button', { name: '关闭播放器', exact: true }).click()
    // Preview remains progress-free and has no casting entry.
    const before = writes.length
    await page.getByRole('button', { name: '文件预览', exact: true }).click()
    await player().waitFor()
    assert.equal(await player().getByRole('button', { name: '投屏', exact: true }).count(), 0)
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
    await player().getByRole('button', { name: '字幕与音轨', exact: true }).click()
    await player().getByRole('region', { name: '字幕与音轨', exact: true }).getByLabel('字幕轨道', { exact: true }).selectOption('-1')
    await page.keyboard.press('Escape')
    await player().getByRole('button', { name: '关闭播放器', exact: true }).click()
    assert.ok(!writes.slice(before).includes('/api/cast/sessions'))
    assert.ok(!writes.slice(before).includes('/api/stream/progress'))
    assert.deepEqual(errors, [])
    console.log(`PASS ${width}px movie/episode casting, SOAP controls, real Range fetch, progress, reconnect, phone handoff, Escape and preview isolation`)
    await context.close()
  }
  if (captureOnly) {
    const mediaBytes = await readFile(path.join(temp, 'media', '合成电影 空格%&#.mp4'))
    await writeFile(path.join(output, 'fixture.json'), JSON.stringify({ syntheticMedia: true, loopbackOnly: true,
      mediaSha256: createHash('sha256').update(mediaBytes).digest('hex'), widths: [1440, 390, 375, 320], position: 20 }, null, 2) + '\n')
    console.log(`PASS isolated ${source ? 'before' : 'after'} captures: ${output}`)
  } else {
  // Low landscape height is independent of narrow portrait width.
  const landscape = await browser.newContext({ viewport: { width: 812, height: 375 }, hasTouch: true, reducedMotion: 'reduce' })
  await landscape.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  const landscapePage = await landscape.newPage(), landscapeErrors = []
  landscapePage.on('pageerror', error => landscapeErrors.push(String(error)))
  await landscapePage.goto(base)
  await landscapePage.getByRole('button', { name: '合成电影', exact: true }).click()
  await quickControls(landscapePage, 812, 'landscape-')
  await fullscreenControls(landscapePage)
  await landscapePage.getByRole('button', { name: '关闭播放器', exact: true }).click()
  assert.deepEqual(landscapeErrors, [])
  await landscape.close()
  const page = await browser.newPage({ viewport: { width: 375, height: 812 } })
  await page.goto(base)
  await page.getByRole('button', { name: '合成电影', exact: true }).click()
  await mode({ discovery: 'empty' })
  await page.getByRole('button', { name: '投屏', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '投屏到电视', exact: true })
  await picker.getByText(/未找到设备/).waitFor()
  await mode({ discovery: 'error' })
  await picker.getByRole('button', { name: '重新搜索', exact: true }).click()
  await picker.getByRole('alert').filter({ hasText: '模拟组播网络不可用' }).waitFor()
  await mode({ discovery: 'normal' })
  await picker.getByRole('button', { name: '重新搜索', exact: true }).click()
  await picker.getByRole('radio').waitFor()
  console.log('PASS empty/error/retry states')
  const restricted = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true })
  await restricted.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
  await restricted.addInitScript(() => {
    Object.defineProperty(HTMLMediaElement.prototype, 'volume', { configurable: true, get: () => 1, set: () => {} })
  })
  const restrictedPage = await restricted.newPage(), restrictedErrors = []
  restrictedPage.on('pageerror', error => restrictedErrors.push(String(error)))
  await restrictedPage.goto(base)
  await restrictedPage.getByRole('button', { name: '文件预览', exact: true }).tap()
  await restrictedPage.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
  await restrictedPage.getByRole('button', { name: '音量调节', exact: true }).tap()
  const restrictedPanel = restrictedPage.getByRole('region', { name: '音量调节', exact: true })
  await restrictedPanel.getByText('请使用设备音量键', { exact: true }).waitFor()
  assert.equal(await restrictedPanel.getByRole('slider', { name: '音量', exact: true }).count(), 0)
  await insidePlayer(restrictedPage, restrictedPanel, 'System volume fallback')
  await restrictedPanel.getByRole('button', { name: '静音', exact: true }).tap()
  assert.equal(await restrictedPage.locator('video').evaluate(video => video.muted), true)
  await restrictedPage.screenshot({ path: path.join(output, 'system-volume-390.png') })
  await restrictedPage.getByRole('button', { name: '关闭播放器', exact: true }).tap()
  assert.deepEqual(restrictedErrors, [])
  await restricted.close()
  console.log('PASS browsers with read-only volume keep mute and use device volume buttons')
  }
} catch (error) {
  const pages = browser?.contexts().flatMap(context => context.pages()) || []
  for (const [index, page] of pages.entries()) {
    try { await page.screenshot({ path: path.join(output, `failure-${index}.png`) }) } catch { /* Keep the original failure. */ }
  }
  await writeFile(path.join(output, 'failure.json'), JSON.stringify({ error: String(error), stack: error.stack,
    backendOutput: backendOutput.slice(-12000) }, null, 2) + '\n')
  throw error
} finally {
  await browser?.close()
  if (backend && backend.exitCode == null) {
    backend.kill('SIGTERM')
    await Promise.race([new Promise(resolve => backend.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 5000))])
    if (backend.exitCode == null) backend.kill('SIGKILL')
  }
  await rm(temp, { recursive: true, force: true })
}
