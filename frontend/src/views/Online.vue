<template>
  <div class="browse-page online-page">
    <header class="browse-heading online-heading"><div><h1>在线</h1><p>从已启用的视频源查找内容</p></div><router-link v-if="!isChildren" to="/settings?sec=sec-online">管理视频源</router-link></header>
    <EmptyState v-if="isChildren" state="empty" title="儿童模式暂不支持在线内容" text="切换观看模式后再浏览。" />
    <template v-else-if="safeMode">
      <section class="online-library" aria-label="在线观看记录">
        <div class="online-library-heading"><div><h2>我的在线记录</h2><p class="online-scope">当前设备 · {{ modeText }}；与本地媒体库记录分开。</p></div><JzButton variant="ghost" icon="refresh" :disabled="libraryLoading" @click="loadLibrary()">刷新记录</JzButton></div>
        <div class="online-library-tabs" role="group" aria-label="在线记录分类">
          <JzButton v-for="tab in libraryTabs" :key="tab.key" :data-online-library-section="tab.key" :aria-pressed="librarySection === tab.key" @click="selectLibrary(tab.key)">{{ tab.label }}</JzButton>
        </div>
        <EmptyState v-if="libraryLoading" state="loading" title="正在读取在线记录" />
        <EmptyState v-else-if="libraryError" state="error" title="在线记录加载失败" :text="libraryError" retry @retry="loadLibrary()" />
        <EmptyState v-else-if="!libraryRows.length" state="empty" :title="librarySection === 'favorites' ? '还没有收藏的在线内容' : librarySection === 'history' ? '还没有在线观看记录' : '暂时没有继续观看的内容'" :text="librarySection === 'favorites' ? '打开内容详情后可收藏到当前设备。' : '实际开始播放后会保存进度，未成功起播不会建立观看记录。'" />
        <div v-else class="online-library-rows">
          <article v-for="row in libraryRows" :key="row.item.id" class="online-library-row" :class="{ 'is-unavailable': !row.available }" :data-online-state-item="row.item.id">
            <div class="online-library-text"><h3>{{ row.item.title }}</h3><p>{{ row.source.name }} · {{ row.item.kind === 'tv' ? '剧集' : '电影' }}</p><p v-if="row.progress">{{ row.progress.completed ? '已播放完毕' : '上次播放到 ' + fmt(row.progress.position) }}<span v-if="row.progress.duration"> / {{ fmt(row.progress.duration) }}</span></p><p v-if="!row.available" class="online-scope">{{ availabilityReason(row.reason) }}</p></div>
            <div class="online-library-actions">
              <JzButton v-if="row.progress" icon="play" :data-online-resume="row.item.id" :disabled="!row.available || loadingSources || !!sourcesError || starting" @click="openSaved(row, true)">{{ row.progress.completed ? '重新播放' : '继续播放' }}</JzButton>
              <JzButton :data-online-library-detail="row.item.id" :disabled="!row.available || loadingSources || !!sourcesError" @click="openSaved(row, false)">查看详情</JzButton>
              <JzButton v-if="row.favorite" icon="heart-filled" :data-online-favorite="row.item.id" :disabled="mutationBusy.has(row.item.id)" @click="favoriteRow(row, false)">取消收藏</JzButton>
              <JzButton v-if="row.progress" :data-online-history-delete="row.item.id" :disabled="mutationBusy.has(row.item.id)" @click="clearHistory(row)">清除观看记录</JzButton>
            </div>
          </article>
        </div>
        <nav v-if="libraryTotal > 20" class="online-pagination" aria-label="在线记录分页"><JzButton :disabled="libraryOffset === 0 || libraryLoading" @click="loadLibrary(Math.max(0, libraryOffset - 20))">上一页</JzButton><span>第 {{ Math.floor(libraryOffset / 20) + 1 }} 页 · {{ libraryTotal }} 项</span><JzButton :disabled="libraryLoading || libraryOffset + 20 >= libraryTotal || libraryOffset + 20 > 1000" @click="loadLibrary(libraryOffset + 20)">下一页</JzButton></nav>
      </section>
      <p v-if="stateNotice" class="online-state-notice" data-online-state-notice role="status">{{ stateNotice }}</p>
      <div v-if="nextLoading" class="online-next-state" data-online-next-loading role="status"><span>正在读取下一集…</span><JzButton @click="cancelNext">取消连续播放</JzButton></div>
      <EmptyState v-if="loadingSources" state="loading" title="正在加载视频源" />
      <EmptyState v-else-if="sourcesError" state="error" title="视频源加载失败" :text="sourcesError" retry @retry="loadSources" />
      <EmptyState v-else-if="!enabled" state="empty" title="在线点播尚未启用" text="在设置中启用在线点播并添加视频源；已有记录仍会保留。"><router-link to="/settings?sec=sec-online">打开在线视频源设置</router-link></EmptyState>
      <EmptyState v-else-if="!sources.length" state="empty" title="还没有可用的视频源" text="可显式启用开放样例目录，或添加自己的 MacCMS JSON 来源。"><router-link to="/settings?sec=sec-online">添加视频源</router-link></EmptyState>
      <template v-else>
        <form class="online-search" data-online-search role="search" aria-label="搜索在线视频" @submit.prevent="search(1)">
          <JzField id="online-source" label="视频源"><template #default="field"><select :id="field.id" v-model="sourceId" data-online-source :disabled="loadingSources" @change="sourceChanged"><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option></select></template></JzField>
          <JzField id="online-query" label="片名"><template #default="field"><input :id="field.id" v-model="query" maxlength="100" autocomplete="off" placeholder="输入电影或剧集名称" /></template></JzField>
          <JzButton type="submit" variant="primary" icon="search" :disabled="!canSearch || searching" :loading="searching">{{ selectedSource?.provider === 'open_catalog' && !query.trim() ? '浏览目录' : '搜索' }}</JzButton>
        </form>
        <p class="online-scope">仅查询所选来源；在线内容独立于 NAS 媒体库。<span v-if="selectedSource?.provider === 'open_catalog'">开放样例用于验证播放能力。</span></p>
        <div class="online-health" data-online-health><span>API：{{ healthLoading ? '正在读取状态' : health ? onlineHealthLabel(health.purposes?.api) : healthError || '未观察' }}</span><span>媒体：{{ healthLoading ? '正在读取状态' : health ? onlineHealthLabel(health.purposes?.media) : healthError || '未观察' }}</span><JzButton variant="ghost" :disabled="healthLoading" @click="loadHealth">刷新状态</JzButton><small>最近实际请求的进程内记录；重启或配置变化后需重新观察，不代表终端已可播。</small></div>
        <EmptyState v-if="detailLoading" state="loading" title="正在加载详情" text="正在读取所选来源的线路与分集。"><JzButton @click="backToResults">返回搜索结果</JzButton></EmptyState>
        <EmptyState v-else-if="detailError" state="error" title="详情加载失败" :text="detailError" retry @retry="openDetail(lastDetailId)"><JzButton @click="backToResults">返回搜索结果</JzButton></EmptyState>
        <section v-else-if="detail" class="online-detail" data-online-detail>
          <JzButton variant="ghost" icon="arrow-left" @click="backToResults">返回搜索结果</JzButton>
          <header class="online-detail-heading"><div class="online-artwork"><AppIcon :name="detail.item.kind === 'tv' ? 'tv' : 'movie'" :size="48" /></div><div><p class="online-eyebrow">{{ detail.source.name }} · {{ detail.item.kind === 'tv' ? '剧集' : '电影' }}</p><h2>{{ detail.item.title }}</h2><p v-if="detail.year" class="online-year">{{ detail.year }}</p></div></header>
          <div class="online-detail-actions"><JzButton :data-online-favorite="detail.item.id" :icon="itemState?.favorite ? 'heart-filled' : 'heart'" :disabled="!itemState || mutationBusy.has(detail.item.id)" @click="favoriteDetail">{{ itemState?.favorite ? '取消收藏' : '收藏' }}</JzButton><JzButton v-if="resumeEntry" icon="play" data-online-detail-resume :disabled="starting" @click="play(resumeEntry)">继续播放</JzButton><span v-if="starting" role="status">正在读取续播位置…</span></div>
          <p v-if="detail.description" class="online-description">{{ detail.description }}</p>
          <div v-if="detail.attribution?.length" class="online-attribution"><span>内容来源与许可</span><a v-for="credit in detail.attribution" :key="credit.url" :href="safeAttribution(credit.url)" target="_blank" rel="noopener noreferrer">{{ credit.label }}</a></div>
          <h3>播放线路</h3>
          <div class="online-lines" role="group" aria-label="播放线路"><JzButton v-for="line in detail.lines" :key="line.id" :data-online-line="line.id" :aria-pressed="line.id === lineId" @click="selectLine(line.id)">{{ line.label }}</JzButton></div>
          <label v-if="detail.item.kind === 'tv'" class="online-autoplay"><input v-model="autoplay" type="checkbox" data-online-autoplay @change="autoplayChanged" />自动播放下一集（当前浏览器）</label>
          <p class="online-scope">{{ selectedLine?.label || '暂无线路' }}<span v-if="selectedLine"> · {{ selectedLine.entries.length }} {{ detail.item.kind === 'tv' ? '集' : '个播放版本' }}</span><span v-if="detail.item.kind === 'tv'">；连续播放仅使用当前来源和线路，不跳过片尾。</span></p>
          <div v-if="selectedLine" class="online-entries"><div v-for="(entry, index) in selectedLine.entries" :key="entry.identity?.episode_id || entry.label + ':' + index" class="online-entry"><JzButton :data-online-entry="entry.label" :disabled="!entry.supported || !entry.identity || starting" :variant="entry.supported ? 'primary' : 'secondary'" :icon="entry.supported ? 'play' : undefined" @click="play(entry)">{{ entry.label }}</JzButton><span v-if="!entry.supported">{{ unsupportedReason(entry.reason) }}</span><span v-else>{{ entry.transport?.toUpperCase() }} · 由当前设备播放</span></div></div>
          <EmptyState v-else state="empty" title="此来源暂无可播放线路" text="选择其他来源或稍后重新查询。" />
        </section>
        <template v-else>
          <EmptyState v-if="searching" state="loading" title="正在搜索" :text="'正在查询 ' + selectedSource?.name" />
          <EmptyState v-else-if="searchError" state="error" title="搜索失败" :text="searchError" retry @retry="search(lastQuery.page, lastQuery.query)" />
          <EmptyState v-else-if="!searched" state="empty" :title="selectedSource?.provider === 'open_catalog' ? '浏览开放样例目录' : '查找想看的内容'" :text="selectedSource?.provider === 'open_catalog' ? '点击“浏览目录”查看开放影片及许可。' : '输入片名，仅向当前选中的来源发起查询。'" />
          <EmptyState v-else-if="!results.length" state="no-results" title="没有找到相关内容" text="试试其他片名，或选择其他来源。" />
          <template v-else>
            <div class="online-results-heading"><h2>{{ loadedQuery ? '搜索结果' : '开放目录' }}</h2><span>{{ total }} 项 · {{ selectedSource?.name }}</span></div>
            <div class="online-results"><article v-for="item in results" :key="item.external_id" class="online-result"><div class="online-result-icon"><AppIcon :name="item.kind === 'tv' ? 'tv' : 'movie'" :size="28" /></div><div class="online-result-text"><h3>{{ item.title }}</h3><p v-if="item.year">{{ item.year }}</p><p v-if="item.description" class="online-result-description">{{ item.description }}</p></div><JzButton :data-online-result="item.external_id" icon="forward" @click="openDetail(item.external_id)">查看详情</JzButton></article></div>
            <nav v-if="pageCount > 1" class="online-pagination" aria-label="在线搜索分页"><JzButton icon="arrow-left" :disabled="page <= 1" @click="search(page - 1, loadedQuery)">上一页</JzButton><span>第 {{ page }} / {{ pageCount }} 页</span><JzButton icon="forward" :disabled="page >= pageCount" @click="search(page + 1, loadedQuery)">下一页</JzButton></nav>
          </template>
        </template>
      </template>
    </template>
    <OnlinePlayerModal v-if="playing" :key="playing.key" :online-identity="playing.identity" :title="playing.title" :initial-position="playing.position" :prepare-session="playing.prepareSession" @close="playing.close" @progress="playing.progress" @ended="playing.ended" />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { createOnlineCatalogClient, createOnlineHealthClient, onlineHealthLabel, onlineCatalogError } from '../onlineCatalog.js'
import { createOnlineProgressMemory, onlineIdentityKey, validateOnlineIdentity } from '../onlinePlayback.js'
import { createOnlineStateClient, createOnlineProgressWriter, loadOnlineAutoplay, saveOnlineAutoplay, nextOnlineEpisode, onlineStateError } from '../onlineState.js'
import { isChildren, registerViewingPlaybackCleanup, viewing } from '../viewing.js'
import { fmtTime as fmt } from '../playerLabels.js'
import EmptyState from '../components/EmptyState.vue'
import JzButton from '../components/JzButton.vue'
import JzField from '../components/JzField.vue'
import AppIcon from '../components/AppIcon.vue'
import OnlinePlayerModal from '../components/OnlinePlayerModal.vue'
import '../styles/online.css'

const client = createOnlineCatalogClient(), nextClient = createOnlineCatalogClient(), stateClient = createOnlineStateClient()
const healthClient = createOnlineHealthClient(), health = ref(null), healthLoading = ref(false), healthError = ref('')
const progress = createOnlineProgressMemory()
const sources = ref([]), sourceId = ref(''), enabled = ref(false), query = ref('')
const loadingSources = ref(true), sourcesError = ref(''), searching = ref(false), searched = ref(false), searchError = ref('')
const results = ref([]), page = ref(1), pageCount = ref(1), total = ref(0), loadedQuery = ref('')
const detailLoading = ref(false), detailError = ref(''), detail = ref(null), lineId = ref(''), lastDetailId = ref(''), itemState = ref(null)
const playing = ref(null), starting = ref(false), nextLoading = ref(false), stateNotice = ref('')
const libraryTabs = [{ key: 'continue', label: '继续观看' }, { key: 'favorites', label: '我的收藏' }, { key: 'history', label: '观看历史' }]
const librarySection = ref('continue'), libraryRows = ref([]), libraryOffset = ref(0), libraryTotal = ref(0), libraryScope = ref('')
const libraryLoading = ref(true), libraryError = ref(''), mutationBusy = ref(new Set())
const safeMode = computed(() => !isChildren.value && !viewing?.locallyLocked && !viewing?.suspended)
const mode = viewing?.mode === 'private' ? 'private' : 'normal'
const modeText = mode === 'private' ? '隐私模式' : '普通模式'
const autoplay = ref(loadOnlineAutoplay(mode))
const selectedSource = computed(() => sources.value.find(source => source.id === sourceId.value))
const canSearch = computed(() => !!sourceId.value && (query.value.trim().length > 0 || selectedSource.value?.provider === 'open_catalog'))
const selectedLine = computed(() => detail.value?.lines.find(line => line.id === lineId.value))
const lastQuery = ref({ page: 1, query: '' })
const resumeEntry = computed(() => !itemState.value?.last_progress?.completed ? findEntry(detail.value, itemState.value?.last_progress?.identity) : null)
let disposed = false, generation = 0, libraryGeneration = 0, healthGeneration = 0, playIntent = 0, nextIntent = 0, runId = 0, activeRun = null
function current(turn) { return !disposed && turn === generation && safeMode.value }
function sameIdentity(a, b) { try { return onlineIdentityKey(a) === onlineIdentityKey(b) } catch { return false } }
function findEntry(data, identity) { return data?.lines?.flatMap(line => line.entries).find(entry => entry.supported && entry.identity && sameIdentity(entry.identity, identity)) || null }
function cancelNext() { nextIntent++; nextLoading.value = false; nextClient.cancel() }
function autoplayChanged() { saveOnlineAutoplay(autoplay.value, mode); if (!autoplay.value) cancelNext() }
function availabilityReason(reason) { return reason === 'online_source_disabled' || reason === 'online_disabled' ? '来源或在线功能已停用，记录仍会保留' : '当前内容暂不可播放，记录仍会保留' }
async function loadSources() {
  const turn = ++generation
  loadingSources.value = true; sourcesError.value = ''
  try {
    const response = await client.sources()
    if (!current(turn) || !response) return
    enabled.value = response.enabled; sources.value = response.items || []
    if (!sources.value.some(source => source.id === sourceId.value)) sourceId.value = sources.value[0]?.id || ''
    void loadHealth()
  } catch (error) { if (current(turn)) sourcesError.value = onlineCatalogError(error) }
  finally { if (current(turn)) loadingSources.value = false }
}
async function loadHealth() {
  const turn = ++healthGeneration, selected = sourceId.value
  if (!selected || !safeMode.value || !enabled.value) { health.value = null; healthLoading.value = false; return }
  healthLoading.value = true; healthError.value = ''
  try {
    const response = await healthClient.read(selected)
    if (!disposed && turn === healthGeneration && sourceId.value === selected && safeMode.value && response) health.value = response
  } catch { if (!disposed && turn === healthGeneration && safeMode.value) { health.value = null; healthError.value = '状态暂不可读' } }
  finally { if (turn === healthGeneration) healthLoading.value = false }
}
async function loadLibrary(offset = libraryOffset.value) {
  const turn = ++libraryGeneration, section = librarySection.value
  libraryLoading.value = true; libraryError.value = ''
  try {
    const response = await stateClient.library(section, offset)
    if (disposed || turn !== libraryGeneration || !safeMode.value || !response) return
    libraryRows.value = response.items || []; libraryOffset.value = response.offset; libraryTotal.value = response.total
    libraryScope.value = response.scope_token
  } catch (error) { if (!disposed && turn === libraryGeneration && safeMode.value) libraryError.value = onlineStateError(error) }
  finally { if (!disposed && turn === libraryGeneration) libraryLoading.value = false }
}
function selectLibrary(section) { librarySection.value = section; void loadLibrary(0) }
function sourceChanged() {
  healthGeneration++; healthClient.cancel(); health.value = null; void loadHealth()
  generation++; client.cancel(); void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false
  results.value = []; searched.value = false; searchError.value = ''; searching.value = false; page.value = pageCount.value = 1; total.value = 0
}
async function search(nextPage = 1, text = query.value) {
  if (!sourceId.value || !safeMode.value || !text.trim() && selectedSource.value?.provider !== 'open_catalog') return
  const turn = ++generation
  void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false
  searching.value = true; searchError.value = ''; lastQuery.value = { page: nextPage, query: text }
  try {
    const response = await client.search(sourceId.value, text, nextPage)
    if (!current(turn) || !response) return
    results.value = response.items || []; page.value = response.page; pageCount.value = response.page_count
    total.value = response.total; loadedQuery.value = text.trim(); searched.value = true
  } catch (error) { if (current(turn)) searchError.value = onlineCatalogError(error) }
  finally { if (current(turn)) { searching.value = false; void loadHealth() } }
}
async function readItemState(data, turn) {
  try {
    const response = await stateClient.item(data.item.id)
    if (current(turn) && detail.value?.item.id === data.item.id && response) itemState.value = response
  } catch (error) { if (current(turn)) stateNotice.value = onlineStateError(error) }
}
async function openDetail(externalId, options = {}) {
  const turn = ++generation, source = sourceId.value
  void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = true; lastDetailId.value = externalId
  try {
    const response = await client.detail(source, externalId)
    if (!current(turn) || !response) return
    detail.value = response; lineId.value = response.lines?.find(line => line.entries.some(entry => entry.supported))?.id || response.lines?.[0]?.id || ''
    await readItemState(response, turn)
    if (!current(turn) || !options.resume) return
    if (options.itemId && options.itemId !== response.item.id) { stateNotice.value = '来源内容已变化，请在详情中重新选择。'; return }
    const entry = findEntry(response, options.identity)
    if (!entry) { stateNotice.value = '上次线路或分集已变化，请在详情中重新选择。'; return }
    lineId.value = entry.identity.line_id
    await play(entry)
  } catch (error) { if (current(turn)) detailError.value = onlineCatalogError(error) }
  finally { if (current(turn)) { detailLoading.value = false; void loadHealth() } }
}
async function openSaved(row, resume) {
  if (!row.available || !safeMode.value || loadingSources.value || sourcesError.value) return
  sourceId.value = row.source.id; sourceChanged()
  await openDetail(row.item.external_id, { resume: resume && !!row.progress, identity: row.progress?.identity, itemId: row.item.id })
}
function backToResults() { generation++; client.cancel(); void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false }
function selectLine(id) { void closePlayer(); lineId.value = id }
async function stopRun(run) {
  if (!run) return
  if (run.stopping) return run.stopping
  run.stopping = (async () => {
    if (playing.value?.key === run.key) playing.value = null
    await nextTick() // the child's beforeUnmount emits its final decoded snapshot
    await run.writer.close()
    if (activeRun === run) activeRun = null
  })()
  return run.stopping
}
async function closePlayer() {
  playIntent++; cancelNext(); starting.value = false
  const run = activeRun
  await stopRun(run)
  if (run && !disposed && safeMode.value) { void loadLibrary(); void loadHealth() }
}
async function play(entry, { automatic = false } = {}) {
  if (!entry?.supported || !entry.identity || !safeMode.value || !detail.value) return
  if (!automatic) cancelNext()
  const intent = ++playIntent, view = generation, data = detail.value
  const identity = validateOnlineIdentity(entry.identity)
  starting.value = true; stateNotice.value = ''
  await stopRun(activeRun)
  if (disposed || intent !== playIntent || view !== generation || !safeMode.value) return
  let state = null
  try { state = await stateClient.item(identity.item_id, identity) }
  catch (error) { if (intent === playIntent && safeMode.value) stateNotice.value = onlineStateError(error) }
  if (disposed || intent !== playIntent || view !== generation || !safeMode.value) return
  const saved = state?.selected_progress
  const remembered = progress.get(identity)
  const position = state ? saved?.updated_at > 0 ? saved.completed ? 0 : saved.position : 0 : remembered?.position || 0
  const run = { key: ++runId, intent, identity, source: identity.source_id, externalId: lastDetailId.value,
    itemId: data.item.id, endedHandled: false, stopping: null, writer: null }
  run.writer = createOnlineProgressWriter({ identity, scopeToken: state?.scope_token,
    revision: saved?.writer_revision || 0,
    onNotice: (message, code) => {
      if (code === 'online_state_scope_changed') progress.clear(identity)
      if (!disposed && activeRun === run && playIntent === run.intent && safeMode.value) stateNotice.value = message
    },
    onSaved: value => { if (!disposed && activeRun === run && safeMode.value && itemState.value?.item_id === identity.item_id) itemState.value = { ...itemState.value, last_progress: value, selected_progress: value } },
  })
  activeRun = run
  playing.value = { key: run.key, identity, title: data.item.title + (data.item.kind === 'tv' ? ' · ' + entry.label : ''), position,
    prepareSession: value => activeRun === run && !run.stopping ? run.writer.session(value) : false,
    progress: value => {
      if (activeRun !== run || !sameIdentity(value.identity, identity) || value.first_frame !== true) return
      progress.save(identity, { ...value, position: value.completed ? 0 : value.position })
      run.writer.update(value)
    },
    close: () => { if (activeRun === run) void closePlayer() },
    ended: value => { void onEnded(run, value) },
  }
  if (state && detail.value?.item.id === identity.item_id) itemState.value = { ...state, last_progress: state.last_progress }
  starting.value = false
}
async function onEnded(run, identity) {
  if (disposed || activeRun !== run || run.endedHandled || !sameIdentity(run.identity, identity) || !safeMode.value) return
  run.endedHandled = true
  const shouldContinue = autoplay.value && identity.kind === 'episode'
  const ticket = ++nextIntent, view = generation
  nextLoading.value = shouldContinue
  await stopRun(run)
  if (!disposed && safeMode.value) { void loadLibrary(); void loadHealth() }
  if (!shouldContinue || disposed || ticket !== nextIntent || view !== generation || !safeMode.value || !autoplay.value) return
  try {
    const fresh = await nextClient.detail(run.source, run.externalId)
    if (disposed || ticket !== nextIntent || view !== generation || !safeMode.value || !autoplay.value || !fresh) return
    const entry = nextOnlineEpisode(fresh, identity)
    detail.value = fresh
    if (!entry) {
      const entries = fresh.lines?.find(value => value.id === identity.line_id)?.entries || []
      const index = entries.findIndex(value => sameIdentity(value.identity, identity))
      stateNotice.value = index < 0 ? '当前线路或分集已变化，请手动选集。'
        : index + 1 < entries.length ? '下一集暂不支持播放，请手动选择。' : '当前线路已播放完毕。'
      return
    }
    lineId.value = identity.line_id
    nextLoading.value = false
    await play(entry, { automatic: true })
  } catch (error) { if (!disposed && ticket === nextIntent && safeMode.value) stateNotice.value = '下一集未能加载，请手动选集。' + onlineCatalogError(error) }
  finally { if (ticket === nextIntent) nextLoading.value = false }
}
async function mutateItem(id, work) {
  if (mutationBusy.value.has(id) || !safeMode.value) return
  mutationBusy.value = new Set([...mutationBusy.value, id])
  const view = generation, playback = playIntent
  try { await work(); if (!disposed && view === generation && safeMode.value) { if (playback === playIntent) stateNotice.value = ''; void loadLibrary() } }
  catch (error) { if (!disposed && view === generation && playback === playIntent && safeMode.value) stateNotice.value = onlineStateError(error) }
  finally { mutationBusy.value = new Set([...mutationBusy.value].filter(value => value !== id)) }
}
function favoriteDetail() {
  const item = detail.value?.item, state = itemState.value
  if (!item || !state) return
  const favorite = !state.favorite, scope = state.scope_token
  void mutateItem(item.id, async () => {
    await stateClient.favorite(item.id, favorite, scope)
    if (detail.value?.item.id === item.id && itemState.value?.scope_token === scope && safeMode.value) itemState.value = { ...itemState.value, favorite }
  })
}
function favoriteRow(row, favorite) {
  const scope = libraryScope.value
  void mutateItem(row.item.id, () => stateClient.favorite(row.item.id, favorite, scope))
}
function clearHistory(row) {
  const scope = libraryScope.value, view = generation
  void mutateItem(row.item.id, async () => {
    const response = await stateClient.deleteHistory(row.item.id, scope)
    if (disposed || view !== generation || !safeMode.value) return
    progress.clearAll()
    if (libraryScope.value === scope) libraryScope.value = response.scope_token
    if (itemState.value?.scope_token === scope) itemState.value = { ...itemState.value, scope_token: response.scope_token,
      ...(itemState.value.item_id === row.item.id ? { last_progress: null, selected_progress: null } : {}) }
  })
}
function safeAttribution(value) { try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined } catch { return undefined } }
function unsupportedReason(reason) { return ['host_not_allowed', 'target_host_not_allowed'].includes(reason) ? '媒体主机尚未加入此来源允许列表' : '当前线路不支持直接播放，请选择其他线路' }
const stopCleanup = registerViewingPlaybackCleanup(async () => {
  generation++; libraryGeneration++; playIntent++; cancelNext(); client.cancel(); stateClient.cancel()
  healthGeneration++; healthClient.cancel(); health.value = null; healthError.value = ''; healthLoading.value = false
  const closing = stopRun(activeRun)
  detail.value = null; itemState.value = null; results.value = []; searched.value = false; libraryRows.value = []; libraryScope.value = ''; libraryTotal.value = 0
  stateNotice.value = ''; searching.value = detailLoading.value = starting.value = false
  await closing
})
onMounted(() => {
  if (safeMode.value) { void loadSources(); void loadLibrary(0) }
  else loadingSources.value = libraryLoading.value = false
})
onUnmounted(() => {
  disposed = true; generation++; libraryGeneration++; healthGeneration++; playIntent++; cancelNext(); client.close(); nextClient.close(); stateClient.close(); healthClient.close(); stopCleanup()
  void stopRun(activeRun)
})
</script>
