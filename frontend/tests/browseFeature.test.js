import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { deferred, flush, nodeText, renderHarness } from './helpers/renderHarness.js'

test('spotlight shares the shelf request, preserves exact episode actions and restricts images to desktop browsing', async t => {
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const oldStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  let onWidth
  const media = { matches: true, addEventListener(_name, fn) { onWidth = fn }, removeEventListener() {} }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    matchMedia: () => media, addEventListener() {}, removeEventListener() {},
  } })
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem() {} } })
  const requests = []
  const api = (url, { signal }) => {
    const request = { url, signal, ...deferred() }
    requests.push(request)
    return request.promise
  }
  const components = { vue: Vue, '../api.js': {
    api, posterUrl: p => p && '/posters/' + p, scopedArtworkUrl: url => url,
  } }
  const Feature = await loadSfc(new URL('../src/components/BrowseFeature.vue', import.meta.url), components)
  const Recent = await loadSfc(new URL('../src/components/ContinueWatchingRow.vue', import.meta.url), components)
  const enabled = Vue.ref(true), lib = Vue.ref(1), fallback = Vue.ref(null), shelf = Vue.ref(null)
  const played = []
  const ui = renderHarness({ setup: () => () => Vue.h(Feature, {
    enabled: enabled.value, recentReady: shelf.value?.settled, kind: 'tv', firstItem: fallback.value,
    recentItem: shelf.value?.featuredItem, onResume: item => played.push(item.progress.version_id),
  }, { recent: ({ featuredVersion }) => Vue.h(Recent, {
    mediaLibraryId: lib.value, ref: shelf, kind: 'tv', featuredVersion, hasCatalogue: !!fallback.value,
    onResume: item => played.push(item.progress.version_id),
  }) }) })
  t.after(() => {
    ui.app.unmount()
    for (const [key, descriptor] of [['window', oldWindow], ['localStorage', oldStorage]]) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })
  const episode = (version, canResume) => ({
    title: '合成剧集', id: 8, show_id: 8, subtitle: '选集 ' + version,
    can_resume: canResume, watched: !canResume, backdrop_path: 'tv-wide.jpg', poster_path: 'tv-poster.jpg',
    progress: { version_id: version, duration: 5000, position: 60, remaining_sec: 4940, percent: .012 },
  })
  const feature = () => ui.find(node => node.props['aria-labelledby'] === 'feature-title-tv')
  const image = () => ui.find(node => node.type === 'img' && String(node.props.class).includes('browse-feature-art'))
  const link = key => ui.find(node => node.props['data-browse-focus'] === key)
  const button = label => ui.find(node => node.type === 'button' && (node.props['aria-label'] === label || nodeText(node).trim() === label))
  await flush()
  assert.equal(requests.length, 1)
  assert.doesNotMatch(requests[0].url, /include_finished=true/)
  assert.equal(feature(), undefined, 'no provisional candidate before history arrives')
  requests[0].resolve({ items: [episode(24, true), episode(23, true)] })
  await flush()
  assert.ok(link('feature-8'))
  assert.equal(image().props.src, '/posters/tv-wide.jpg')
  assert.equal(link('recent-24'), undefined)
  assert.ok(link('recent-23'))
  button('继续播放').props.onClick()
  button('继续播放 合成剧集 选集 23').props.onClick()
  assert.deepEqual(played, [24, 23])
  image().props.onError({ currentTarget: { getAttribute: () => '/posters/tv-wide.jpg' } })
  await flush()
  assert.equal(image().props.src, '/posters/tv-poster.jpg')
  image().props.onError({ currentTarget: { getAttribute: () => '/posters/tv-poster.jpg' } })
  await flush()
  assert.equal(image(), undefined)
  assert.ok(feature())
  onWidth({ matches: false })
  await flush()
  assert.equal(feature(), undefined)
  assert.ok(link('recent-24'), 'phone keeps the first item in its compact shelf')
  enabled.value = false
  onWidth({ matches: true })
  await flush()
  assert.equal(feature(), undefined)
  assert.equal(requests.length, 1, 'reflow keeps the shelf mounted')
  enabled.value = true
  lib.value = 2
  await flush()
  assert.equal(feature(), undefined)
  assert.equal(link('recent-25'), undefined)
  lib.value = 3
  await flush()
  assert.equal(requests[1].signal.aborted, true)
  fallback.value = { id: 15, title: '新库作品' }
  requests[1].resolve({ items: [episode(99, true)] })
  requests[2].resolve({ items: [] })
  await flush()
  assert.ok(link('feature-15'))
  assert.equal(button('继续播放'), undefined)
  assert.equal(image(), undefined)
  assert.equal(link('recent-99'), undefined)
  button('全部最近播放').props.onClick()
  assert.match(requests[3].url, /include_finished=true/)
  requests[3].resolve({ items: [episode(25, false)] })
  await flush()
  assert.ok(link('recent-25'), 'completed-only history remains accessible')
  assert.ok(link('feature-15'), 'a completed episode does not replace the wall fallback')
  lib.value = 4
  await flush()
  requests[4].resolve({ items: [episode(25, false), episode(24, true), episode(23, true)] })
  await flush()
  assert.ok(link('feature-8'))
  assert.ok(link('recent-24'), 'full history includes the featured version')
  assert.ok(link('recent-23'))
  assert.ok(link('recent-25'))
  button('只看未看完').props.onClick()
  requests[5].reject(new Error('稍后重试'))
  await flush()
  assert.equal(link('recent-25'), undefined, 'a refresh failure cannot put completed history into the unfinished shelf')
  assert.ok(link('recent-23'))
})
