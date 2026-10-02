import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref, nextTick } from 'vue'
import { librarySourceOrder, useMatchingSettings } from '../src/useMatchingSettings.js'

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
function setup(t, request = async () => ({}), refresh = async () => {}) {
  const scope = effectScope()
  const libraries = ref([
    { id: 7, kind: 'movie', metadata_providers: '["tmdb","local"]' },
    { id: 9, kind: 'tv', metadata_providers: '["tvmaze","tmdb"]' },
  ])
  const state = scope.run(() => useMatchingSettings(libraries, request, refresh))
  t.after(() => scope.stop())
  return { state, libraries, scope }
}
test('source priority preserves the configured order and tolerates old invalid settings', () => {
  assert.deepEqual(librarySourceOrder({ metadata_providers: '["tmdb","local","tmdb","unknown"]' }), ['tmdb', 'local'])
  assert.deepEqual(librarySourceOrder({ metadata_providers: '{bad' }), ['local', 'tmdb', 'wikidata'])
  assert.deepEqual(librarySourceOrder({ metadata_providers: '{}' }), ['local', 'tmdb', 'wikidata'])
})
test('library changes and list refreshes retain independent drafts and explicit priority', async t => {
  const { state, libraries } = setup(t)
  state.move(1, -1)
  state.toggle('wikidata', true)
  assert.deepEqual(state.selection.value, ['local', 'tmdb', 'wikidata'])
  state.libraryId.value = 9
  state.toggle('tmdb', false)
  libraries.value = libraries.value.map(library => ({ ...library }))
  await nextTick()
  assert.deepEqual(state.selection.value, ['tvmaze'])
  assert.equal(state.draftCount.value, 2)
  state.libraryId.value = 7
  assert.deepEqual(state.selection.value, ['local', 'tmdb', 'wikidata'])
  state.discard()
  assert.deepEqual(state.selection.value, ['tmdb', 'local'])
  assert.equal(state.draftCount.value, 1)
})
test('save captures its original target and does not discard edits made while saving', async t => {
  const pending = deferred(), requests = []
  const { state, libraries } = setup(t, (path, options) => { requests.push({ path, options }); return pending.promise })
  state.move(1, -1)
  const save = state.save()
  state.toggle('wikidata', true)
  state.libraryId.value = 9
  state.toggle('tmdb', false)
  pending.resolve({})
  await save
  assert.equal(requests[0].path, '/api/libraries/7')
  assert.deepEqual(JSON.parse(JSON.parse(requests[0].options.body).metadata_providers), ['local', 'tmdb'])
  assert.equal(libraries.value[0].metadata_providers, '["local","tmdb"]')
  assert.deepEqual(state.selection.value, ['tvmaze'])
  state.libraryId.value = 7
  assert.deepEqual(state.selection.value, ['local', 'tmdb', 'wikidata'])
  assert.equal(state.dirty.value, true)
})
test('matching tests use the selected movie or TV library; empty results are not successful matches', async t => {
  const requests = []
  const { state } = setup(t, async path => {
    requests.push(path)
    return requests.length === 1 ? { items: [], source: 'none' } : { items: [{ title: '三体', source: 'tvmaze' }] }
  })
  state.query.value = '阿凡达'
  await state.testSearch()
  assert.match(requests[0], /library=7&kind=movie/)
  assert.match(state.testMessage.value, /未找到候选/)
  assert.doesNotMatch(state.testMessage.value, /资料搜索可用/)
  state.libraryId.value = 9
  state.query.value = '三体'
  await state.testSearch()
  assert.match(requests[1], /library=9&kind=tv/)
  assert.match(state.testMessage.value, /找到 1 个候选 · 来源：TVmaze/)
  state.toggle('local', true)
  await state.testSearch()
  assert.equal(requests.length, 2, 'Drafts must be saved before testing')
})
test('switching library aborts a test and ignores its late result', async t => {
  const pending = deferred()
  let signal
  const { state } = setup(t, (_path, options) => { signal = options.signal; return pending.promise })
  state.query.value = '测试'
  const task = state.testSearch()
  state.libraryId.value = 9
  assert.equal(signal.aborted, true)
  pending.resolve({ items: [{ title: '过期电影', source: 'tmdb' }] })
  await task
  assert.equal(state.testResult.value, null)
  assert.equal(state.testMessage.value, '')
  assert.equal(state.testing.value, false)
})
