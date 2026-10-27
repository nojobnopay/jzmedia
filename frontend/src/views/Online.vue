<template>
  <div class="browse-page online-page">
    <header class="browse-heading online-heading"><div><h1>在线</h1><p>从已启用的视频源查找内容</p></div><router-link v-if="!isChildren" to="/settings?sec=sec-online">管理视频源</router-link></header>
    <EmptyState v-if="isChildren" state="empty" title="儿童模式暂不支持在线内容" text="切换观看模式后再浏览。" />
    <EmptyState v-else-if="loadingSources" state="loading" title="正在加载视频源" />
    <EmptyState v-else-if="sourcesError" state="error" title="视频源加载失败" :text="sourcesError" retry @retry="loadSources" />
    <EmptyState v-else-if="!enabled" state="empty" title="在线点播尚未启用" text="在设置中启用在线点播并添加视频源。"><router-link to="/settings?sec=sec-online">打开在线视频源设置</router-link></EmptyState>
    <EmptyState v-else-if="!sources.length" state="empty" title="还没有可用的视频源" text="可显式启用开放样例目录，或添加自己的 MacCMS JSON 来源。"><router-link to="/settings?sec=sec-online">添加视频源</router-link></EmptyState>
    <template v-else>
      <form class="online-search" data-online-search role="search" aria-label="搜索在线视频" @submit.prevent="search(1)">
        <JzField id="online-source" label="视频源"><template #default="field"><select :id="field.id" v-model="sourceId" data-online-source :disabled="loadingSources" @change="sourceChanged"><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option></select></template></JzField>
        <JzField id="online-query" label="片名"><template #default="field"><input :id="field.id" v-model="query" maxlength="100" autocomplete="off" placeholder="输入电影或剧集名称" /></template></JzField>
        <JzButton type="submit" variant="primary" icon="search" :disabled="!canSearch || searching" :loading="searching">{{ selectedSource?.provider === 'open_catalog' && !query.trim() ? '浏览目录' : '搜索' }}</JzButton>
      </form>
      <p class="online-scope">仅查询所选来源；在线内容独立于 NAS 媒体库。<span v-if="selectedSource?.provider === 'open_catalog'">开放样例用于验证播放能力。</span></p>
      <EmptyState v-if="detailLoading" state="loading" title="正在加载详情" text="正在读取所选来源的线路与分集。"><JzButton @click="backToResults">返回搜索结果</JzButton></EmptyState>
      <EmptyState v-else-if="detailError" state="error" title="详情加载失败" :text="detailError" retry @retry="openDetail(lastDetailId)"><JzButton @click="backToResults">返回搜索结果</JzButton></EmptyState>
      <section v-else-if="detail" class="online-detail" data-online-detail>
        <JzButton variant="ghost" icon="arrow-left" @click="backToResults">返回搜索结果</JzButton>
        <header class="online-detail-heading"><div class="online-artwork"><AppIcon :name="detail.item.kind === 'tv' ? 'tv' : 'movie'" :size="48" /></div><div><p class="online-eyebrow">{{ detail.source.name }} · {{ detail.item.kind === 'tv' ? '剧集' : '电影' }}</p><h2>{{ detail.item.title }}</h2><p v-if="detail.year" class="online-year">{{ detail.year }}</p></div></header>
        <p v-if="detail.description" class="online-description">{{ detail.description }}</p>
        <div v-if="detail.attribution?.length" class="online-attribution"><span>内容来源与许可</span><a v-for="credit in detail.attribution" :key="credit.url" :href="safeAttribution(credit.url)" target="_blank" rel="noopener noreferrer">{{ credit.label }}</a></div>
        <h3>播放线路</h3>
        <div class="online-lines" role="group" aria-label="播放线路"><JzButton v-for="line in detail.lines" :key="line.id" :data-online-line="line.id" :aria-pressed="line.id === lineId" @click="selectLine(line.id)">{{ line.label }}</JzButton></div>
        <p class="online-scope">{{ selectedLine?.label || '暂无线路' }}<span v-if="selectedLine"> · {{ selectedLine.entries.length }} {{ detail.item.kind === 'tv' ? '集' : '个播放版本' }}</span></p>
        <div v-if="selectedLine" class="online-entries"><div v-for="(entry, index) in selectedLine.entries" :key="entry.identity?.episode_id || entry.label + ':' + index" class="online-entry"><JzButton :data-online-entry="entry.label" :disabled="!entry.supported || !entry.identity" :variant="entry.supported ? 'primary' : 'secondary'" :icon="entry.supported ? 'play' : undefined" @click="play(entry)">{{ entry.label }}</JzButton><span v-if="!entry.supported">{{ unsupportedReason(entry.reason) }}</span><span v-else>{{ entry.transport?.toUpperCase() }} · 由当前设备播放</span></div></div>
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
    <OnlinePlayerModal v-if="playingIdentity" :online-identity="playingIdentity" :title="playingTitle" :initial-position="initialPosition" @close="closePlayer" @progress="saveProgress" @ended="closePlayer" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { createOnlineCatalogClient, onlineCatalogError } from '../onlineCatalog.js'
import { createOnlineProgressMemory, validateOnlineIdentity } from '../onlinePlayback.js'
import { isChildren, registerViewingPlaybackCleanup } from '../viewing.js'
import EmptyState from '../components/EmptyState.vue'
import JzButton from '../components/JzButton.vue'
import JzField from '../components/JzField.vue'
import AppIcon from '../components/AppIcon.vue'
import OnlinePlayerModal from '../components/OnlinePlayerModal.vue'
import '../styles/online.css'

const client = createOnlineCatalogClient()
const progress = createOnlineProgressMemory()
const sources = ref([]), sourceId = ref(''), enabled = ref(false), query = ref('')
const loadingSources = ref(true), sourcesError = ref(''), searching = ref(false), searched = ref(false), searchError = ref('')
const results = ref([]), page = ref(1), pageCount = ref(1), total = ref(0), loadedQuery = ref('')
const detailLoading = ref(false), detailError = ref(''), detail = ref(null), lineId = ref(''), lastDetailId = ref('')
const playingIdentity = ref(null), playingTitle = ref(''), initialPosition = ref(0)
const selectedSource = computed(() => sources.value.find(source => source.id === sourceId.value))
const canSearch = computed(() => !!sourceId.value && (query.value.trim().length > 0 || selectedSource.value?.provider === 'open_catalog'))
const selectedLine = computed(() => detail.value?.lines.find(line => line.id === lineId.value))
const lastQuery = ref({ page: 1, query: '' })
let disposed = false, generation = 0
const stopCleanup = registerViewingPlaybackCleanup(() => {
  generation++; client.cancel(); closePlayer(); detail.value = null; results.value = []; searched.value = false
  searching.value = detailLoading.value = false
})
function current(turn) { return !disposed && turn === generation && !isChildren.value }
async function loadSources() {
  const turn = ++generation
  loadingSources.value = true; sourcesError.value = ''
  try {
    const response = await client.sources()
    if (!current(turn) || !response) return
    enabled.value = response.enabled; sources.value = response.items || []
    if (!sources.value.some(source => source.id === sourceId.value)) sourceId.value = sources.value[0]?.id || ''
  } catch (error) { if (current(turn)) sourcesError.value = onlineCatalogError(error) }
  finally { if (current(turn)) loadingSources.value = false }
}
function sourceChanged() {
  generation++; client.cancel(); closePlayer(); detail.value = null; detailError.value = ''; detailLoading.value = false
  results.value = []; searched.value = false; searchError.value = ''; searching.value = false; page.value = pageCount.value = 1; total.value = 0
}
async function search(nextPage = 1, text = query.value) {
  if (!sourceId.value || isChildren.value || !text.trim() && selectedSource.value?.provider !== 'open_catalog') return
  const turn = ++generation
  closePlayer(); detail.value = null; detailError.value = ''; detailLoading.value = false
  searching.value = true; searchError.value = ''; lastQuery.value = { page: nextPage, query: text }
  try {
    const response = await client.search(sourceId.value, text, nextPage)
    if (!current(turn) || !response) return
    results.value = response.items || []; page.value = response.page; pageCount.value = response.page_count
    total.value = response.total; loadedQuery.value = text.trim(); searched.value = true
  } catch (error) { if (current(turn)) searchError.value = onlineCatalogError(error) }
  finally { if (current(turn)) searching.value = false }
}
async function openDetail(externalId) {
  const turn = ++generation
  closePlayer(); detail.value = null; detailError.value = ''; detailLoading.value = true; lastDetailId.value = externalId
  try {
    const response = await client.detail(sourceId.value, externalId)
    if (!current(turn) || !response) return
    detail.value = response; lineId.value = response.lines?.find(line => line.entries.some(entry => entry.supported))?.id || response.lines?.[0]?.id || ''
  } catch (error) { if (current(turn)) detailError.value = onlineCatalogError(error) }
  finally { if (current(turn)) detailLoading.value = false }
}
function backToResults() { generation++; client.cancel(); closePlayer(); detail.value = null; detailError.value = ''; detailLoading.value = false }
function selectLine(id) { closePlayer(); lineId.value = id }
function play(entry) {
  if (!entry.supported || !entry.identity) return
  playingIdentity.value = validateOnlineIdentity(entry.identity)
  playingTitle.value = detail.value.item.title + (detail.value.item.kind === 'tv' ? ' · ' + entry.label : '')
  initialPosition.value = progress.get(entry.identity)?.position || 0
}
function closePlayer() { playingIdentity.value = null }
function saveProgress(value) { progress.save(value.identity, value) }
function safeAttribution(value) { try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined } catch { return undefined } }
function unsupportedReason(reason) { return ['host_not_allowed', 'target_host_not_allowed'].includes(reason) ? '媒体主机尚未加入此来源允许列表' : '当前线路不支持直接播放，请选择其他线路' }
onMounted(() => { if (!isChildren.value) void loadSources(); else loadingSources.value = false })
onUnmounted(() => { disposed = true; generation++; client.close(); stopCleanup(); closePlayer() })
</script>
