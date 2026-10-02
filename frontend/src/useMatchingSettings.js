import { computed, onScopeDispose, reactive, ref, watch } from 'vue'

export const CHAIN_PROVIDERS = ['local', 'tmdb', 'wikidata', 'tvmaze', 'bgm', 'douban', 'nfo']
export const CHAIN_LABELS = {
  local: '本地索引', tmdb: 'TMDB', wikidata: 'Wikidata', tvmaze: 'TVmaze',
  bgm: 'Bangumi', douban: '豆瓣（需服务器配置）', nfo: 'NFO 导入',
}
export const DEFAULT_CHAIN = ['local', 'tmdb', 'wikidata']
export function librarySourceOrder(library) {
  let value = []
  try { value = JSON.parse(library?.metadata_providers || '[]') } catch { /* Old invalid values use the server default. */ }
  const names = Array.isArray(value) ? [...new Set(value.filter(name => CHAIN_PROVIDERS.includes(name)))] : []
  return names.length ? names : [...DEFAULT_CHAIN]
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

// Drafts belong to a library, so switching targets or refreshing the library list cannot discard edits.
export function useMatchingSettings(libraries, request, refreshLibraries) {
  const libraryId = ref(null)
  const drafts = reactive({})
  const messages = reactive({})
  const saving = ref(false)
  const testing = ref(false)
  const query = ref('')
  const testMessage = ref('')
  const testResult = ref(null)
  let generation = 0
  let controller = null
  let disposed = false
  const library = computed(() => libraries.value.find(item => item.id === libraryId.value))
  const selection = computed({
    get: () => drafts[libraryId.value] || librarySourceOrder(library.value),
    set: value => { if (library.value) drafts[libraryId.value] = [...value] },
  })
  const dirty = computed(() => !!library.value && !same(selection.value, librarySourceOrder(library.value)))
  const draftCount = computed(() => libraries.value.filter(item => drafts[item.id] && !same(drafts[item.id], librarySourceOrder(item))).length)
  const message = computed(() => messages[libraryId.value] || '')
  const orderText = computed(() => selection.value.map(name => CHAIN_LABELS[name]).join(' → '))
  function resetTest() {
    generation++
    controller?.abort()
    controller = null
    testing.value = false
    testMessage.value = ''
    testResult.value = null
  }
  watch(libraries, () => {
    if (!libraries.value.some(item => item.id === libraryId.value)) libraryId.value = libraries.value[0]?.id ?? null
  }, { immediate: true })
  watch([libraryId, query, () => selection.value.join(',')], resetTest, { flush: 'sync' })
  function toggle(name, enabled) {
    selection.value = enabled ? [...selection.value.filter(item => item !== name), name] : selection.value.filter(item => item !== name)
    messages[libraryId.value] = ''
  }
  function move(index, offset) {
    const target = index + offset
    if (target < 0 || target >= selection.value.length) return
    const next = [...selection.value]
    ;[next[index], next[target]] = [next[target], next[index]]
    selection.value = next
    messages[libraryId.value] = ''
  }
  function discard() {
    delete drafts[libraryId.value]
    messages[libraryId.value] = ''
  }
  async function save() {
    if (disposed || saving.value || !library.value || !selection.value.length) return
    const id = libraryId.value
    const names = [...selection.value]
    const target = library.value
    saving.value = true
    messages[id] = ''
    resetTest()
    try {
      await request('/api/libraries/' + id, {
        method: 'PATCH', body: JSON.stringify({ metadata_providers: JSON.stringify(names) }),
      })
      if (disposed) return
      target.metadata_providers = JSON.stringify(names)
      if (same(drafts[id], names)) delete drafts[id]
      messages[id] = '已保存：' + names.map(name => CHAIN_LABELS[name]).join(' → ')
      try { await refreshLibraries() } catch { messages[id] += '。列表刷新失败，规则已保存。' }
    } catch (error) {
      if (!disposed) messages[id] = '保存失败：' + error.message
    } finally { if (!disposed) saving.value = false }
  }
  async function testSearch() {
    if (disposed || testing.value || saving.value || !library.value || dirty.value || !query.value.trim()) return
    resetTest()
    const gen = generation
    controller = new AbortController()
    const selected = library.value
    const kind = selected.kind === 'tv' ? 'tv' : 'movie'
    const params = new URLSearchParams({ library: String(selected.id), kind, q: query.value.trim() })
    const started = performance.now()
    testing.value = true
    try {
      const result = await request('/api/metadata/test-search?' + params, { signal: controller.signal })
      if (disposed || gen !== generation) return
      const items = Array.isArray(result.items) ? result.items : []
      const sources = [...new Set(items.map(item => item.source).filter(Boolean))]
      if (!sources.length && result.source && result.source !== 'none') sources.push(result.source)
      const sourceText = sources.map(source => CHAIN_LABELS[source] || source).join('、')
      testResult.value = { ...result, items }
      const elapsed = Math.round(performance.now() - started)
      testMessage.value = items.length
        ? `找到 ${items.length} 个候选 · 来源：${sourceText || '未标注'} · ${elapsed} ms`
        : `未找到候选 · ${elapsed} ms。可更换片名或检查来源运行状态；空结果不能证明来源连接正常。`
    } catch (error) {
      if (!disposed && gen === generation) testMessage.value = '匹配测试失败：' + error.message
    } finally {
      if (gen === generation) { testing.value = false; controller = null }
    }
  }
  onScopeDispose(() => { disposed = true; resetTest() })
  return { libraryId, library, selection, dirty, draftCount, message, orderText, saving, testing, query, testMessage, testResult, toggle, move, discard, save, testSearch }
}
