// Shared browser-fixture lifecycle. Imports stay lightweight for Node contract tests.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import path from 'node:path'

export const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
export const stopped = child => !child || child.exitCode !== null || child.signalCode !== null
export const sha256 = data => createHash('sha256').update(data).digest('hex')

export function killOwned(child, signal = 'SIGTERM') {
  if (!child?.pid || stopped(child)) return
  try { process.kill(-child.pid, signal) } catch { try { child.kill(signal) } catch { /* Already stopped. */ } }
}

async function waitForExit(child, timeout) {
  if (stopped(child)) return
  await new Promise(resolve => {
    const done = () => { clearTimeout(timer); child.removeListener('exit', done); resolve() }
    const timer = setTimeout(done, timeout)
    child.once('exit', done)
    if (stopped(child)) done()
  })
}

export async function stopOwned(child, { termMs = 4000, killMs = 1000 } = {}) {
  killOwned(child)
  await waitForExit(child, termMs)
  if (!stopped(child)) {
    killOwned(child, 'SIGKILL')
    await waitForExit(child, killMs)
  }
  return stopped(child)
}

export async function freePort() {
  const server = createServer()
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}

export async function waitUntil(predicate, message, { timeout = 12000, interval = 60, checkStop = () => {} } = {}) {
  const deadline = Date.now() + timeout
  let lastError
  while (Date.now() < deadline) {
    checkStop()
    try { const value = await predicate(); if (value) return value } catch (error) { lastError = error }
    await delay(interval)
  }
  throw new Error(`${message}${lastError ? `: ${lastError.message}` : ''}`)
}

const sessionCounters = ['registry.sessions', 'registry.pending_sessions', 'registry.active_requests',
  'registry.reserved_bytes', 'registry.resources', 'registry.resource_url_bytes']
const catalogCounters = ['pending_requests', 'catalogue_active', 'catalogue_owners', 'api_pool.active',
  'image_pool.active', 'artwork_cache.inflight', 'artwork_cache.pending', 'images.active',
  'admission.active', 'admission.owners', 'admission.background', 'admission.preempt_waiters']

// Do not coerce missing or malformed telemetry to zero: a leak must not become
// a clean pass when an endpoint changes its response shape.
export function assertFixtureIdle(metrics, kind) {
  assert.ok(kind === 'player' || kind === 'catalog', 'Unknown online fixture kind')
  const counters = [...sessionCounters, ...(kind === 'player' ? ['pending_resolutions'] : catalogCounters),
    'local_rows.movies', 'local_rows.tv_episodes', 'local_rows.playback_progress']
  for (const name of counters) {
    const value = name.split('.').reduce((object, key) => object?.[key], metrics)
    assert.ok(Number.isSafeInteger(value) && value >= 0, `Missing or invalid idle metric: ${name}`)
    assert.equal(value, 0, `Online fixture resource still active: ${name}`)
  }
  return metrics
}

export async function waitForFixtureIdle(readMetrics, kind, { message = 'All online fixture resources must be released',
  checkStop, timeout = 12000 } = {}) {
  const started = performance.now()
  const metrics = await waitUntil(async () => assertFixtureIdle(await readMetrics(), kind), message, { checkStop, timeout })
  return { releaseMs: Math.round(performance.now() - started), metrics }
}

export async function backendChildren(child) {
  const children = new Set()
  for (const task of await readdir(`/proc/${child.pid}/task`)) {
    try {
      const value = await readFile(`/proc/${child.pid}/task/${task}/children`, 'utf8')
      for (const pid of value.trim().split(/\s+/)) if (pid) children.add(Number(pid))
    } catch (error) { if (!['ENOENT', 'ESRCH'].includes(error.code)) throw error }
  }
  return [...children]
}

export async function mediaHashes(folder) {
  const result = {}
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name)
      assert.equal(entry.isSymbolicLink(), false, 'Fixture media must not include symlinks')
      if (entry.isDirectory()) await visit(file)
      else if (entry.isFile()) result[path.relative(folder, file)] = sha256(await readFile(file))
    }
  }
  await visit(folder)
  return Object.fromEntries(Object.entries(result).sort(([first], [second]) => first.localeCompare(second)))
}

export function readPlayer(page) {
  return page.locator('video').evaluate(video => ({ time: video.currentTime, paused: video.paused,
    ready: video.readyState, width: video.videoWidth, height: video.videoHeight,
    frames: video.getVideoPlaybackQuality?.().totalVideoFrames || 0, duration: video.duration,
    muted: video.muted, volume: video.volume, rate: video.playbackRate, src: video.currentSrc,
    error: video.error?.code || null }))
}

export async function buildOnlinePreview({ root, work, dist, kind }) {
  assert.ok(kind === 'player' || kind === 'catalog', 'Unknown online preview kind')
  const { build } = await import('../../frontend/node_modules/vite/dist/node/index.js')
  const { default: vue } = await import('../../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs')
  const app = kind === 'catalog'
  const preview = app ? 'OnlineAppPreview.vue' : 'OnlinePlayerPreview.vue'
  const entryId = `virtual:jzmedia-online-${kind}`
  const source = `
import { createApp, h } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import Preview from ${JSON.stringify(path.join(root, 'scripts/fixtures', preview))};
${app ? `import ${JSON.stringify(path.join(root, 'frontend/src/styles/browse.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/settings.css'))};
import { createMemoryHistory, createRouter } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue-router/dist/vue-router.mjs'))};
import Online from ${JSON.stringify(path.join(root, 'frontend/src/views/Online.vue'))};
import Sources from ${JSON.stringify(path.join(root, 'frontend/src/components/OnlineSourcesPanel.vue'))};
const router = createRouter({history:createMemoryHistory(), routes:[{path:'/online',component:Online},{path:'/settings',component:{render:()=>h('div',{class:'settings-layout',style:{display:'block',padding:'20px 0'}},[h('div',{class:'settings-main'},[h(Sources)])])}},{path:'/:pathMatch(.*)*',redirect:'/online'}]});
router.push('/online').then(() => createApp(Preview).use(router).mount('#app'));` : "createApp(Preview).mount('#app');"}`
  const result = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: app ? path.join(root, 'frontend/public') : false,
    cacheDir: path.join(work, 'vite-cache'), logLevel: 'error',
    define: { 'import.meta.env.DEV': 'true' }, worker: { format: 'es' },
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      'hls.js': path.join(root, 'frontend/node_modules/hls.js/dist/hls.mjs'),
      ...(app ? { 'vue-router': path.join(root, 'frontend/node_modules/vue-router/dist/vue-router.mjs') } : {}) } },
    plugins: [vue(), { name: `online-${kind}-preview`, resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null }],
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { preview: entryId } } } })
  const entry = result.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const css = result.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item => item.fileName)
  await writeFile(path.join(dist, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    + `<meta name="viewport" content="width=device-width,initial-scale=1"><title>jzmedia · 在线${app ? '来源' : '播放器'}隔离验证</title>`
    + '<link rel="icon" href="data:,">' + css.map(file => `<link rel="stylesheet" href="/${file}">`).join('')
    + `</head><body><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`)
}
