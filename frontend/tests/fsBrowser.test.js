import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer, h, nextTick, reactive } from 'vue'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createFsDirectoryLoader, fsCopyIssues, fsJobRunning, fsListUrl, fsMatchStatus, fsPasteSnapshot, fsSelect, fsVisibleRows } from '../src/fsBrowser.js'
import { useFsBrowser } from '../src/useFsBrowser.js'
import { loadSfc } from './helpers/loadSfc.js'

function deferred() {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}
function fixture(t, request) {
  const props = reactive({ active: false, initialLibId: 7, videoLibs: [{ id: 7, name: '电影', kind: 'movie' }, { id: 8, name: '剧集', kind: 'tv' }], pendingChanges: null })
  const events = []
  const navigations = []
  let state
  const renderer = createRenderer({
    createElement: () => ({}), insert() {}, remove() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null, createText: () => ({}), createComment: () => ({}), setText() {}, setComment() {},
  })
  const app = renderer.createApp({ setup() { state = useFsBrowser(props, (...event) => events.push(event), request, { push: target => navigations.push(target) }); return () => h('div') } })
  app.mount({})
  t.after(() => app.unmount())
  return { state, props, events, navigations }
}
const listing = (path = '') => ({ path, dirs: [], files: [], crumbs: [], fs_writable: true, has_more: false })

test('loads every file page, preserving full directory tree and captured library scope', async () => {
  const requests = []
  const loader = createFsDirectoryLoader(async url => {
    const params = new URL(url, 'http://local').searchParams
    requests.push(Object.fromEntries(params))
    const offset = Number(params.get('offset'))
    return { ...listing('Season 1'), dirs: [{ name: 'Extras', rel: 'Season 1/Extras' }], offset, total_files: 1002, has_more: offset === 0,
      files: Array.from({ length: offset === 0 ? 1000 : 2 }, (_, i) => ({ name: `e${offset + i}.mkv`, rel: `Season 1/e${offset + i}.mkv` })) }
  })
  let output
  await loader.load(8, 'Season 1', value => { output = value })
  assert.equal(output.files.length, 1002)
  assert.equal(output.dirs.length, 1)
  assert.deepEqual(requests.map(r => [r.library, r.path, r.offset]), [['8', 'Season 1', '0'], ['8', 'Season 1', '1000']])
})

test('late pages after navigation or cancellation never replace the current directory', async () => {
  const late = deferred()
  let oldSignal
  const loader = createFsDirectoryLoader((url, options) => {
    if (url.includes('path=old')) { oldSignal = options.signal; return late.promise }
    return Promise.resolve(listing('new'))
  })
  const publications = []
  const old = loader.load(7, 'old', value => publications.push(value.path))
  await loader.load(8, 'new', value => publications.push(value.path))
  assert.equal(oldSignal.aborted, true)
  late.resolve(listing('old'))
  assert.equal(await old, false)
  assert.deepEqual(publications, ['new'])
})

test('a malformed empty continuation fails instead of looping indefinitely', async () => {
  const loader = createFsDirectoryLoader(async () => ({ ...listing(), files: [], has_more: true, offset: 0 }))
  await assert.rejects(loader.load(7, '', () => assert.fail('must not publish partial directory')), /分页未前进/)
})

test('sort and search keep folders first; Shift selection follows visible order', () => {
  const rows = fsVisibleRows([{ name: '合集', rel: 'd' }], [
    { name: 'Episode 10.mkv', rel: '10', kind: 'feature', size: 10 },
    { name: 'Episode 2.mkv', rel: '2', kind: 'feature', size: 2 },
    { name: 'Episode 1.mkv', rel: '1', kind: 'feature', size: 1 },
  ])
  assert.deepEqual(rows.map(r => r.rel), ['d', '1', '2', '10'])
  const first = fsSelect(rows, [], null, '1')
  assert.deepEqual(fsSelect(rows, first.selected, first.anchor, '10', { shiftKey: true }).selected, ['1', '2', '10'])
  assert.deepEqual(fsSelect(rows, ['d'], '1', '2', { shiftKey: true, ctrlKey: true }).selected, ['d', '1', '2'])
  assert.deepEqual(fsVisibleRows([], rows.filter(r => !r.isDir), 'EPISODE 2').map(r => r.rel), ['2'])
  assert.equal(fsMatchStatus({ kind: 'feature', episode_id: 3, tmdb_id: 90 }), '已匹配')
  assert.equal(fsMatchStatus({ kind: 'feature', match_status: 'unregistered' }), '待扫描')
})

test('paste captures immutable source and destination and rejects cross-library operations', () => {
  const clipboard = { mode: 'copy', library_id: 7, rels: ['source/a.mkv'] }
  const snapshot = fsPasteSnapshot(clipboard, 7, 'destination')
  clipboard.rels.push('later.mkv')
  clipboard.library_id = 8
  assert.deepEqual(snapshot, { library_id: 7, to_dir: 'destination', from: ['source/a.mkv'], mode: 'copy' })
  assert.throws(() => fsPasteSnapshot(clipboard, 7, ''), /跨视频库/)
})

test('copy confirmation executes its previewed directory even if the displayed path changes', async t => {
  const requests = []
  const { state, events } = fixture(t, async (url, options) => {
    const body = options?.body && JSON.parse(options.body)
    requests.push({ url, body })
    if (url === '/api/fs/copy') return body.dry_run ? { needs_confirm: true, total: 1, files: 1, bytes: 3e9, conflicts: [] } : { job_id: 'copy-1', total: 1, bytes: 3e9 }
    return listing()
  })
  state.writable.value = true
  state.path.value = 'confirmed-target'
  state.clipboard.value = { library_id: 7, mode: 'copy', rels: ['source/film.mkv'] }
  await state.paste()
  assert.equal(state.prompt.value.type, 'copy')
  state.path.value = 'different-display-path'
  state.clipboard.value = { library_id: 8, mode: 'copy', rels: ['different-file'] }
  await state.confirmPaste()
  const execution = requests.find(r => r.body?.dry_run === false)
  assert.deepEqual(execution.body, { library_id: 7, from: ['source/film.mkv'], to_dir: 'confirmed-target', dry_run: false })
  assert.equal(state.hasRunningTask.value, true)
  assert.deepEqual(events.at(-1), ['changed', { library_id: 7, operation: 'copy_started', count: 0 }])
})

test('editing a rename invalidates confirmation synchronously and requires a new preview', async t => {
  const requests = []
  const { state } = fixture(t, async (url, options) => {
    const body = JSON.parse(options.body)
    requests.push(body)
    assert.equal(url, '/api/fs/rename')
    assert.equal(body.dry_run, true)
    return { plans: [{ from: body.from, to: body.name, status: 'planned', followers: [{ from: 'a.srt', to: body.name + '.srt', status: 'planned' }] }] }
  })
  state.writable.value = true
  state.files.value = [{ name: 'a.mkv', rel: 'a.mkv', kind: 'feature' }]
  state.selection.value = ['a.mkv']
  state.beginRename()
  state.inputName.value = 'first.mkv'
  await state.submitName()
  assert.equal(state.prompt.value.plans[0].to, 'first.mkv')
  state.inputName.value = 'second.mkv'
  assert.equal(state.prompt.value.plans, null)
  await state.submitName()
  assert.equal(requests.length, 2)
  assert.equal(requests[1].name, 'second.mkv')
})

test('rename completion reports actual library and moved results even without aggregate count', async t => {
  const { state, events } = fixture(t, async (url, options) => {
    if (url.startsWith('/api/fs/list')) return listing()
    const body = JSON.parse(options.body)
    return body.dry_run ? { plans: [{ from: body.from, to: body.name, status: 'planned' }] } : { results: [{ status: 'moved', followed: 2 }] }
  })
  state.writable.value = true
  state.files.value = [{ name: 'a.mkv', rel: 'a.mkv', kind: 'feature' }]
  state.selection.value = ['a.mkv']
  state.beginRename()
  state.inputName.value = 'b.mkv'
  await state.submitName()
  await state.submitName()
  assert.deepEqual(events.at(-1), ['changed', { library_id: 7, operation: 'rename', count: 1 }])
})

test('feature deletion requires a visible preview before confirm and excludes non-empty directories', async t => {
  const requests = []
  const { state, events } = fixture(t, async (url, options) => {
    if (url.startsWith('/api/fs/list')) return listing()
    const body = JSON.parse(options.body)
    requests.push(body)
    return body.dry_run ? { plans: [
      { rel: 'e.mkv', kind: 'feature', episode_id: 22, requires_confirm: true },
      { rel: 'a.srt', kind: 'subtitle', requires_confirm: false },
      { rel: 'folder', kind: 'dir', status: 'dir_not_empty' },
    ] } : { deleted: 2, results: [{ status: 'deleted' }, { status: 'deleted' }] }
  })
  state.writable.value = true
  state.selection.value = ['e.mkv', 'a.srt', 'folder']
  await state.beginDelete()
  assert.equal(requests.length, 1)
  assert.equal(requests[0].dry_run, true)
  assert.equal(state.prompt.value.type, 'delete')
  await state.confirmDelete()
  assert.deepEqual(requests[1], { library_id: 7, paths: ['e.mkv', 'a.srt'], dry_run: false, confirm: true })
  assert.deepEqual(events.at(-1), ['changed', { library_id: 7, operation: 'delete', count: 2 }])
})

test('library switching waits for parent acceptance; scans always use the accepted context', async t => {
  const { state, props, events } = fixture(t, async () => listing())
  state.switchLibrary(8)
  assert.equal(state.libraryId.value, 7)
  assert.deepEqual(events.at(-1), ['library-change', { library_id: 8 }])
  state.scan()
  assert.deepEqual(events.at(-1), ['scan', { library_id: 7 }])
  props.initialLibId = 8
  await nextTick()
  state.scan()
  assert.deepEqual(events.at(-1), ['scan', { library_id: 8 }])
})

test('restored copy jobs keep background progress without blocking another library', async t => {
  const { state, props } = fixture(t, async () => listing())
  props.pendingChanges = { library_id: 7, pending: true, active_jobs: [{ job_id: 'restored', library_id: 7, state: 'running', done: 1 }] }
  await nextTick()
  assert.equal(state.hasRunningTask.value, true)
  assert.equal(state.pending.value, true)
  props.initialLibId = 8
  await nextTick()
  assert.equal(state.hasRunningTask.value, false)
  assert.equal(state.pending.value, false)
  assert.equal(state.jobs.value[0].job_id, 'restored')
  props.pendingChanges = { library_id: 7, active_jobs: [{ job_id: 'restored', library_id: 7, state: 'running' }] }
  await nextTick()
  assert.equal(state.jobs.value.length, 1)
})

test('file manager template initializes with explicit library controls and accessible operations', async () => {
  const component = await loadSfc(new URL('../src/components/FsBrowser.vue', import.meta.url), { 'vue-router': { useRouter: () => ({ push() {} }) } })
  const html = await renderToString(createSSRApp(component, { active: false, initialLibId: 7, videoLibs: [{ id: 7, name: '示例电影', kind: 'movie' }] }))
  assert.match(html, /aria-label="搜索当前目录"/)
  assert.match(html, /aria-label="更多文件操作"/)
  assert.match(html, /aria-label="视频库目录"/)
  assert.doesNotMatch(html, /新文件名/)
})

test('a cancelled copy remains active until its writer finishes cleanup', () => {
  assert.equal(fsJobRunning({ state: 'cancelled', worker_finished: false }), true)
  assert.equal(fsJobRunning({ state: 'cancelled', worker_finished: true }), false)
  assert.equal(fsJobRunning({ state: 'done' }), false)
})


test('server page-size clamping still visits every offset without skipping Unicode names', async () => {
  const offsets = []
  const longName = '很长的中文电影名称'.repeat(8) + '.mkv'
  const directory = '国产电影/待核对 & 新增'
  const loader = createFsDirectoryLoader(async url => {
    const query = new URL(url, 'http://local').searchParams
    assert.equal(query.get('path'), directory)
    const offset = Number(query.get('offset'))
    offsets.push(offset)
    return { ...listing(directory), offset, limit: 2, total_files: 5, has_more: offset < 4,
      files: Array.from({ length: offset < 4 ? 2 : 1 }, (_, i) => ({ name: longName, rel: directory + '/' + (offset + i) + longName })) }
  })
  let output
  await loader.load(8, directory, value => { output = value })
  assert.deepEqual(offsets, [0, 2, 4])
  assert.equal(output.files.length, 5)
  assert.equal(output.files[4].name, longName)
  assert.equal(new URL(fsListUrl(8, directory), 'http://local').searchParams.get('path'), directory)
})

test('late history navigation cannot reset the index of a more recent Back action', async t => {
  const old = deferred()
  const { state } = fixture(t, async url => {
    const requested = new URL(url, 'http://local').searchParams.get('path')
    return requested === 'earlier' ? old.promise : listing(requested)
  })
  state.history.value = ['', 'earlier', 'latest']
  state.historyIndex.value = 2
  state.path.value = 'latest'
  const first = state.historyMove(-1)
  await state.historyMove(-1)
  old.resolve(listing('earlier'))
  await first
  assert.equal(state.path.value, '')
  assert.equal(state.historyIndex.value, 0)
})

test('copy completion queues refresh behind navigation instead of reopening the old folder', async t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  const waiting = deferred()
  const requestedPaths = []
  const { state, props } = fixture(t, async url => {
    if (url.startsWith('/api/fs/copy/')) return { state: 'done', worker_finished: true, done: 1, results: [{ status: 'copied' }] }
    const requested = new URL(url, 'http://local').searchParams.get('path')
    requestedPaths.push(requested)
    if (requestedPaths.length === 1) return waiting.promise
    return listing(requested)
  })
  state.path.value = 'old'
  props.pendingChanges = { library_id: 7, active_jobs: [{ job_id: 'copy-completes', library_id: 7, state: 'running' }] }
  await nextTick()
  const navigating = state.navigate('新的目的目录')
  t.mock.timers.tick(1000)
  await nextTick()
  await nextTick()
  assert.deepEqual(requestedPaths, ['新的目的目录'])
  waiting.resolve(listing('新的目的目录'))
  await navigating
  assert.equal(state.path.value, '新的目的目录')
  assert.deepEqual(requestedPaths, ['新的目的目录', '新的目的目录', ''])
})

test('database path conflicts and empty rename plans cannot be confirmed', async t => {
  let status = 'conflict_db_occupied'
  const requests = []
  const { state } = fixture(t, async (url, options) => {
    const body = JSON.parse(options.body)
    requests.push(body)
    assert.equal(body.dry_run, true)
    return { plans: status ? [{ from: body.from, to: body.name, status }] : [] }
  })
  state.writable.value = true
  state.files.value = [{ name: '旧片名.mkv', rel: '旧片名.mkv' }]
  state.selection.value = ['旧片名.mkv']
  state.beginRename()
  state.inputName.value = '新片名.mkv'
  await state.submitName()
  await state.submitName()
  assert.equal(requests.length, 1)
  status = null
  state.inputName.value = '另一个片名.mkv'
  await state.submitName()
  await state.submitName()
  assert.equal(requests.length, 2)
})

test('copy tasks expose per-file failures and metadata warnings even in done state', t => {
  const { state } = fixture(t, async () => listing())
  const job = { state: 'done', done: 3, total: 3, results: [
    { from: '成功.mkv', status: 'copied' },
    { from: '失败.mkv', status: 'error: permission denied' },
    { from: '待扫描.mkv', status: 'copied_scan_warn: scan failed' },
  ] }
  assert.deepEqual(fsCopyIssues(job).map(item => item.from), ['失败.mkv', '待扫描.mkv'])
  assert.match(state.jobText(job), /2 项需核对/)
})

test('foreground writes block directory and library navigation', async t => {
  const requests = []
  const { state, events } = fixture(t, async url => { requests.push(url); return listing() })
  state.busy.value = true
  state.history.value = ['', 'current']
  state.historyIndex.value = 1
  state.navigate('other')
  state.switchLibrary(8)
  await state.historyMove(-1)
  assert.deepEqual(requests, [])
  assert.deepEqual(events, [])
  assert.equal(state.historyIndex.value, 1)
})


test('previewing keeps library, selection and directory; details navigation remains explicit', async t => {
  const { state, events, navigations } = fixture(t, async () => listing())
  const row = { rel: '子目录/影片.mkv', name: '影片.mkv', kind: 'feature', movie_id: 35 }
  state.path.value = '子目录'
  state.files.value = [row]
  state.selection.value = [row.rel]
  state.open(row)
  assert.equal(state.previewFile.value.movie_id, 35)
  assert.equal(new URL(state.previewFile.value.url, 'http://local').searchParams.get('library'), '7')
  assert.equal(new URL(state.previewFile.value.url, 'http://local').searchParams.get('path'), row.rel)
  assert.deepEqual(navigations, [])
  assert.deepEqual(events, [])
  state.closePreview()
  assert.equal(state.path.value, '子目录')
  assert.deepEqual(state.selection.value, [row.rel])
  state.viewDetails(row)
  assert.deepEqual(navigations, ['/m/35'])
})

test('hiding or changing the video library clears an open preview', async t => {
  const { state, props } = fixture(t, async () => listing())
  props.active = true
  await nextTick()
  await nextTick()
  state.open({ rel: 'poster.jpg', name: 'poster.jpg' })
  assert.ok(state.previewFile.value)
  props.active = false
  assert.equal(state.previewFile.value, null)
  state.open({ rel: 'poster.jpg', name: 'poster.jpg' })
  props.initialLibId = 8
  await nextTick()
  assert.equal(state.previewFile.value, null)
})

test('directory loading remains distinct from an empty result until every page completes', async t => {
  const finalPage = deferred()
  const { state } = fixture(t, async url => {
    const offset = Number(new URL(url, 'http://local').searchParams.get('offset'))
    return offset === 0 ? { ...listing('目录'), offset: 0, has_more: true, total_files: 3,
      files: [{ name: '1.mkv', rel: '目录/1.mkv' }, { name: '2.mkv', rel: '目录/2.mkv' }] } : finalPage.promise
  })
  assert.equal(state.loadState.value, 'waiting')
  assert.equal(state.hasLoaded.value, false)
  const reading = state.load('目录')
  await nextTick()
  assert.equal(state.loadState.value, 'loading')
  assert.equal(state.hasLoaded.value, false)
  assert.equal(state.loadedCount.value, 2)
  assert.equal(state.totalCount.value, 3)
  assert.deepEqual(state.files.value, [], 'partial pages must not be presented as a completed directory')
  finalPage.resolve({ ...listing('目录'), offset: 2, total_files: 3, files: [{ name: '3.mkv', rel: '目录/3.mkv' }] })
  await reading
  assert.equal(state.loadState.value, 'ready')
  assert.equal(state.hasLoaded.value, true)
  assert.equal(state.files.value.length, 3)
})

test('failed navigation retains the previous list and retry uses the failed target, not the old path', async t => {
  const targets = []
  let failing = true
  const { state } = fixture(t, async url => {
    const target = new URL(url, 'http://local').searchParams.get('path')
    targets.push(target)
    if (target === '无法读取的目录' && failing) throw new Error('source offline')
    return { ...listing(target), files: target === '原目录' ? [{ name: 'old.mkv', rel: '原目录/old.mkv' }] : [] }
  })
  await state.load('原目录')
  await state.navigate('无法读取的目录')
  assert.equal(state.loadState.value, 'error')
  assert.equal(state.loadError.value, 'source offline')
  assert.equal(state.path.value, '原目录')
  assert.equal(state.loadTarget.value, '无法读取的目录')
  assert.equal(state.files.value[0].name, 'old.mkv')
  failing = false
  await state.retryLoad()
  assert.equal(targets.at(-1), '无法读取的目录')
  assert.equal(state.path.value, '无法读取的目录')
  assert.equal(state.loadState.value, 'ready')
  assert.equal(state.loadError.value, '')
  assert.equal(state.files.value.length, 0, 'only the successful retry establishes an empty folder')
})

test('a cancelled request failure cannot erase a newer directory loading indicator or publish its error', async t => {
  let rejectOld
  const old = new Promise((resolve, reject) => { rejectOld = reject })
  const latest = deferred()
  const { state } = fixture(t, url => new URL(url, 'http://local').searchParams.get('path') === 'old' ? old : latest.promise)
  const first = state.load('old')
  const second = state.load('new')
  rejectOld(new Error('已取消'))
  await first
  assert.equal(state.loading.value, true)
  assert.equal(state.loadState.value, 'loading')
  assert.equal(state.loadError.value, '')
  assert.equal(state.loadTarget.value, 'new')
  latest.resolve(listing('new'))
  await second
  assert.equal(state.loadState.value, 'ready')
  assert.equal(state.path.value, 'new')
})

test('switching libraries resets completed/error state and rejects late errors from the previous library', async t => {
  let rejectOld
  const old = new Promise((resolve, reject) => { rejectOld = reject })
  const { state, props } = fixture(t, () => old)
  const reading = state.load('old-library-directory')
  props.initialLibId = 8
  await nextTick()
  assert.equal(state.loadState.value, 'waiting')
  assert.equal(state.hasLoaded.value, false)
  assert.equal(state.loadTarget.value, '')
  assert.equal(state.loadedCount.value, 0)
  rejectOld(new Error('previous library offline'))
  await reading
  assert.equal(state.loadState.value, 'waiting')
  assert.equal(state.loadError.value, '')
})

test('actual browser template shows a spinner while pending, retry after failure, and empty only after success', async t => {
  let rejectRead
  let succeed = false
  const waiting = new Promise((resolve, reject) => { rejectRead = reject })
  const { state } = fixture(t, () => succeed ? Promise.resolve(listing()) : waiting)
  const component = await loadSfc(new URL('../src/components/FsBrowser.vue', import.meta.url), {
    'vue-router': { useRouter: () => ({ push() {} }) },
    '../useFsBrowser.js': { useFsBrowser: () => state },
  })
  const render = () => renderToString(createSSRApp(component, { active: true, initialLibId: 7, videoLibs: [{ id: 7, name: '电影' }] }))
  const reading = state.load('')
  let html = await render()
  assert.match(html, /class="ios-spin"/)
  assert.match(html, /正在加载目录/)
  assert.doesNotMatch(html, /此文件夹为空/)
  rejectRead(new Error('无法连接媒体库'))
  await reading
  html = await render()
  assert.match(html, /目录加载失败/)
  assert.match(html, /重试加载目录/)
  assert.doesNotMatch(html, /此文件夹为空/)
  succeed = true
  await state.retryLoad()
  html = await render()
  assert.match(html, /此文件夹为空/)
  assert.doesNotMatch(html, /class="ios-spin"|目录加载失败/)
})


test('background completion cannot clear a failed destination; queued refresh waits for successful retry', async t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  let rejectDestination
  let destinationReady = false
  const failedDestination = new Promise((resolve, reject) => { rejectDestination = reject })
  const requestedPaths = []
  const { state, props } = fixture(t, async url => {
    if (url.startsWith('/api/fs/copy/')) return { state: 'done', worker_finished: true, done: 1, results: [{ status: 'copied' }] }
    const requested = new URL(url, 'http://local').searchParams.get('path')
    requestedPaths.push(requested)
    return requested === '目标目录' && !destinationReady ? failedDestination : listing(requested)
  })
  await state.load('原目录')
  props.pendingChanges = { library_id: 7, active_jobs: [{ job_id: 'copy-completes', library_id: 7, state: 'running' }] }
  await nextTick()
  const navigating = state.navigate('目标目录')
  t.mock.timers.tick(1000)
  await nextTick()
  await nextTick()
  rejectDestination(new Error('source offline'))
  await navigating
  assert.equal(state.loadState.value, 'error')
  assert.equal(state.loadTarget.value, '目标目录')
  assert.equal(state.path.value, '原目录')
  assert.match(state.loadError.value, /offline/)
  assert.deepEqual(requestedPaths, ['原目录', '目标目录'])
  destinationReady = true
  await state.retryLoad()
  assert.equal(state.loadState.value, 'ready')
  assert.equal(state.path.value, '目标目录')
  assert.equal(state.loadError.value, '')
  assert.deepEqual(requestedPaths, ['原目录', '目标目录', '目标目录', '目标目录', ''])
})
