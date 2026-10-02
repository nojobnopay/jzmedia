import test from 'node:test'
import assert from 'node:assert/strict'
import { clearFilePreviewElement, filePreviewKind, filePreviewPlayer, filePreviewReadError, filePreviewUrl } from '../src/filePreview.js'
import { useFilePreview } from '../src/useFilePreview.js'

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { resolve, promise } }
const response = (value, extra = {}) => ({ ok: true, status: 200, headers: { get: () => null }, text: async () => value, ...extra })
const file = (name = 'subtitle.srt') => ({ name, url: '/api/fs/blob?library=7&path=' + encodeURIComponent(name) })

test('preview supports VTT and safe content kinds; SVG/HTML remain download-only', () => {
  assert.equal(filePreviewKind('字幕.VTT'), 'text')
  assert.equal(filePreviewKind('movie.NFO'), 'text')
  assert.equal(filePreviewKind('剧集.rmvb'), 'video')
  assert.equal(filePreviewKind('海报.JPEG'), 'image')
  assert.equal(filePreviewKind('资料.pdf'), 'pdf')
  for (const name of ['index.html', 'poster.svg', 'data.xml', 'script.js', 'unknown.bin']) assert.equal(filePreviewKind(name), 'unknown')
  assert.equal(filePreviewUrl('/api/fs/blob?library=7&path=字幕.vtt', 'text'), '/api/fs/blob?library=7&path=字幕.vtt&mode=text')
})

test('registered preview resolves the precise episode, extra or feature version without using an owner ID', () => {
  assert.deepEqual(filePreviewPlayer({ name: 'episode.mkv', episode_id: 10, show_id: 999 }), { id: 10, kind: 'episode' })
  assert.deepEqual(filePreviewPlayer({ name: 'extra.mp4', kind: 'sidecar', extra_id: 11, movie_id: 999 }), { id: 11, kind: 'extra' })
  assert.deepEqual(filePreviewPlayer({ name: 'version.mkv', kind: 'feature', movie_id: 12 }), { id: 12, kind: 'movie' })
  assert.equal(filePreviewPlayer({ name: 'unregistered-extra.mp4', kind: 'sidecar', movie_id: 999 }), null)
  assert.equal(filePreviewPlayer({ name: 'unregistered-episode.mkv', show_id: 999 }), null)
  assert.equal(filePreviewPlayer({ name: 'poster.jpg', movie_id: 12, kind: 'feature' }), null)
  assert.equal(filePreviewPlayer({ name: 'video.mkv', isDir: true, movie_id: 12, kind: 'feature' }), null)
})

test('switching text previews aborts the old request and ignores its late response', async () => {
  const old = deferred()
  const requests = []
  const state = useFilePreview({ request: (url, options) => {
    requests.push({ url, signal: options.signal })
    return url.includes('first') ? old.promise : Promise.resolve(response('新的字幕内容'))
  } })
  const first = state.open(file('first.srt'))
  await state.open(file('second.vtt'))
  assert.equal(requests[0].signal.aborted, true)
  old.resolve(response('过期内容'))
  await first
  assert.equal(state.file.value.name, 'second.vtt')
  assert.equal(state.text.value, '新的字幕内容')
  assert.equal(state.loading.value, false)
  state.dispose()
})

test('closing during Response.text cancels and prevents a late body from reviving the dialog', async () => {
  const body = deferred()
  let signal
  const state = useFilePreview({ request: async (url, options) => { signal = options.signal; return response('', { text: () => body.promise }) } })
  const opening = state.open(file())
  await Promise.resolve()
  state.close()
  assert.equal(signal.aborted, true)
  body.resolve('晚到的正文')
  await opening
  assert.equal(state.file.value, null)
  assert.equal(state.text.value, '')
  assert.equal(state.error.value, '')
})

test('disposing a preview stops its media and prevents reopening or late fetch mutation', async () => {
  const late = deferred()
  const calls = []
  const media = { pause: () => calls.push('pause'), removeAttribute: name => calls.push('remove:' + name), load: () => calls.push('load') }
  const state = useFilePreview({ clearMedia: () => clearFilePreviewElement(media), request: () => late.promise })
  const pending = state.open(file())
  calls.length = 0
  state.dispose()
  assert.deepEqual(calls, ['pause', 'remove:src', 'load'])
  late.resolve(response('late'))
  await pending
  await state.open(file('video.mp4'))
  assert.equal(state.file.value, null)
  assert.equal(state.text.value, '')
})

test('HTTP read errors never render an error response as subtitle content', async () => {
  let bodyRead = false
  const state = useFilePreview({ request: async () => response('', { ok: false, status: 404, text: async () => { bodyRead = true; return '{"detail":"missing"}' } }) })
  await state.open(file())
  assert.equal(bodyRead, false)
  assert.equal(state.text.value, '')
  assert.match(state.error.value, /文件已不存在/)
  assert.match(filePreviewReadError(503), /离线/)
  assert.match(filePreviewReadError(403), /权限/)
  state.dispose()
})

test('text preview preserves content as text and reports server truncation', async () => {
  const state = useFilePreview({ request: async () => response('<script>alert("text only")</script>', { headers: new Headers({ 'X-Preview-Truncated': 'true', 'X-Preview-Limit': '65536' }) }) })
  await state.open(file('movie.nfo'))
  assert.equal(state.text.value, '<script>alert("text only")</script>')
  assert.equal(state.truncated.value, true)
  assert.equal(state.limit.value, 65536)
  state.dispose()
})

test('failed assets probe only one byte, discard response bodies and report offline distinctly', async () => {
  const requests = []
  let cancelled = false
  const state = useFilePreview({ request: async (url, options) => {
    requests.push({ url, options })
    return response('', { ok: false, status: 503, body: { cancel: async () => { cancelled = true } } })
  } })
  await state.open(file('poster.jpg'))
  await state.failAsset('图片格式不兼容')
  assert.equal(requests[0].options.headers.Range, 'bytes=0-0')
  assert.equal(requests[0].options.signal.aborted, true)
  assert.equal(cancelled, true)
  assert.match(state.error.value, /媒体库暂时离线/)
  state.dispose()
})

test('late asset probes cannot replace a new preview error or reopen a closed preview', async () => {
  const waiting = deferred()
  let signal
  const state = useFilePreview({ request: (url, options) => { signal = options.signal; return waiting.promise } })
  await state.open(file('old.mp4'))
  const checking = state.failAsset('旧视频格式不支持')
  await state.open(file('new.jpg'))
  assert.equal(signal.aborted, true)
  waiting.resolve(response('', { ok: false, status: 404 }))
  await checking
  assert.equal(state.file.value.name, 'new.jpg')
  assert.equal(state.error.value, '')
  state.dispose()
})

test('unmounting the actual preview dialog pauses and detaches a native video before DOM removal', async t => {
  const { createRenderer, nextTick } = await import('vue')
  const { loadSfc } = await import('./helpers/loadSfc.js')
  const previousDocument = globalThis.document
  const listeners = new Map()
  globalThis.document = {
    fullscreenElement: null,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
  }
  t.after(() => { if (previousDocument) globalThis.document = previousDocument; else delete globalThis.document })
  const calls = [], created = []
  const makeNode = type => {
    const node = { type, children: [], parent: null, props: {}, removeAttribute(name) { calls.push(type + ':remove:' + name); delete node.props[name] } }
    if (type === 'video') { node.pause = () => calls.push('video:pause'); node.load = () => calls.push('video:load') }
    created.push(node)
    return node
  }
  const body = makeNode('body')
  const renderer = createRenderer({
    createElement: makeNode, createText: () => makeNode('text'), createComment: () => makeNode('comment'),
    setText(node, value) { node.text = value }, setElementText(node, value) { node.text = value }, setComment() {},
    patchProp(node, name, previous, value) { node.props[name] = value },
    insert(node, parent, anchor) {
      node.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
    },
    remove(node) { calls.push(node.type + ':detach'); if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node) },
    parentNode: node => node.parent,
    nextSibling(node) { const siblings = node.parent?.children || []; return siblings[siblings.indexOf(node) + 1] || null },
    querySelector: () => body,
  })
  const component = await loadSfc(new URL('../src/components/FilePreviewDialog.vue', import.meta.url), { '../useFocusTrap.js': { useFocusTrap() {} } })
  const app = renderer.createApp(component, { file: file('unregistered.mp4') })
  app.mount(makeNode('root'))
  await nextTick()
  assert.ok(created.some(node => node.type === 'video' && node.props.src.includes('inline=1')))
  calls.length = 0
  app.unmount()
  assert.deepEqual(calls.slice(0, 3), ['video:pause', 'video:remove:src', 'video:load'])
  assert.equal(listeners.size, 0)
})


test('PDF preview stays loading until a one-byte status probe succeeds and discards its body', async () => {
  const waiting = deferred()
  const requests = []
  let bodyCancelled = false
  const state = useFilePreview({ request: (url, options) => { requests.push({ url, options }); return waiting.promise } })
  const opening = state.open(file('document.pdf'))
  assert.equal(state.kind.value, 'pdf')
  assert.equal(state.loading.value, true)
  assert.equal(requests[0].options.headers.Range, 'bytes=0-0')
  waiting.resolve(response('', { status: 206, body: { cancel: async () => { bodyCancelled = true } } }))
  await opening
  assert.equal(bodyCancelled, true)
  assert.equal(requests[0].options.signal.aborted, true)
  assert.equal(state.loading.value, false)
  assert.equal(state.error.value, '')
  state.dispose()
})

test('PDF missing, denied and offline responses become localized errors before iframe creation', async () => {
  for (const [status, expected] of [[403, /权限/], [404, /不存在/], [503, /离线/]]) {
    const state = useFilePreview({ request: async () => response('', { ok: false, status }) })
    await state.open(file('document.pdf'))
    assert.equal(state.loading.value, false)
    assert.match(state.error.value, expected)
    assert.equal(state.text.value, '')
    state.dispose()
  }
})

test('late PDF probe results cannot show an iframe or errors after close or replacement', async () => {
  for (const replacement of [null, file('new.jpg')]) {
    const waiting = deferred()
    let signal
    let cancelled = false
    const state = useFilePreview({ request: (url, options) => { signal = options.signal; return waiting.promise } })
    const opening = state.open(file('old.pdf'))
    if (replacement) await state.open(replacement); else state.close()
    assert.equal(signal.aborted, true)
    waiting.resolve(response('', { ok: false, status: 404, body: { cancel: async () => { cancelled = true } } }))
    await opening
    assert.equal(cancelled, true)
    assert.equal(state.file.value?.name || null, replacement?.name || null)
    assert.equal(state.error.value, '')
    assert.equal(state.loading.value, false)
    state.dispose()
  }
})

test('retrying a PDF repeats the preflight and clears the earlier HTTP error', async () => {
  let attempts = 0
  const state = useFilePreview({ request: async () => ++attempts === 1 ? response('', { ok: false, status: 503 }) : response('', { status: 206 }) })
  await state.open(file('document.pdf'))
  assert.match(state.error.value, /离线/)
  await state.open(file('document.pdf'))
  assert.equal(attempts, 2)
  assert.equal(state.error.value, '')
  assert.equal(state.loading.value, false)
  state.dispose()
})
