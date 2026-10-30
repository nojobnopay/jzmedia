// Isolated Vue/hls.js + synthetic MacCMS/API + real FFmpeg media, no production app or database.
// Run: node scripts/smoke_online_prototype.mjs [--demo]
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
if (args.length === 1 && args[0] === '--help') {
  console.log('Usage: node scripts/smoke_online_prototype.mjs [--demo]')
  process.exit(0)
}
assert.ok(args.length <= 1 && args.every(arg => arg === '--demo'), 'Usage: node scripts/smoke_online_prototype.mjs [--demo]')
const demo = args.includes('--demo')
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-prototype-'))
const output = path.join(root, 'output/playwright/online-prototype')
const dist = path.join(work, 'dist')
const entryId = 'virtual:jzmedia-online-prototype'
const checks = [], errors = [], unexpected = []
const fingerprints = {}
let backend, browser, backendLog = ''
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

async function freePort() {
  const server = createServer()
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}

async function readPlayer(page) {
  return page.locator('video').evaluate(element => ({ time: element.currentTime, paused: element.paused,
    ready: element.readyState, width: element.videoWidth, height: element.videoHeight,
    frames: element.getVideoPlaybackQuality?.().totalVideoFrames || 0,
    duration: element.duration, src: element.currentSrc, error: element.error?.code || null }))
}

async function assertProgress(page, minimum = 0.8) {
  await page.waitForFunction(min => {
    const video = document.querySelector('video')
    return video?.currentTime > min && video.readyState >= 2 && !video.paused && video.videoWidth > 0
  }, minimum)
  const before = await readPlayer(page)
  await page.waitForFunction(time => document.querySelector('video')?.currentTime > time + 0.45, before.time)
  const after = await readPlayer(page)
  assert.ok(after.frames > 0 && after.time > before.time, 'Actual decoded video frames and media clock must advance')
  return { before, after }
}

async function checkLayout(page, width) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
  assert.equal(overflow, false, `${width}px has no horizontal overflow`)
  if (width <= 700) {
    const small = await page.locator('button:visible').evaluateAll(buttons => buttons.map(button => {
      const rect = button.getBoundingClientRect()
      return { label: button.getAttribute('aria-label') || button.textContent.trim(), width: rect.width, height: rect.height }
    }).filter(button => button.width < 43.5 || button.height < 43.5))
    assert.deepEqual(small, [], 'All visible phone buttons have at least 44px touch targets')
  }
}

try {
  for (const file of ['scripts/fixtures/OnlinePrototypePreview.vue', 'scripts/smoke_online_prototype.mjs',
    'frontend/src/components/JzButton.vue', 'frontend/src/components/JzDialog.vue', 'frontend/src/playbackLifecycle.js',
    'scripts/online_prototype/server.py', 'scripts/online_prototype/core.py', 'scripts/online_prototype/fixtures.py', 'scripts/online_fixture/media.py']) {
    fingerprints[file] = createHash('sha256').update(await readFile(path.join(root, file))).digest('hex')
  }
  const source = `
import { createApp } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import Preview from ${JSON.stringify(path.join(root, 'scripts/fixtures/OnlinePrototypePreview.vue'))};
createApp(Preview).mount('#app');`
  const result = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: false, cacheDir: path.join(work, 'vite-cache'), logLevel: 'error',
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      'hls.js': path.join(root, 'frontend/node_modules/hls.js/dist/hls.mjs') } },
    plugins: [vue(), { name: 'online-prototype-preview',
      resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null }],
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { preview: entryId } } } })
  const entry = result.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const css = result.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item => item.fileName)
  await writeFile(path.join(dist, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width, initial-scale=1"><title>jzmedia · 在线点播验证</title><link rel="icon" href="data:,">'
    + css.map(file => `<link rel="stylesheet" href="/${file}">`).join('')
    + `</head><body><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`)
  const port = await freePort(), base = `http://127.0.0.1:${port}`
  backend = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_prototype.server',
    '--root', work, '--port', String(port), '--static-dir', dist],
  { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, stdio: ['ignore', 'pipe', 'pipe'] })
  backend.stdout.on('data', data => { backendLog = (backendLog + data.toString()).slice(-12000) })
  backend.stderr.on('data', data => { backendLog = (backendLog + data.toString()).slice(-12000) })
  const deadline = Date.now() + 90000
  let ready = false
  while (Date.now() < deadline && !ready) {
    if (backend.exitCode !== null) throw new Error(`prototype backend exited: ${backendLog}`)
    try { ready = (await fetch(`${base}/api/prototype/search?q=`, { signal: AbortSignal.timeout(1000) })).ok } catch { /* Still generating media. */ }
    if (!ready) await delay(150)
  }
  assert.ok(ready, `prototype backend must become ready: ${backendLog}`)
  if (demo) {
    console.log(`在线点播隔离原型：${base}/`)
    console.log('临时合成片源；仅监听回环地址；不读取正式 .env、数据库或 NAS 片库。Ctrl+C 停止并清理。')
    await new Promise(resolve => { process.once('SIGINT', resolve); process.once('SIGTERM', resolve) })
  } else {
    await mkdir(output, { recursive: true })
    browser = await chromium.launch({ headless: true })
    for (const width of [1440, 390]) {
      for (const transport of ['mp4', 'hls']) {
        const context = await browser.newContext({ viewport: { width, height: width > 700 ? 1040 : 844 },
          hasTouch: width <= 700, serviceWorkers: 'block' })
        await context.route('**/*', route => {
          const url = new URL(route.request().url())
          if (url.origin !== base) { unexpected.push(url.href); return route.abort() }
          return route.continue()
        })
        const page = await context.newPage(), mediaResponses = [], playSessions = []
        page.setDefaultTimeout(20000)
        page.on('pageerror', error => errors.push(String(error)))
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
        page.on('response', response => {
          if (response.url().includes('/api/prototype/media/')) mediaResponses.push({ path: new URL(response.url()).pathname,
            status: response.status(), type: response.headers()['content-type'] || '' })
          if (response.url().includes('/api/prototype/play/')) {
            void response.json().then(data => playSessions.push(data)).catch(() => {})
          }
        })
        await page.goto(base)
        await page.getByRole('heading', { name: '在线点播', exact: true }).waitFor()
        const query = page.getByRole('searchbox', { name: '搜索影片或剧集' })
        await query.fill('没有这个节目')
        await page.getByRole('button', { name: '搜索', exact: true }).click()
        await page.getByRole('heading', { name: '没有找到相关内容' }).waitFor()
        await query.fill('在线')
        await page.getByRole('button', { name: '搜索', exact: true }).click()
        await page.getByRole('button', { name: '查看剧集' }).click()
        await page.locator('[data-episode-id="ep-1"]').waitFor()
        await page.getByLabel('播放格式').selectOption(transport)
        await checkLayout(page, width)
        await page.screenshot({ path: path.join(output, `browse-${width}-${transport}.png`), fullPage: true })

        const start = performance.now()
        await page.locator('[data-episode-id="ep-1"]').click()
        const dialog = page.getByRole('dialog')
        await dialog.waitFor()
        await page.waitForFunction(() => {
          const video = document.querySelector('video')
          return video?.readyState >= 2 && video.videoWidth > 0 && video.getVideoPlaybackQuality?.().totalVideoFrames > 0
        })
        const firstFramesWithinMs = Math.round(performance.now() - start)
        const advancing = await assertProgress(page)
        assert.equal(await page.locator('.playback').getAttribute('data-engine'), transport === 'hls' ? 'hls.js' : '原生 MP4')
        await dialog.getByRole('button', { name: '暂停', exact: true }).click()
        const pausedAt = await readPlayer(page)
        await delay(350)
        const stillPaused = await readPlayer(page)
        assert.equal(stillPaused.paused, true)
        assert.ok(Math.abs(stillPaused.time - pausedAt.time) < 0.2, 'Pause stops the real media clock')
        await dialog.getByRole('button', { name: '前进 10 秒', exact: true }).click()
        await page.waitForFunction(time => document.querySelector('video')?.currentTime >= time + 9.5, pausedAt.time)
        const seek = await readPlayer(page)
        await dialog.getByRole('button', { name: '播放', exact: true }).click()
        await assertProgress(page, seek.time)
        await dialog.getByRole('button', { name: '暂停', exact: true }).click()
        await page.waitForFunction(() => document.querySelector('video')?.paused)
        await dialog.getByRole('button', { name: '播放', exact: true }).waitFor()
        const savedAt = (await readPlayer(page)).time
        await checkLayout(page, width)
        await page.screenshot({ path: path.join(output, `player-${width}-${transport}.png`) })
        const oldSession = playSessions.at(-1)
        assert.ok(oldSession?.session_id)
        await dialog.getByRole('button', { name: '关闭播放器' }).click()
        await dialog.waitFor({ state: 'hidden' })
        assert.equal(await page.locator('video').count(), 0, 'Closing unmounts the media element')
        await page.waitForFunction(() => document.activeElement?.dataset.episodeId === 'ep-1')
        let revoked = false
        for (let attempt = 0; attempt < 20 && !revoked; attempt++) {
          const response = await fetch(new URL(oldSession.media_url, base), { method: 'HEAD' })
          revoked = [404, 410].includes(response.status)
          if (!revoked) await delay(50)
        }
        assert.ok(revoked, 'Closing releases the backend session and makes old media unavailable')

        await page.locator('[data-episode-id="ep-1"]').click()
        await assertProgress(page, savedAt - 0.5)
        const resumedAt = (await readPlayer(page)).time
        assert.ok(resumedAt >= savedAt - 0.5, 'Reopening resumes the saved episode position')
        await dialog.getByRole('button', { name: '第 2 集', exact: true }).click()
        await page.getByRole('heading', { name: '第 2 集 · 绿色测试片' }).waitFor()
        const secondEpisode = await assertProgress(page)
        assert.ok(secondEpisode.after.time < 6, 'Changing episode starts its own timeline')
        await page.screenshot({ path: path.join(output, `episode-2-${width}-${transport}.png`) })
        await page.keyboard.press('Escape')
        await dialog.waitFor({ state: 'hidden' })
        assert.equal(await page.locator('video').count(), 0)
        if (transport === 'hls') {
          assert.ok(mediaResponses.some(response => /mpegurl/i.test(response.type)), 'HLS playlists really loaded')
          assert.ok(mediaResponses.some(response => /mp4|mp2t/i.test(response.type)), 'HLS init or video segments really loaded')
        } else assert.ok(mediaResponses.some(response => response.status === 206), 'MP4 supports real Range reads')
        checks.push({ width, transport, firstFramesWithinMs, advancing, pause: { pausedAt, stillPaused }, seek,
          savedAt, resumedAt, secondEpisode, sessionReleased: true, mediaResponses,
          passed: ['search', 'empty search', 'detail', 'episode selection', 'decoded frames', 'advancing clock',
            'pause', 'seek', 'resume', 'episode isolation', 'close release', 'Escape close', 'layout',
            ...(width <= 700 ? ['44px touch targets'] : [])] })
        console.log(`PASS ${width}px ${transport.toUpperCase()}：搜索/选集、真实解码、暂停/跳转/续播、关闭回收`)
        await context.close()
      }
    }
    assert.deepEqual(errors, [], 'No browser runtime/console errors')
    assert.deepEqual(unexpected, [], 'No external browser requests')
    await writeFile(path.join(output, 'manifest.json'), JSON.stringify({ generatedAt: new Date().toISOString(),
      scope: 'Isolated Vue/hls.js prototype with synthetic MacCMS data and FFmpeg media. No PlayerModal integration, no public-source playback, no real phone/Safari/TV validation.',
      fingerprints, checks, errors, unexpected }, null, 2) + '\n')
    console.log(`证据：${path.join(output, 'manifest.json')}`)
  }
} catch (error) {
  await mkdir(output, { recursive: true })
  await writeFile(path.join(output, 'failure.json'), JSON.stringify({ error: String(error), checks, errors, unexpected, backendLog }, null, 2) + '\n')
  throw error
} finally {
  await browser?.close()
  if (backend && backend.exitCode === null) {
    backend.kill('SIGTERM')
    await Promise.race([new Promise(resolve => backend.once('exit', resolve)), delay(2000)])
    if (backend.exitCode === null) backend.kill('SIGKILL')
  }
  await rm(work, { recursive: true, force: true })
}
