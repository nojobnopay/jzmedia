import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const bindText = (element, binding) => { element.props.value = binding.value }
const vue = { ...Vue, vShow: {}, vModelText: { mounted: bindText, updated: bindText }, vModelSelect: {}, vModelCheckbox: {} }
const blank = { setup: () => () => Vue.h('div') }
const button = (ui, label) => ui.find(node => node.type === 'button' && nodeText(node).trim() === label)
const movie = (id, metadata = {}) => ({ id, title: '影片' + id, library_id: 7, tmdb_id: 100 + id,
  watched: 0, needs_review: true, genres: [], persons: [], versions: [], ...metadata })
const writes = ui => ui.requests.filter(request => ['POST', 'PATCH'].includes(request.opts.method) && request.path !== '/api/stream/versions')
const environments = new WeakMap()

async function detailPage(t, options = {}) {
  let environment = environments.get(t)
  if (!environment) {
    const descriptors = new Map(['window', 'document', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
    for (const key of ['window', 'document']) Object.defineProperty(globalThis, key, {
      configurable: true, value: { addEventListener() {}, removeEventListener() {} },
    })
    environment = { pages: [] }
    environments.set(t, environment)
    t.after(() => {
      for (const page of environment.pages) page.app.unmount()
      for (const [key, descriptor] of descriptors) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else delete globalThis[key]
      }
    })
  }
  Object.defineProperty(globalThis, 'fetch', { configurable: true, value: (...args) => options.fetch?.(...args) ?? Promise.resolve({ ok: false }) })
  const route = Vue.reactive({ path: options.path || '/m/1', params: { id: '1' }, query: {} })
  const requests = [], polls = new Map()
  const player = {
    props: ['versionId'], emits: ['close', 'watched'],
    setup(props, { emit, expose }) {
      expose({ saveFinal: () => options.saveFinal?.() ?? Promise.resolve() })
      return () => Vue.h('section', [Vue.h('span', '播放器' + props.versionId),
        Vue.h('button', { onClick: () => emit('close') }, '关闭测试播放器'),
        Vue.h('button', { onClick: () => emit('watched') }, '模拟播放已看')])
    },
  }
  const editor = {
    emits: ['saved', 'matched'], setup: (_props, { emit }) => () => Vue.h('section', [
      Vue.h('button', { onClick: () => emit('saved') }, '模拟资料保存'),
      Vue.h('button', { onClick: () => emit('matched', { background: { poster: true } }) }, '模拟匹配完成'),
    ]),
  }
  const component = await loadSfc(new URL('../src/views/Detail.vue', import.meta.url), {
    vue, 'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
    '../api.js': { posterUrl: path => path, scopedArtworkUrl: path => path, api: (path, opts = {}) => {
      const request = { path, opts }
      requests.push(request)
      const override = options.api?.(path, opts, request)
      if (override !== undefined) return override
      const id = path.match(/^\/api\/movies\/(\d+)$/)?.[1]
      if (id) return Promise.resolve(movie(Number(id), options.metadata))
      if (path === '/api/stream/versions') {
        const vid = JSON.parse(opts.body).movie_id
        return Promise.resolve({ versions: [{ version_id: vid, playable: true, method: 'transcode',
          duration: 600, audio_count: 0, sub_count: 0 }], best_version_id: vid })
      }
      if (path === '/api/health') return Promise.resolve({ ffmpeg: true })
      if (path.startsWith('/api/stream/progress?')) return Promise.resolve(null)
      if (path.includes('/similar')) return Promise.resolve({ items: [] })
      if (path.endsWith('/files') || path.endsWith('/collection-hint')) return Promise.resolve(null)
      if (['POST', 'PATCH'].includes(opts.method)) {
        Object.assign(request, deferred())
        return request.promise
      }
      throw new Error('Unexpected API request: ' + path)
    } },
    '../libraries.js': { currentMediaId: () => 7, isRemoteVideoLib: () => false },
    '../caps.js': { getCaps: () => options.caps?.() ?? Promise.resolve({}) },
    '../usePolling.js': { usePolling: tick => {
      const poll = { tick, starts: 0, active: Vue.ref(false), start() { this.starts++; this.active.value = true }, stop() { this.active.value = false } }
      polls.set(tick.name, poll)
      return poll
    } },
    '../useFocusTrap.js': { useFocusTrap() {} },
    '../viewing.js': { isChildren: Vue.ref(false), viewing: Vue.reactive(options.viewing || { enabled: false }),
      ensureManagement: () => options.ensureManagement?.() ?? Promise.resolve(true) },
    '../components/PlayerModal.vue': { default: player }, '../components/MovieEditPanel.vue': { default: editor },
    ...Object.fromEntries(['ContentPolicyButton', 'MediaBackdrop', 'MediaOverview', 'SimilarRow', 'CastWall', 'CrewRow',
      'MovieUploadPanel', 'MovieFileManager', 'MovieCollectionsPanel'].map(name => [`../components/${name}.vue`, { default: blank }])),
  })
  const ui = renderHarness(component)
  environment.pages.push(ui)
  await flush()
  return { ...ui, route, requests, polls, navigate(id) { route.path = '/m/' + id; route.params.id = String(id) },
    leave() { route.path = '/'; route.params = {} } }
}

test('a late movie read after saving watched state cannot replace another detail', async t => {
  const followup = deferred()
  let delay = false
  const ui = await detailPage(t, { api: path => path === '/api/movies/1' && delay ? followup.promise : undefined })
  button(ui, '标记已看').props.onClick()
  delay = true
  writes(ui)[0].resolve({ ok: true })
  await flush()
  ui.navigate(2)
  await flush()
  followup.resolve(movie(1, { title: '旧影片资料', watched: 1 }))
  await flush()
  assert.match(ui.text(), /影片2/)
  assert.doesNotMatch(ui.text(), /旧影片资料/)
})

test('leaving movie detail after a submitted write prevents new reads or feedback', async t => {
  for (const label of ['标记已看', '匹配正确', '更新资料']) {
    const ui = await detailPage(t)
    button(ui, label).props.onClick()
    const write = writes(ui)[0]
    assert.equal(write.opts.signal, undefined)
    ui.leave()
    await flush()
    const count = ui.requests.length
    write.resolve({ ok: true, changed: true })
    await flush()
    assert.equal(ui.requests.length, count)
    assert.ok(ui.requests.every(request => !request.path.includes('undefined')))
    ui.app.unmount()
  }
})

test('late confirmation errors cannot change a newer movie or release its active operation', async t => {
  const ui = await detailPage(t)
  button(ui, '匹配正确').props.onClick()
  ui.navigate(2)
  await flush()
  button(ui, '匹配正确').props.onClick()
  assert.equal(writes(ui).length, 2)
  writes(ui)[0].reject(new Error('上一影片错误'))
  await flush()
  assert.equal(button(ui, '匹配正确').props.disabled, true)
  assert.doesNotMatch(ui.text(), /上一影片错误/)
  writes(ui)[1].resolve({ ok: true })
  await flush()
})

test('feedback already shown for a movie is cleared when the next movie opens', async t => {
  const ui = await detailPage(t)
  button(ui, '标记已看').props.onClick()
  writes(ui)[0].reject(new Error('本片保存失败'))
  await flush()
  assert.match(ui.text(), /本片保存失败/)
  ui.navigate(2)
  await flush()
  assert.match(ui.text(), /影片2/)
  assert.doesNotMatch(ui.text(), /本片保存失败/)
})

test('movie confirmation and watched saves remain single-flight and refresh the same movie normally', async t => {
  for (const label of ['标记已看', '匹配正确']) {
    let changed = false
    const ui = await detailPage(t, { path: '/m/1/', api: path => path === '/api/movies/1' && changed
      ? Promise.resolve(movie(1, { watched: 1, needs_review: false })) : undefined })
    const action = button(ui, label).props.onClick
    action(); action()
    assert.equal(writes(ui).length, 1)
    changed = true
    writes(ui)[0].resolve({ ok: true })
    await flush()
    assert.match(ui.text(), /已保存/)
    assert.ok(button(ui, '标记未看'))
    assert.equal(button(ui, '匹配正确'), undefined)
    ui.app.unmount()
  }
})

test('metadata refresh polling stops during its timer or ignores a late movie read after navigation', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  for (const phase of ['timer', 'request']) {
    const late = deferred()
    let polling = false
    const ui = await detailPage(t, { api: path => path === '/api/movies/1' && polling ? late.promise : undefined })
    button(ui, '更新资料').props.onClick()
    writes(ui)[0].resolve({ changed: true })
    await flush()
    polling = true
    if (phase === 'request') { t.mock.timers.tick(3000); await flush() }
    ui.navigate(2)
    await flush()
    const count = ui.requests.length
    late.resolve(movie(1, { title: '旧轮询资料', poster_path: 'old.jpg' }))
    t.mock.timers.tick(60000)
    await flush()
    assert.equal(ui.requests.length, count)
    assert.match(ui.text(), /影片2/)
    assert.doesNotMatch(ui.text(), /旧轮询资料|影片资料已更新/)
    ui.app.unmount()
  }
})

test('closing a player waits for its save but cannot refresh progress after leaving the movie', async t => {
  const save = deferred()
  const ui = await detailPage(t, { saveFinal: () => save.promise })
  button(ui, '播放影片').props.onClick()
  await flush()
  assert.match(ui.text(), /播放器1/)
  button(ui, '关闭测试播放器').props.onClick()
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  save.resolve()
  await flush()
  assert.equal(ui.requests.length, count)
  assert.doesNotMatch(ui.text(), /播放器1/)
})

test('progress from another version cannot replace the selected version progress', async t => {
  const old = deferred(), selected = deferred()
  const ui = await detailPage(t, {
    metadata: { versions: [{ id: 1, file_path: 'one.mkv' }, { id: 11, file_path: 'eleven.mkv' }] },
    api: path => path === '/api/stream/progress?version_id=1' ? old.promise
      : path === '/api/stream/progress?version_id=11' ? selected.promise : undefined,
  })
  const selector = ui.find(node => node.type === 'select' && node.props['aria-label'] === '播放版本')
  selector.props['onUpdate:modelValue'](11)
  selector.props.onChange()
  selected.resolve({ position: 90, position_text: '当前版本进度', duration: 600 })
  await flush()
  old.resolve({ position: 180, position_text: '旧版本进度', duration: 600 })
  await flush()
  assert.match(ui.text(), /当前版本进度/)
  assert.doesNotMatch(ui.text(), /旧版本进度/)
})

test('a player watched event fixes its movie identity before the write and stops following after navigation', async t => {
  const ui = await detailPage(t)
  button(ui, '播放影片').props.onClick()
  await flush()
  button(ui, '模拟播放已看').props.onClick()
  assert.deepEqual(JSON.parse(writes(ui)[0].opts.body), { ids: [1], ops: { watched: true } })
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  writes(ui)[0].resolve({ ok: true })
  await flush()
  assert.equal(ui.requests.length, count)
  assert.doesNotMatch(ui.text(), /已保存|播放器1/)
})

test('prewarm snapshots its version and quality, blocks duplicate starts and ignores a late startup', async t => {
  const caps = deferred()
  let checks = 0
  const ui = await detailPage(t, { caps: () => ++checks === 1 ? Promise.resolve({}) : caps.promise })
  const start = button(ui, '开始预转码').props.onClick
  start(); start()
  await flush()
  assert.equal(button(ui, '开始预转码').props['aria-busy'], true)
  ui.find(node => node.props.id === 'pre-quality').props['onUpdate:modelValue']('720p')
  caps.resolve({ h264: true })
  await flush()
  assert.equal(writes(ui).length, 1)
  assert.deepEqual(JSON.parse(writes(ui)[0].opts.body), { version_id: 1, quality: 'auto', audio: 0, caps: { h264: true } })
  ui.navigate(2)
  await flush()
  writes(ui)[0].resolve({ job_id: 'old-prewarm' })
  await flush()
  assert.equal(ui.polls.get('pollPrewarm').starts, 0)
  assert.doesNotMatch(ui.text(), /已开始，后台转码中/)
})

test('prewarm capability detection cannot submit a write after the movie has been left', async t => {
  const caps = deferred()
  let checks = 0
  const ui = await detailPage(t, { caps: () => ++checks === 1 ? Promise.resolve({}) : caps.promise })
  button(ui, '开始预转码').props.onClick()
  ui.leave()
  await flush()
  caps.resolve({})
  await flush()
  assert.equal(writes(ui).length, 0)
})

test('prewarm and repair status responses cannot update a new movie or restart reads', async t => {
  for (const kind of ['prewarm', 'repair']) {
    const status = deferred()
    const ui = await detailPage(t, { api: path => path === '/api/stream/prewarm/job-1'
      || path === '/api/jobs/rebuild-meta/job-1' ? status.promise : undefined })
    if (kind === 'repair') {
      button(ui, '修复资料文件').props.onClick()
      await flush()
      button(ui, '确认修复本片资料文件').props.onClick()
    } else button(ui, '开始预转码').props.onClick()
    await flush()
    writes(ui)[0].resolve({ job_id: 'job-1' })
    await flush()
    const poll = ui.polls.get(kind === 'repair' ? 'pollMeta' : 'pollPrewarm')
    const pending = poll.tick()
    ui.navigate(2)
    await flush()
    const count = ui.requests.length
    status.resolve(kind === 'repair' ? { state: 'done', failed: [] } : { status: 'done' })
    await pending
    assert.equal(poll.active.value, false)
    assert.equal(ui.requests.length, count)
    assert.doesNotMatch(ui.text(), /已修复所选资料文件|已就绪/)
    ui.app.unmount()
  }
})

test('late repair startup or series creation cannot change a newer movie or its busy state', async t => {
  for (const kind of ['repair', 'series']) {
    const ui = await detailPage(t, { api: path => path.endsWith('/collection-hint')
      ? Promise.resolve({ collection_tmdb_id: 7, collection_name: '系列', already_collected: false }) : undefined })
    if (kind === 'repair') {
      button(ui, '修复资料文件').props.onClick()
      await flush()
      button(ui, '确认修复本片资料文件').props.onClick()
    } else {
      const create = button(ui, '创建系列合集').props.onClick
      create(); create()
    }
    assert.equal(writes(ui).length, 1)
    ui.navigate(2)
    await flush()
    const count = ui.requests.length
    writes(ui)[0].resolve(kind === 'repair' ? { job_id: 'old-repair' } : { id: 1, name: '旧系列', member_count: 2 })
    await flush()
    assert.equal(ui.requests.length, count)
    assert.equal(ui.polls.get('pollMeta').starts, 0)
    assert.doesNotMatch(ui.text(), /旧系列|后台重写中/)
    ui.app.unmount()
  }
})

test('poster originals finishing after close or unmount cannot recreate object URLs', async t => {
  const created = t.mock.method(URL, 'createObjectURL', () => 'blob:late-poster')
  for (const leave of ['close', 'unmount']) {
    const body = deferred()
    const ui = await detailPage(t, { metadata: { poster_path: 'poster.jpg' },
      fetch: () => Promise.resolve({ ok: true, blob: () => body.promise }) })
    ui.find(node => node.type === 'img' && node.props.title === '查看大图').props.onClick()
    await flush()
    if (leave === 'close') button(ui, '关闭').props.onClick()
    else ui.app.unmount()
    body.resolve(new Blob(['isolated poster']))
    await flush()
    assert.equal(created.mock.callCount(), 0)
  }
})

test('poster candidates cannot leak into the next movie or reopen a closed preview', async t => {
  const candidates = deferred()
  const ui = await detailPage(t, { metadata: { poster_path: 'poster.jpg' },
    api: path => path === '/api/movies/1/posters' ? candidates.promise : undefined })
  ui.find(node => node.type === 'img' && node.props.title === '查看大图').props.onClick()
  await flush()
  button(ui, '换海报').props.onClick()
  ui.navigate(2)
  await flush()
  candidates.resolve({ items: [{ file_path: '/old.jpg', thumb_url: '/old-thumb.jpg', width: 100, height: 200 }] })
  await flush()
  assert.equal(ui.find(node => node.props.class === 'poster-grid'), undefined)
  assert.equal(ui.find(node => node.props.title === '100×200'), undefined)
  ui.find(node => node.type === 'img' && node.props.title === '查看大图').props.onClick()
  await flush()
  assert.equal(ui.find(node => node.props.class === 'poster-grid'), undefined)
})

test('poster saves block duplicate submissions and late results cannot reopen the next movie preview', async t => {
  const ui = await detailPage(t, { metadata: { poster_path: 'poster.jpg' },
    api: path => path.endsWith('/posters') ? Promise.resolve({ items: [{ file_path: '/new.jpg', thumb_url: '/new-thumb.jpg', width: 100, height: 200 }] }) : undefined })
  ui.find(node => node.type === 'img' && node.props.title === '查看大图').props.onClick()
  await flush()
  button(ui, '换海报').props.onClick()
  await flush()
  const choose = ui.find(node => node.type === 'button' && node.props.title === '100×200').props.onClick
  choose(); choose()
  await flush()
  assert.equal(writes(ui).length, 1)
  assert.equal(writes(ui)[0].path, '/api/movies/1/poster')
  assert.deepEqual(JSON.parse(writes(ui)[0].opts.body), { file_path: '/new.jpg' })
  assert.equal(button(ui, '关闭').props.disabled, true)
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  writes(ui)[0].resolve({ ok: true })
  await flush()
  assert.equal(ui.requests.length, count)
  assert.equal(button(ui, '关闭'), undefined)
  assert.match(ui.text(), /影片2/)
})

test('an archive recommendation fixes the movie and library, blocks repeats and ignores late completion after navigation', async t => {
  for (const leave of [false, true]) {
    const ui = await detailPage(t, { metadata: { poster_path: 'poster.jpg' }, api: path => path.endsWith('/organize-hint')
      ? Promise.resolve({ needs: true, plans: [{ from: '待整理/片.mkv', to: '电影/影片1/片.mkv' }], params: { library_id: 7, mode: 'relocate' } }) : undefined })
    button(ui, '重新匹配').props.onClick()
    await flush()
    button(ui, '模拟匹配完成').props.onClick()
    await flush()
    const apply = button(ui, '立即归档').props.onClick
    apply(); apply()
    await flush()
    assert.equal(writes(ui).length, 1)
    assert.deepEqual(JSON.parse(writes(ui)[0].opts.body), { mode: 'relocate', library_id: 7, ids: [1], dry_run: false })
    assert.equal(button(ui, '稍后整理').props.disabled, true)
    if (leave) { ui.navigate(2); await flush() }
    const count = ui.requests.length
    writes(ui)[0].resolve({ results: [{ status: 'moved' }] })
    await flush()
    if (leave) {
      assert.equal(ui.requests.length, count)
      assert.match(ui.text(), /影片2/)
      assert.doesNotMatch(ui.text(), /归档完成|已匹配成功，可以归档了/)
    } else assert.match(ui.text(), /归档完成：1\/1 项/)
    ui.app.unmount()
  }
})

test('file management authorization cannot open the next movie tab after navigation', async t => {
  const authorization = deferred()
  const ui = await detailPage(t, { viewing: { enabled: true, management_unlocked: false }, ensureManagement: () => authorization.promise })
  button(ui, '文件与版本 0').props.onClick()
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  authorization.resolve(true)
  await flush()
  assert.equal(ui.requests.length, count)
  assert.equal(button(ui, '影片资料').props['aria-pressed'], true)
})

test('an old matching callback cannot request archive hints or reopen a dialog on another movie', async t => {
  const reloaded = deferred()
  let reads = 0
  const ui = await detailPage(t, { api: path => path === '/api/movies/1' && ++reads > 2 ? reloaded.promise : undefined })
  button(ui, '重新匹配').props.onClick()
  await flush()
  button(ui, '模拟匹配完成').props.onClick()
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  reloaded.resolve(movie(1))
  await flush()
  assert.equal(ui.requests.length, count)
  assert.match(ui.text(), /影片2/)
  assert.doesNotMatch(ui.text(), /已匹配成功，可以归档了/)
})
