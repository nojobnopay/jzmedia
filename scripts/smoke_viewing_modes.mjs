// Real Vue pages with isolated in-memory media and password sessions. No backend, NAS, or .env.
// Run: node scripts/smoke_viewing_modes.mjs
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { art, createFixture, movies } from './fixtures/uiReviewData.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const temp = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-viewing-ui-'))
const dist = path.join(temp, 'dist')
const output = path.join(root, 'output/playwright/viewing-modes')
const fixture = createFixture()
const sessions = new Map()
const policies = new Map()
const refreshJobs = new Map()
const refreshReadFailures = new Map()
const requests = [], unexpected = [], errors = []
let password = 'demo-pass', serial = 0, multipleLibraries = true, refreshStartFailure = null, server, browser
const initial = () => ({ mode: 'normal', enabled: !!password, management_unlocked: false, generation: 1, expires_at: 0 })
const visible = (movie, session) => {
  const policy = policies.get(movie.id) || { is_private: false, children_override: 'auto' }
  if (session.mode === 'private') return policy.is_private
  return !policy.is_private && (session.mode !== 'children' || policy.children_override === 'allow' || movie.id === 101 && policy.children_override !== 'block')
}
function api(url, method, body, session) {
  const key = url.pathname
  requests.push({ key, method, body, mode: session.mode })
  const fail = (code, detail) => ({ status: 403, data: { code, detail } })
  const valid = value => value === password
  if (key === '/api/viewing/session' || key === '/api/viewing/activity') return { data: { ...session } }
  if (key === '/api/viewing/password') {
    if (password && !valid(body.current_password)) return fail('protection_password_invalid', '当前密码不正确')
    password = body.new_password
    for (const current of sessions.values()) Object.assign(current, { enabled: true, mode: current.mode === 'children' ? 'children' : 'normal', management_unlocked: false, generation: current.generation + 1 })
    return { data: { ...session } }
  }
  if (key === '/api/viewing/mode') {
    if (!password) return fail('protection_setup_required', '请先设置保护密码')
    if ((session.mode === 'children' && body.mode !== 'children' || body.mode === 'private') && !valid(body.password)) return fail('protection_password_invalid', '保护密码不正确')
    Object.assign(session, { mode: body.mode, management_unlocked: body.mode === 'private', generation: session.generation + 1 })
    return { data: { ...session } }
  }
  if (key === '/api/viewing/unlock') {
    if (!valid(body.password)) return fail('protection_password_invalid', '保护密码不正确')
    session.management_unlocked = true
    return { data: { ...session } }
  }
  if (key === '/api/viewing/lock') {
    Object.assign(session, { mode: session.mode === 'children' ? 'children' : 'normal', management_unlocked: false, generation: session.generation + 1 })
    return { data: { ...session } }
  }
  if (key === '/api/viewing/refresh') {
    if (refreshStartFailure) {
      const failure = refreshStartFailure
      refreshStartFailure = null
      return failure
    }
    const job_id = `children-${refreshJobs.size + 1}`
    const value = { job_id, library_id: body.library_id ?? null, state: 'running', worker_finished: false,
      done: 1, total: 2, current: '模拟儿童资料', failed: [], error: '' }
    refreshJobs.set(job_id, value)
    return { data: value }
  }
  if (key.startsWith('/api/viewing/refresh/')) {
    const jobId = key.split('/')[4]
    const value = refreshJobs.get(jobId)
    if (!value) return { status: 404, data: { detail: '补全任务已失效' } }
    if (method === 'GET' && refreshReadFailures.has(jobId)) {
      refreshReadFailures.delete(jobId)
      return { status: 503, data: { detail: '模拟任务状态暂不可用' } }
    }
    if (key.endsWith('/cancel')) value.state = 'cancelled'
    return { data: value }
  }
  if (key === '/api/viewing/policy') {
    if (!session.management_unlocked) return fail('management_required', '请验证保护密码')
    if (method === 'PATCH') {
      for (const id of body.ids) policies.set(id, { ...policies.get(id), ...(body.is_private == null ? {} : { is_private: body.is_private }), ...(body.children_override == null ? {} : { children_override: body.children_override }) })
      return { data: { ok: true } }
    }
    const id = Number(url.searchParams.get('id'))
    return { data: { is_private: false, children_override: 'auto', children_auto_status: id === 101 ? 'allow' : id === 103 ? 'unknown' : 'block', children_reason: id === 101 ? '适合全家观看' : id === 103 ? '缺少明确分级资料' : '包含暴力主题', ...policies.get(id) } }
  }
  if (key === '/api/search' || key === '/api/movies/recent-played') {
    const items = url.searchParams.get('media_library') === '2' ? [] : movies.slice(0, 3).filter(movie => visible(movie, session))
    return { data: { items, has_more: false, total: items.length } }
  }
  if (/^\/api\/movies\/\d+$/.test(key)) {
    const movie = movies.find(movie => movie.id === Number(key.split('/').at(-1)))
    return visible(movie, session) ? { data: movie } : { status: 404, data: { detail: '影片不存在或当前不可见' } }
  }
  if (key.startsWith('/api/movies/') && key.endsWith('/similar')) return { data: { items: movies.slice(0, 3).filter(movie => visible(movie, session)) } }
  const value = fixture.api(url, method, body)
  if (key === '/api/libraries' && multipleLibraries) value.items = [...value.items, { id: 4, media_library_id: 2, media_name: '家庭录像与长期收藏',
    name: '家庭录像', source: 'local', kind: 'movie', enabled: true, read_only: true }]
  if (key === '/api/media-libraries' && multipleLibraries) value.items.push({ id: 2, name: '家庭录像与长期收藏', source: 'local', path: '/demo/family',
    movie_count: 0, episode_count: 0, enabled: true, read_only: true,
    video_libraries: [{ id: 4, media_library_id: 2, name: '家庭录像', kind: 'movie', subpath: '', enabled: true }] })
  if (value === undefined) unexpected.push(method + ' ' + key)
  return { data: value }
}
try {
  await mkdir(output, { recursive: true })
  await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(temp, 'cache'), plugins: [vue()], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (key.startsWith('/posters/') || /\/(backdrop|still)$/.test(key)) {
        res.writeHead(200, { 'Content-Type': 'image/svg+xml' }); res.end(art(Number(key.match(/-(\d+)/)?.[1] || 0), /backdrop|still/.test(key))); return
      }
      if (key.startsWith('/api/')) {
        let id = req.headers.cookie?.match(/demo_viewing=(\d+)/)?.[1]
        if (!id || !sessions.has(id)) { id = String(++serial); sessions.set(id, initial()); res.setHeader('Set-Cookie', `demo_viewing=${id}; HttpOnly; SameSite=Strict; Path=/`) }
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString()
        const result = api(url, req.method, raw ? JSON.parse(raw) : undefined, sessions.get(id))
        res.writeHead(result.status || (result.data === undefined ? 501 : 200), { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
        res.end(JSON.stringify(result.data ?? { detail: 'Unexpected isolated API' })); return
      }
      const file = /^\/assets\/[\w.-]+$/.test(key) || /^\/(favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key) ? path.join(dist, key) : path.join(dist, 'index.html')
      const contents = await readFile(file)
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' }); res.end(contents)
    } catch (error) { unexpected.push(String(error)); if (!res.headersSent) res.writeHead(500); res.end('Isolated fixture error') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 375, height: 812 }]) {
    password = 'demo-pass'
    policies.clear()
    policies.set(102, { is_private: true, children_override: 'auto' })
    const context = await browser.newContext({ viewport, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
    const page = await context.newPage()
    await page.clock.install()
    page.setDefaultTimeout(8000)
    page.on('pageerror', error => errors.push(String(error)))
    const pass = name => console.log(`PASS ${viewport.width}px ${name}`)
    const go = async target => { await page.goto(base + target); await page.waitForLoadState('networkidle') }
    const dialog = () => page.getByRole('dialog')
    const verify = async (value = 'demo-pass') => {
      await page.getByLabel('保护密码', { exact: true }).fill(value)
      await dialog().getByRole('button', { name: '确认', exact: true }).click()
    }
    const choose = async mode => {
      await page.locator('.nav-viewing').getByRole('button').first().click()
      await dialog().getByRole('button', { name: mode }).click()
      await page.waitForLoadState('networkidle')
    }
    const cards = () => page.locator('[data-browse-id]')
    const ids = async () => (await cards().evaluateAll(elements => elements.map(el => Number(el.dataset.browseId)))).sort()
    const expectIds = async expected => {
      await page.waitForFunction(expected => JSON.stringify([...document.querySelectorAll('[data-browse-id]')].map(el => Number(el.dataset.browseId)).sort()) === JSON.stringify(expected), expected)
      assert.deepEqual(await ids(), expected)
    }
    const screenshot = async name => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false, 'Viewport has no horizontal overflow')
      if (viewport.width < 701) {
        const library = await page.locator('.nav-library').boundingBox()
        const modes = await page.locator('.nav-viewing').boundingBox()
        assert.ok(Math.abs(library.y + library.height / 2 - modes.y - modes.height / 2) < 1, 'Library and viewing controls share the second navigation row')
      }
      await page.screenshot({ path: path.join(output, `${name}-${viewport.width}.png`), animations: 'disabled' })
    }
    const policyDialog = () => page.getByRole('dialog', { name: /^内容可见性/ })
    const radio = name => policyDialog().getByRole('radio', { name, exact: true })
    const policyWrites = () => requests.filter(request => request.key === '/api/viewing/policy' && request.method === 'PATCH')
    const openPolicy = async id => {
      await go(`/m/${id}`)
      await page.locator('.action-menu summary').filter({ hasText: '更多操作' }).click()
      await page.getByRole('button', { name: '内容可见性…', exact: true }).click()
      await page.getByRole('dialog').waitFor()
      if (await page.getByRole('dialog', { name: '验证保护密码', exact: true }).isVisible()) await verify()
      await policyDialog().waitFor()
    }
    const expectPolicy = async expected => {
      for (const [mode, text] of Object.entries(expected)) {
        assert.equal((await policyDialog().locator(`[data-policy-mode="${mode}"] dd`).innerText()).trim(), text)
      }
    }
    const savePolicy = async () => {
      await policyDialog().getByRole('button', { name: '保存', exact: true }).click()
      await policyDialog().waitFor({ state: 'hidden' })
      await page.waitForLoadState('networkidle')
    }
    const openBulkPolicy = async () => {
      const toolbar = page.getByRole('toolbar', { name: '多选操作' })
      if (await toolbar.count()) await toolbar.getByRole('button', { name: '取消', exact: true }).click()
      for (const id of [102, 103]) await page.locator(`[data-browse-id="${id}"]`).getByRole('button', { name: '选择', exact: true }).click()
      await toolbar.getByRole('button', { name: '可见性', exact: true }).click()
      await policyDialog().waitFor()
    }
    await go('/')
    await expectIds([101, 103])
    await screenshot('normal'); pass('normal hides private titles')
    await page.getByRole('combobox', { name: '切换媒体库' }).selectOption('2')
    await page.waitForURL(url => url.searchParams.get('media') === '2')
    await page.waitForLoadState('networkidle')
    await expectIds([])
    await screenshot('multiple-libraries'); pass('media library switch and mode controls fit with a long library name')

    // The same dialog must explain the real relationship between all three modes.
    await go('/?media=1')
    await openPolicy(103)
    await radio('普通模式可见').waitFor()
    assert.equal(await radio('普通模式可见').isChecked(), true)
    assert.equal(await radio('自动筛选').isChecked(), true)
    await expectPolicy({ normal: '显示', children: '不显示（资料不足）', private: '不显示' })
    assert.equal(await policyDialog().getByRole('button', { name: '保存', exact: true }).isDisabled(), true, 'Unchanged single-item policy has no redundant save')
    await radio('仅隐私模式可见').check()
    assert.equal(await radio('自动筛选').count(), 0, 'Private content offers no conflicting children choice')
    await expectPolicy({ normal: '不显示', children: '不显示', private: '显示' })
    await screenshot('content-policy-private')
    await radio('普通模式可见').check()
    await radio('普通和儿童模式都显示').check()
    await expectPolicy({ normal: '显示', children: '显示', private: '不显示' })
    await radio('仅隐私模式可见').check()
    await radio('普通模式可见').check()
    assert.equal(await radio('普通和儿童模式都显示').isChecked(), true, 'Temporarily selecting private preserves the unsaved children choice')
    await screenshot('content-policy-shared')
    if (viewport.width < 701) {
      await policyDialog().locator('[data-policy-mode="private"]').scrollIntoViewIfNeeded()
      await screenshot('content-policy-shared-result')
    }
    for (const target of await policyDialog().getByRole('radio').all()) {
      const label = await target.evaluate(el => {
        const rect = el.closest('label').getBoundingClientRect()
        return { width: rect.width, height: rect.height }
      })
      assert.ok(label.height >= 44, 'Radio card has at least a 44px touch target')
    }
    await radio('普通模式可见').focus()
    await page.keyboard.press('ArrowDown')
    assert.equal(await radio('仅隐私模式可见').isChecked(), true, 'Arrow keys select native radio options')
    await page.keyboard.press('ArrowUp')
    assert.equal(await radio('普通模式可见').isChecked(), true)
    const cancelledWrites = policyWrites().length
    await policyDialog().getByRole('button', { name: '取消', exact: true }).click()
    assert.equal(policyWrites().length, cancelledWrites, 'Cancel does not save the previewed choices')
    assert.equal(await page.locator('.action-menu summary').filter({ hasText: '更多操作' }).evaluate(el => el === document.activeElement), true, 'Closing restores focus to More actions')
    pass('single-item choices preview ordinary plus children, private exclusivity, keyboard and cancellation')

    await openPolicy(103)
    await radio('只在普通模式显示').check()
    await expectPolicy({ normal: '显示', children: '不显示', private: '不显示' })
    await screenshot('content-policy-normal-only')
    await savePolicy()
    assert.deepEqual(policyWrites().at(-1).body, { kind: 'movie', ids: [103], is_private: false, children_override: 'block' })
    await openPolicy(103)
    await radio('自动筛选').check()
    await expectPolicy({ normal: '显示', children: '不显示（资料不足）', private: '不显示' })
    await savePolicy()
    assert.equal(policyWrites().at(-1).body.children_override, 'auto')
    await openPolicy(101)
    await expectPolicy({ normal: '显示', children: '显示', private: '不显示' })
    await policyDialog().getByRole('button', { name: '取消', exact: true }).click()
    await openPolicy(103)
    await radio('普通和儿童模式都显示').check()
    await savePolicy()
    await openPolicy(103)
    await radio('仅隐私模式可见').check()
    await savePolicy()
    assert.equal(Object.hasOwn(policyWrites().at(-1).body, 'children_override'), false, 'Saving private does not overwrite its stored children decision')
    assert.equal(policies.get(103).children_override, 'allow')
    await choose('隐私模式')
    await verify()
    await expectIds([102, 103])
    await openPolicy(103)
    assert.equal(await radio('仅隐私模式可见').isChecked(), true)
    assert.equal(await radio('自动筛选').count(), 0)
    await radio('普通模式可见').check()
    assert.equal(await radio('普通和儿童模式都显示').isChecked(), true, 'Removing privacy shows the previously stored children decision')
    await expectPolicy({ normal: '显示', children: '显示', private: '不显示' })
    await screenshot('content-policy-remove-private')
    await policyDialog().getByRole('button', { name: '取消', exact: true }).click()
    await go('/')
    await openBulkPolicy()
    assert.equal(await radio('保持各自展示范围').isChecked(), true)
    assert.equal(await radio('保持各自儿童设置').count(), 0)
    await screenshot('content-policy-batch-keep')
    const keptWrites = policyWrites().length
    assert.equal(await policyDialog().getByRole('button', { name: '保存', exact: true }).isDisabled(), true)
    await policyDialog().getByRole('button', { name: '取消', exact: true }).click()
    assert.equal(policyWrites().length, keptWrites, 'Default batch keep leaves all policies untouched')
    await openBulkPolicy()
    await radio('普通模式可见').check()
    assert.equal(await radio('保持各自儿童设置').isChecked(), true)
    await radio('普通和儿童模式都显示').check()
    await radio('保持各自展示范围').check()
    assert.equal(await radio('普通和儿童模式都显示').count(), 0)
    assert.equal(await policyDialog().getByRole('button', { name: '保存', exact: true }).isDisabled(), true)
    await policyDialog().getByRole('button', { name: '取消', exact: true }).click()
    assert.equal(policyWrites().length, keptWrites, 'Keeping batch scope also preserves each children setting')
    await openBulkPolicy()
    await radio('普通模式可见').check()
    await radio('普通和儿童模式都显示').check()
    await expectPolicy({ normal: '显示', children: '显示', private: '不显示' })
    await screenshot('content-policy-batch-shared')
    await savePolicy()
    assert.deepEqual(policyWrites().at(-1).body, { kind: 'movie', ids: [102, 103], is_private: false, children_override: 'allow' })
    assert.equal(policies.get(102).is_private, false)
    assert.equal(policies.get(103).is_private, false)
    await expectIds([])
    pass('automatic eligibility, stored children decisions and batch keep or apply are reflected in saved requests')

    // Restore fixtures before the original cross-mode, lock and password smoke.
    policies.clear()
    policies.set(102, { is_private: true, children_override: 'auto' })
    await choose('普通模式')
    await go('/?media=1&genre=剧情')
    await page.locator('[data-browse-id="103"]').click()
    await page.locator('.action-menu summary').filter({ hasText: '更多操作' }).click()
    await page.getByRole('button', { name: '内容可见性…' }).click()
    await verify()
    await page.getByRole('dialog', { name: '内容可见性', exact: true }).waitFor()
    await radio('仅隐私模式可见').check()
    await dialog().getByRole('button', { name: '保存', exact: true }).click()
    await page.waitForURL(url => url.pathname === '/' && url.searchParams.get('media') === '1' && url.searchParams.get('genre') === '剧情')
    await page.waitForLoadState('networkidle')
    await page.locator('[data-browse-id="101"]').waitFor()
    await expectIds([101]); pass('marking hidden content retains the current library and filters')
    await choose('隐私模式')
    await verify('wrong-pass')
    await page.getByText('403 保护密码不正确', { exact: true }).waitFor()
    assert.equal(await page.getByRole('dialog', { name: '需要访问令牌' }).count(), 0)
    await verify()
    await page.locator('.nav-viewing').getByRole('button', { name: '隐私模式', exact: true }).waitFor()
    await page.waitForLoadState('networkidle')
    await expectIds([102, 103])
    await screenshot('private'); pass('private-only browsing and independent password errors')
    await page.getByRole('combobox', { name: '切换媒体库' }).selectOption('2')
    await page.waitForURL(url => url.searchParams.get('media') === '2')
    await page.waitForLoadState('networkidle')
    await expectIds([])
    await screenshot('private-multiple-libraries')
    await page.getByRole('combobox', { name: '切换媒体库' }).selectOption('1')
    await page.waitForURL(url => url.searchParams.get('media') === '1')
    await expectIds([102, 103])
    if (viewport.width === 1440) {
      await go('/settings?sec=sec-auth')
      await page.getByLabel('新保护密码', { exact: true }).fill('unsaved-pass')
      await page.getByRole('button', { name: '退出隐私模式', exact: true }).click()
      const unsaved = page.getByRole('dialog', { name: '设置尚未保存', exact: true })
      await unsaved.getByRole('button', { name: '继续编辑', exact: true }).click()
      await page.locator('.nav-viewing').getByRole('button', { name: '隐私模式', exact: true }).waitFor()
      assert.equal(await page.getByLabel('新保护密码', { exact: true }).inputValue(), 'unsaved-pass')
      await page.getByRole('button', { name: '锁定管理权限', exact: true }).click()
      await unsaved.getByRole('button', { name: '继续编辑', exact: true }).click()
      await page.getByRole('button', { name: '退出隐私模式', exact: true }).click()
      await unsaved.getByRole('button', { name: '放弃修改并离开', exact: true }).click()
      await page.locator('.nav-viewing').getByRole('button', { name: '普通模式', exact: true }).waitFor()
      await choose('隐私模式'); await verify(); await expectIds([102, 103])
      pass('explicit private exit and management lock respect unsaved settings')
    }
    const secondTab = await context.newPage()
    await secondTab.goto(base + '/')
    await secondTab.getByRole('button', { name: '隐私模式', exact: true }).waitFor()
    await page.locator('.nav-viewing').getByRole('button', { name: '退出隐私模式', exact: true }).click()
    await secondTab.getByRole('button', { name: '普通模式', exact: true }).waitFor()
    await secondTab.close()
    await page.waitForLoadState('networkidle')
    await expectIds([101]); pass('locking invalidates other tabs and cached walls')
    if (viewport.width === 1440) {
      await choose('隐私模式'); await verify(); await expectIds([102, 103])
      await page.waitForLoadState('networkidle')
      const offlinePeer = await context.newPage()
      await offlinePeer.goto(base + '/')
      await offlinePeer.waitForLoadState('networkidle')
      await offlinePeer.locator('[data-browse-id="102"]').waitFor()
      const lockedContentRequests = []
      const onLockedRequest = request => {
        if (new URL(request.url()).pathname === '/api/search') lockedContentRequests.push(request.url())
      }
      page.on('request', onLockedRequest)
      offlinePeer.on('request', onLockedRequest)
      await context.setOffline(true)
      await page.getByRole('button', { name: '退出隐私模式', exact: true }).click()
      await page.getByText('隐私内容已锁定', { exact: true }).waitFor()
      await offlinePeer.getByText('隐私内容已锁定', { exact: true }).waitFor()
      await page.waitForLoadState('networkidle')
      assert.equal(await cards().count(), 0)
      assert.equal(await offlinePeer.locator('[data-browse-id]').count(), 0, 'An offline peer tab must hide private titles immediately')
      assert.deepEqual(lockedContentRequests, [], 'No content page requests may use the old private cookie while the server lock is unconfirmed')
      page.off('request', onLockedRequest)
      offlinePeer.off('request', onLockedRequest)
      await context.setOffline(false)
      await expectIds([101])
      await offlinePeer.locator('[data-browse-id="101"]').waitFor()
      assert.equal(await offlinePeer.locator('[data-browse-id="102"]').count(), 0, 'Reconnect must not revive the peer tab private page')
      await offlinePeer.close()
      pass('offline explicit exit hides both tabs and blocks content until server lock confirmation')
      await choose('隐私模式'); await verify(); await expectIds([102, 103])
      await context.setOffline(true)
      await page.clock.fastForward(16 * 60 * 1000)
      await page.locator('.nav-viewing').getByRole('button', { name: '普通模式', exact: true }).waitFor()
      await page.getByText('隐私内容已锁定', { exact: true }).waitFor()
      assert.equal(await page.locator('[data-browse-id="102"]').count(), 0)
      await context.setOffline(false)
      await page.evaluate(() => window.dispatchEvent(new Event('focus')))
      await page.locator('.nav-viewing').getByRole('button', { name: '普通模式', exact: true }).waitFor()
      await expectIds([101])
      pass('offline idle timeout immediately removes private content and reconnect cannot restore it')
    }
    await choose('儿童模式')
    await page.locator('.nav-viewing').getByRole('button', { name: '儿童模式', exact: true }).waitFor()
    await page.waitForLoadState('networkidle')
    await expectIds([101])
    assert.equal(await page.getByRole('link', { name: '设置', exact: true }).count(), 0)
    assert.equal(await page.getByRole('button', { name: '选择', exact: true }).count(), 0)
    await screenshot('children')
    await go('/settings?sec=sec-files')
    await page.getByText('儿童模式无法打开管理页面', { exact: true }).waitFor()
    assert.equal(await page.locator('.settings-layout').count(), 0)
    await choose('普通模式')
    await verify()
    await page.locator('.nav-viewing').getByRole('button', { name: '普通模式', exact: true }).waitFor()
    pass('children persist across reloads, prevent management, and require password to exit')
    await go('/settings?sec=sec-auth')
    await page.getByRole('button', { name: '验证保护密码', exact: true }).click()
    await verify()
    await page.getByRole('heading', { name: '儿童与隐私保护' }).waitFor()
    const category = async (id, label) => {
      if (viewport.width < 701) await page.getByRole('combobox', { name: '设置分类', exact: true }).selectOption(id)
      else await page.locator('.side-nav').getByRole('button', { name: label, exact: true }).click()
      await page.waitForURL(url => url.searchParams.get('sec') === id)
      await page.waitForLoadState('networkidle')
    }
    const refresh = page.locator('.children-refresh')
    const scope = refresh.getByRole('combobox', { name: '补全范围', exact: true })
    const startRefresh = () => refresh.getByRole('button', { name: /^(重试)?补全儿童筛选资料$/, exact: true })
    const readRefresh = () => refresh.getByRole('button', { name: '刷新状态', exact: true })
    const refreshWrites = () => requests.filter(request => request.key === '/api/viewing/refresh' && request.method === 'POST')
    const currentJob = () => refreshJobs.get([...refreshJobs.keys()].at(-1))
    const expectTaskScope = async label => refresh.getByText('任务范围：' + label, { exact: true }).waitFor()
    const allLabel = '全部媒体库'
    const movieLabel = '客厅影库 / 电影 · 电影'
    const tvLabel = '客厅影库 / 剧集 · 剧集'
    await category('sec-libtools', '扫描与整理')
    await page.locator('.lib-tabs').getByRole('button', { name: /客厅影库.*电影/, exact: false }).first().click()
    await page.waitForURL(url => url.searchParams.get('library') === '1')
    await category('sec-auth', '访问保护')
    assert.equal(await scope.inputValue(), '', 'Scanning scope never supplies the children refresh default')
    assert.deepEqual(await scope.locator('option').evaluateAll(options => options.map(option => option.value)), ['', '1', '2', '3', '4'], 'The independent scope contains every video library across media libraries')
    assert.equal(await scope.locator('option[value="4"]').innerText(), '家庭录像与长期收藏 / 家庭录像 · 电影')
    await scope.selectOption('2')
    await category('sec-libtools', '扫描与整理')
    assert.match(await page.locator('.lib-tabs [aria-pressed="true"]').innerText(), /客厅影库.*电影/, 'Children scope cannot change the selected scanning library')
    await category('sec-auth', '访问保护')
    assert.equal(await scope.inputValue(), '2', 'Switching settings categories preserves the independent scope')
    const childrenScopeUrl = page.url()
    await scope.selectOption('4')
    assert.equal(await page.getByRole('combobox', { name: '切换媒体库', exact: true }).inputValue(), '1', 'Children scope cannot change the global media library')
    assert.equal(page.url(), childrenScopeUrl, 'Children scope cannot rewrite the settings route')
    await scope.selectOption('')
    await startRefresh().click()
    await refresh.getByRole('button', { name: '取消补全', exact: true }).waitFor()
    assert.deepEqual(refreshWrites().at(-1).body, { library_id: null })
    assert.equal(await scope.isDisabled(), true, 'An active worker locks its scope')
    await expectTaskScope(allLabel)
    await refresh.getByRole('button', { name: '取消补全', exact: true }).click()
    await refresh.getByText('正在取消 · 1/2 · 模拟儿童资料', { exact: true }).waitFor()
    assert.equal(await scope.isDisabled(), true, 'Cancellation keeps the scope locked while the worker finishes')
    currentJob().worker_finished = true
    await readRefresh().click()
    await page.waitForFunction(() => !document.querySelector('#children-refresh-library').disabled)
    await scope.selectOption('1')
    await expectTaskScope(allLabel)
    await screenshot('children-refresh-scope')
    await startRefresh().click()
    await expectTaskScope(movieLabel)
    assert.deepEqual(refreshWrites().at(-1).body, { library_id: 1 })
    Object.assign(currentJob(), { state: 'done', worker_finished: true, done: 2 })
    await readRefresh().click()
    await refresh.getByText('补全完成 · 2/2 · 模拟儿童资料', { exact: true }).waitFor()
    await scope.selectOption('2')
    await expectTaskScope(movieLabel)
    refreshStartFailure = { status: 409, data: { detail: '已有儿童资料补全任务正在运行' } }
    await startRefresh().click()
    await refresh.getByRole('alert').filter({ hasText: '已有儿童资料补全任务正在运行' }).waitFor()
    assert.equal(await scope.inputValue(), '2', 'A conflicting task does not expand the chosen scope')
    assert.equal(await startRefresh().isEnabled(), true)
    await startRefresh().click()
    await expectTaskScope(tvLabel)
    assert.deepEqual(refreshWrites().at(-1).body, { library_id: 2 })
    const restoredJobId = await page.evaluate(() => sessionStorage.getItem('jzmedia.childrenRefreshJob'))
    assert.equal(refreshJobs.get(restoredJobId).library_id, 2)
    refreshReadFailures.set(restoredJobId, true)
    await page.reload()
    await refresh.getByRole('alert').filter({ hasText: '模拟任务状态暂不可用' }).waitFor()
    assert.equal(await scope.isDisabled(), true, 'A task whose state cannot be restored cannot silently start another scope')
    await readRefresh().click()
    await expectTaskScope(tvLabel)
    assert.equal(await scope.inputValue(), '2', 'Reload restores the scope from the server task')
    assert.equal(await scope.isDisabled(), true)
    await refresh.getByRole('button', { name: '取消补全', exact: true }).click()
    await refresh.getByText('正在取消 · 1/2 · 模拟儿童资料', { exact: true }).waitFor()
    refreshJobs.get(restoredJobId).worker_finished = true
    await readRefresh().click()
    await page.waitForFunction(() => !document.querySelector('#children-refresh-library').disabled)
    refreshJobs.delete(restoredJobId)
    await readRefresh().click()
    await refresh.getByRole('alert').filter({ hasText: '补全任务已失效' }).waitFor()
    assert.equal(await page.evaluate(() => sessionStorage.getItem('jzmedia.childrenRefreshJob')), null, 'An expired task clears its stored reference')
    await page.reload()
    await page.waitForLoadState('networkidle')
    assert.equal(await scope.inputValue(), '', 'Opening settings without an existing task defaults to every library')
    pass('children refresh scope is independent, locks active workers, preserves task scope and restores or retries tasks')
    await page.getByLabel('当前保护密码', { exact: true }).fill('demo-pass')
    await page.getByLabel('新保护密码', { exact: true }).fill('updated-pass')
    await page.getByLabel('确认密码', { exact: true }).fill('updated-pass')
    await page.getByRole('button', { name: '修改保护密码', exact: true }).click()
    await page.getByText('验证保护密码以管理内容', { exact: true }).waitFor()
    assert.equal(await page.locator('.settings-layout').count(), 0)
    pass('password changes revoke management and unmount settings')
    await context.close()
  }
  password = ''
  multipleLibraries = false
  const fresh = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await fresh.newPage()
  page.on('pageerror', error => errors.push(String(error)))
  await page.goto(base + '/')
  await page.waitForLoadState('networkidle')
  assert.equal(await page.locator('.nav-library').count(), 0)
  const contextRow = await page.locator('.nav-context').boundingBox()
  const modeControl = await page.locator('.nav-viewing').boundingBox()
  assert.equal(contextRow.x + contextRow.width, modeControl.x + modeControl.width, 'Single-library mode control stays right-aligned')
  await page.locator('.nav-viewing').getByRole('button', { name: '普通模式', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '儿童模式' }).click()
  await page.getByLabel('保护密码', { exact: true }).fill('first-pass')
  await page.getByLabel('确认密码', { exact: true }).fill('first-pass')
  await page.getByRole('button', { name: '设置并继续', exact: true }).click()
  await page.locator('.nav-viewing').getByRole('button', { name: '儿童模式', exact: true }).waitFor()
  const otherDevice = await browser.newContext()
  const otherPage = await otherDevice.newPage()
  await otherPage.goto(base + '/')
  await otherPage.locator('.nav-viewing').getByRole('button', { name: '普通模式', exact: true }).waitFor()
  console.log('PASS first setup and independent device modes')
  await fresh.close(); await otherDevice.close()
  assert.deepEqual(unexpected, [])
  assert.deepEqual(errors, [])
  console.log(`PASS viewing mode UI: ${requests.length} isolated requests, screenshots in ${output}`)
} finally {
  await browser?.close()
  if (server) await new Promise(resolve => server.close(resolve))
  await rm(temp, { recursive: true, force: true })
}
