<template>
  <div class="browse-page cinema-browse" :class="{ 'has-selection': selecting, 'catalogue-tools-ready': !loadError && !scanOpen && !msg }">
  <header class="browse-heading">
    <h1>电影</h1>
  </header>
  <BrowseFeature ref="featureRef" kind="movie" :first-item="firstFeatureItem" :scope="curMediaId" :recent-item="cwRef?.featuredItem"
    :recent-ready="cwRef?.settled" :enabled="featureReady && showContinue && !ai.open && !selecting && !viewing.suspended"
    @resume="resumeMovie">
    <template #recent="{ featuredVersion }">
      <ContinueWatchingRow v-if="browseReady && showContinue && !ai.open && !selecting && !viewing.suspended" ref="cwRef"
        kind="movie" :media-library-id="curMediaId" :featured-version="featuredVersion" :has-catalogue="!!firstFeatureItem" @resume="resumeMovie" />
    </template>
    <template #tools>
      <BrowseToolbar id="movie-browse" v-model="searchInput"
        :label="!ai.open ? '搜索电影' : ai.result ? '追问当前片单' : '智能找片'"
        :placeholder="!ai.open ? '搜片名 / 演员 / 标签' : ai.result ? '继续描述要求，例如：只换第 2 部' : '描述想看的作品、记得的情节或想要的片单'"
        :submit-label="ai.open ? (ai.result ? '提交追问' : '找片') : ''"
        :suggestions-enabled="!ai.open" :maxlength="ai.open ? 500 : undefined"
        :loading="ai.open && ai.busy" :search-disabled="ai.open && (ai.expired || !ai.draft.trim())"
        item-heading="电影" :suggest-open="suggestOpen" :suggest-no-match="suggestNoMatch"
        :suggest-items="suggestItems" :suggest-persons="suggestPersons" :suggest-idx="suggestIdx"
        :ai-open="ai.open" :ai-enabled="!isChildren" @update:ai-open="setAiOpen"
        @input="!ai.open && onQInput($event)" @compositionstart="!ai.open && (composing = true)" @compositionend="!ai.open && onCompositionEnd()"
        @enter="ai.open ? discovery.submit() : onSearchEnter($event)" @move="suggestMove" @close="closeSuggest" @blur="!ai.open && onQBlur()"
        @pick-item="pickMovie" @pick-person="pickPerson" @hover="suggestIdx = $event" @search="ai.open ? discovery.submit() : submitQuery()">
        <template #filters>
          <BrowseFilters :model-value="sel" :facets="facets" kind="movie" v-model:open="filtersOpen"
            :scope-label="filterScope" :scope-key="curMediaId" :context-key="route.fullPath"
            :loading="facetsLoading" :error="facetsError" @retry="loadFacets"
            @open="closeSuggest" @apply="applyFilters" />
        </template>
        <div v-if="!isChildren" class="browse-actions">
          <ActionMenu label="管理">
            <router-link :to="toolsLink"><AppIcon name="organize" :size="18" />扫描与整理</router-link>
            <JzButton icon="scan" variant="ghost" @click="scanOpen = !scanOpen">扫描新文件</JzButton>
            <JzButton icon="upload" variant="ghost" @click="upDlg = true">上传文件</JzButton>
          </ActionMenu>
        </div>
      </BrowseToolbar>
    </template>
  </BrowseFeature>
  <div id="movie-browse-ai"><AiSearchPanel v-if="ai.open" :state="ai" kind="movie" :scope-label="filterScope" :chips="selectedChips"
    @cancel="discovery.cancel" @retry="discovery.retry" @restart="discovery.restart" @ask="discovery.ask"
    @fill="discovery.fill" @remember="discovery.remember" @public="discovery.searchPublic" /></div>
  <ScanAction v-if="scanOpen" kind="movie" @done="onUpDone" />
  <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>

  <div v-show="!ai.open" class="browse-results">
  <BrowseFilterSummary :chips="selectedChips" :query="appliedQuery" fallback-id="movie-browse-input"
    @remove="removeFilter" @clear="clearFilters" @clear-query="clearQuery" />

  <BrowseResultsHeader v-if="items.length" :title="q.trim() || activeCount ? '筛选结果' : '全部影片'"
    :count="wallCountText" :sort="sort" :options="WALL_SORTS" :rating-source="sel.ratingSource"
    :relevance="!!q.trim()" @sort="pickSort" />

  <div class="grid" :aria-busy="loading">
    <div v-for="m in items" :key="m.id" :data-browse-id="m.id" :class="['card', { sel: selectedIds.has(m.id) }]"
      tabindex="0" :role="selecting ? 'button' : 'link'" :aria-pressed="selecting ? selectedIds.has(m.id) : undefined"
      :aria-label="selecting ? '选择 ' + m.title : '查看 ' + m.title + ' 详情'"
      @keydown.enter.self="onCard(m)" @keydown.space.self="onCardSpace($event, m)"
      :title="m.added_at ? ('入库 ' + fmtDate(m.added_at)) : ''" @click="onCard(m)">
      <div class="poster-wrap">
        <button v-if="!isChildren" :class="['sel-circle', { on: selectedIds.has(m.id) }]"
          @click.stop="toggleSelect(m.id)" :aria-pressed="selectedIds.has(m.id)" aria-label="选择" type="button">
          <AppIcon name="check" :size="18" />
        </button>
        <WallPoster :src="posterUrl(m.poster_path)" :title="m.title" />
        <button v-if="!selecting" class="poster-play" :disabled="playBusy !== null"
          :title="'播放 ' + (m.title || '')" :aria-label="'播放 ' + (m.title || '')"
          @click.stop="playMovie(m)">
          <Spinner v-if="playBusy === m.id" :size="18" />
          <PlayerIcon v-else name="play" :size="24" />
        </button>
        <span v-if="m.watched" class="watched-badge"><AppIcon name="check" :size="14" />已看</span>
        <span v-if="!m.tmdb_id || m.needs_review" class="unmatched-badge" :title="!m.tmdb_id ? '尚未匹配 TMDB，点击卡片进详情匹配' : '请进入详情确认匹配结果'">{{ !m.tmdb_id ? '未匹配' : '待确认' }}</span>
      </div>
      <div class="t">
        <span class="card-title" :title="m.title">{{ m.title }}</span>
        <div class="card-metadata">
          <span v-if="m.year">{{ m.year }}</span>
          <span v-if="m.version_count > 1" :title="m.version_count + ' 个版本'">×{{ m.version_count }}</span>
          <ScoreBadge :score="m.tmdb_rating" source="tmdb" />
          <ScoreBadge :score="m.custom_rating" source="custom" />
        </div>
        <span v-if="m.region || m.genres?.length" class="card-meta">{{ [m.region, (m.genres || []).slice(0, 2).join('/')].filter(Boolean).join(' · ') }}</span>
      </div>
    </div>
  </div>
  <EmptyState v-if="loading && !items.length || !firstLoaded && !loadError" state="loading" title="正在加载影片" text="请稍候…" />
  <EmptyState v-else-if="loadError" state="error" title="影片加载失败" :text="loadError" retry @retry="retryLoad" />
  <EmptyState v-else-if="!items.length && !q.trim() && !activeCount && (isChildren || isPrivate)" state="empty"
    :title="isChildren ? '当前没有适合儿童的影片' : '还没有隐私影片'"
    :text="isChildren ? '可退出儿童模式，在影片的“内容可见性”中确认适合儿童的内容。' : '返回普通模式，从影片详情的“更多操作 → 内容可见性”中加入隐私，也可在电影墙多选后设置。'" />
  <EmptyState v-else-if="showEmptyGuide" state="empty" title="当前媒体库还没有影片" text="扫描已有文件，或上传影片开始观看。">
    <JzButton @click="scanOpen = !scanOpen" type="button" icon="scan">扫描新文件</JzButton><router-link :to="pipelineLink"><AppIcon name="organize" :size="18" />扫描与整理<AppIcon name="chevron-right" :size="16" /></router-link>
  </EmptyState>
  <EmptyState v-else-if="firstLoaded && !items.length" state="no-results" title="没有符合条件的影片" text="试试其他片名，或重置筛选条件。">
    <JzButton @click="clearAll" type="button" icon="filter">重置筛选</JzButton>
    <JzButton v-if="!isChildren && q.trim()" icon="ai" @click="setAiOpen(true)">用智能搜索找片</JzButton>
  </EmptyState>

  <div ref="loadSentinel" class="load-more">
    <JzButton v-if="hasMore" @click="loadMore" :disabled="loadingMore" type="button" icon="more">{{ loadingMore ? '加载中…' : '加载更多' }}</JzButton>
    <span v-else-if="items.length" class="fhint">已全部加载（{{ items.length }} 部）</span>
  </div>

  </div>

  <div v-if="selecting && !ai.open" class="floatbar" role="toolbar" aria-label="多选操作" :aria-busy="batching">
    <span class="count">已选 {{ selectedIds.size }} 部</span>
    <div class="batch-desktop-actions" :inert="batching">
    <ContentPolicyButton kind="movie" :ids="[...selectedIds]" compact @saved="onPolicySaved" />
    <JzButton variant="ghost" size="compact" @click="selectAllVisible" :disabled="!items.length" :title="`全选已加载的 ${items.length} 部（不含未加载页）`" type="button">
      <AppIcon name="check-circle" :size="18" />
      <span>全选</span>
    </JzButton>
    <JzButton variant="ghost" size="compact" @click="batchWatched(true)" :disabled="batching" title="标为已看" type="button">
      <AppIcon name="eye" :size="18" />
      <span>已看</span>
    </JzButton>
    <JzButton variant="ghost" size="compact" @click="batchWatched(false)" :disabled="batching" title="标为未看" type="button">
      <AppIcon name="eye-off" :size="18" />
      <span>未看</span>
    </JzButton>
    <JzButton variant="ghost" size="compact" @click="openTagDlg" title="批量标签" type="button">
      <AppIcon name="tag" :size="18" />
      <span>标签</span>
    </JzButton>
    <JzButton variant="ghost" size="compact" @click="openColDlg" title="加入合集" type="button">
      <AppIcon name="collections" :size="18" />
      <span>合集</span>
    </JzButton>
    <JzButton variant="ghost" size="compact" @click="openDelDlg" title="删除选中影片" type="button">
      <AppIcon name="delete" :size="18" />
      <span>删除</span>
    </JzButton>
    </div>
    <div class="batch-mobile-actions" :inert="batching">
      <JzButton variant="ghost" size="compact" :disabled="batching" @click="batchWatched(true)" icon="eye">已看</JzButton>
      <ActionMenu class="batch-more" label="更多">
        <ContentPolicyButton kind="movie" :ids="[...selectedIds]" compact @saved="onPolicySaved" />
        <JzButton variant="ghost" @click="selectAllVisible" :disabled="!items.length" icon="check-circle">全选已加载影片</JzButton>
        <JzButton variant="ghost" @click="batchWatched(false)" icon="eye-off">标为未看</JzButton>
        <JzButton variant="ghost" @click="openTagDlg" icon="tag">批量标签</JzButton>
        <JzButton variant="ghost" @click="openColDlg" icon="collections">加入合集</JzButton>
        <JzButton variant="ghost" @click="openDelDlg" icon="delete">删除选中影片</JzButton>
      </ActionMenu>
    </div>
    <JzButton class="batch-cancel" variant="ghost" size="compact" :disabled="batching" @click="clearSelection" title="退出多选" type="button">
      <AppIcon name="close" :size="18" />
      <span>取消</span>
    </JzButton>
    <span v-if="batchMsg || batching" class="fmsg" role="status">{{ batching ? '提交中…' : batchMsg }}</span>
  </div>

  <JzDialog v-if="tagDlg" :title="`批量标签（${selectedIds.size} 部）`" size="small" :busy="batching" @close="tagDlg = false">
    <fieldset class="batch-dialog-fields" :disabled="batching">
      <div class="bar batch-tag-mode">
        <label><input type="radio" value="add" v-model="tagMode" /> 追加</label>
        <label><input type="radio" value="remove" v-model="tagMode" /> 移除</label>
      </div>
      <label for="batch-new-tag">新标签</label>
      <div class="bar">
        <input id="batch-new-tag" v-model="newTag" placeholder="输入标签，回车加入待办" @keyup.enter="onTagEnter" />
        <JzButton @click="queueNewTag" type="button" icon="plus">加入</JzButton>
      </div>
      <div v-if="pendingTags.length" class="bar">待{{ tagMode === 'add' ? '追加' : '移除' }}：<JzButton v-for="t in pendingTags" :key="t" class="chip on" size="compact" :aria-label="'移除待提交标签 ' + t" @click="dropPending(t)">{{ t }}<AppIcon name="close" :size="14" /></JzButton></div>
      <div class="taglist">
        <JzButton v-for="t in facets.tags" :key="t.value" class="chip tag" size="compact" @click="queueExisting(t.value)">{{ t.value }} {{ t.count }}</JzButton>
      </div>
    </fieldset>
    <template #footer>
      <p v-if="batchMsg" class="batch-result" role="status">{{ batchMsg }}</p>
      <JzButton :disabled="batching" @click="tagDlg = false">取消</JzButton>
      <JzButton variant="primary" :loading="batching" :disabled="!pendingTags.length" @click="confirmBatchTags">确认提交</JzButton>
    </template>
  </JzDialog>

  <JzDialog v-if="colDlg" :title="selectedIds.size ? `加入合集（${selectedIds.size} 部）` : '加入合集'" size="small" :busy="batching" @close="colDlg = false">
    <fieldset class="batch-dialog-fields" :disabled="batching || !selectedIds.size">
      <label for="batch-collection-search">搜索合集</label>
      <div class="bar"><input id="batch-collection-search" v-model="colQ" type="search" placeholder="输入合集名称" /></div>
      <ul class="collist">
        <li v-for="c in filteredCols" :key="c.id"><span>{{ c.name }}（{{ c.member_count }}）</span><JzButton @click="joinCollection(c.id)" type="button" icon="plus">加入</JzButton></li>
      </ul>
      <label for="batch-new-collection">新建合集</label>
      <div class="bar"><input id="batch-new-collection" v-model="newCol" placeholder="新建合集名（含当前选中）" /><JzButton @click="createAndJoin" :disabled="!newCol.trim()" type="button" icon="plus">创建并加入</JzButton></div>
    </fieldset>
    <template #footer><p v-if="batchMsg" class="batch-result" role="status">{{ batchMsg }}</p><JzButton :disabled="batching" @click="colDlg = false">关闭</JzButton></template>
  </JzDialog>

  <JzDialog v-if="delDlg" :title="`删除影片（${delSummary.total_movies} 部）`" size="small" :busy="batching" @close="delDlg = false">
    <p class="del-warn">警告：将永久删除磁盘文件与库记录（海报/镜像缓存保留），不可恢复。每部片的全部版本与附属文件（花絮/字幕/NFO/周边）都会一起删除。</p>
    <p class="del-sum">{{ delSummary.total_movies }} 部影片 · {{ delSummary.total_versions }} 个正片版本 · {{ delSummary.total_files }} 个文件 · 共 {{ fmtBytes(delSummary.total_bytes) }}</p>
    <ul class="collist">
      <li v-for="p in visibleDelPlans" :key="p.id"><span>{{ p.title || '(未命名)' }}<span v-if="p.year"> ({{ p.year }})</span></span><span class="fhint">{{ p.feature_count }} 版本 · {{ p.extra_count }} 附属 · {{ fmtBytes(p.total_size) }}</span></li>
    </ul>
    <p v-if="delPlans.length > 20" class="fhint">等共 {{ delPlans.length }} 部<span v-if="!showAllDel">（仅列前 20）</span> <JzButton v-if="!showAllDel" @click="showAllDel = true" type="button">展开全部</JzButton></p>
    <template #footer>
      <p v-if="batchMsg" class="batch-result" role="status">{{ batchMsg }}</p>
      <JzButton :disabled="batching" @click="delDlg = false">{{ delDone ? '关闭' : '取消' }}</JzButton>
      <JzButton v-if="!delDone" :loading="batching" :disabled="!delPlans.length" @click="confirmDel" variant="danger" icon="delete">确认删除 {{ delSummary.total_movies }} 部影片（{{ delSummary.total_files }} 个文件）</JzButton>
    </template>
  </JzDialog>

  <PlayerModal v-if="playVid" :key="'movie:' + playVid" ref="playerRef" :versionId="playVid" :title="playTitle"
    kind="movie" @close="closeStream" @watched="onPlayWatched"
    @version-change="playVid = $event.id; playTitle = $event.label" />

  <UploadDialog v-if="upDlg" @close="upDlg = false" @done="onUpDone" />
  </div>
</template>
<script setup>
import { useAssistantPage } from '../assistantPage.js'
import ContentPolicyButton from '../components/ContentPolicyButton.vue'
import { isChildren, isPrivate, viewing } from '../viewing.js'
import WallPoster from '../components/WallPoster.vue'

import AppIcon from '../components/AppIcon.vue'

import JzButton from '../components/JzButton.vue'
import JzDialog from '../components/JzDialog.vue'

import BrowseResultsHeader from '../components/BrowseResultsHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import BrowseToolbar from '../components/BrowseToolbar.vue'
import BrowseFilters from '../components/BrowseFilters.vue'
import BrowseFilterSummary from '../components/BrowseFilterSummary.vue'
import { defaultBrowseFilters, normalizeBrowseFilters, filterChips, countBrowseFilters, removeBrowseFilter } from '../browseFilters.js'
import AiSearchPanel from '../components/AiSearchPanel.vue'
import { useAiDiscovery } from '../useAiDiscovery.js'
import { browseKey, saveBrowse, readBrowse, captureAnchor, restoreBrowsePosition } from '../browseHistory.js'

import PlayerIcon from '../components/PlayerIcon.vue'

import ScanAction from '../components/ScanAction.vue'
import ActionMenu from '../components/ActionMenu.vue'
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { currentMediaId, preferredVideoLibId, mediaParam, switchLib, switchMedia,
         loadLibs, onLibChange, listMediaLibs } from '../libraries.js'
import ScoreBadge from '../components/ScoreBadge.vue'
import Spinner from '../components/Spinner.vue'
import UploadDialog from '../components/UploadDialog.vue'
import BrowseFeature from '../components/BrowseFeature.vue'
import ContinueWatchingRow from '../components/ContinueWatchingRow.vue'
import PlayerModal from '../components/PlayerModal.vue'
import { getCaps } from '../caps.js'
import { fmtBytes, fmtDate } from '../format.js'
import { WALL_SORTS, loadWallSort, normalizeWallSort, saveWallSort,
         toggleWallSort, wallSortParams } from '../wallSort.js'

const filtersOpen = ref(false)
const toolsLink = computed(() => {
  const media = curMediaId.value
  const library = preferredVideoLibId('movie')
  return { path: '/settings', query: library ? { sec: 'sec-libtools', media, library } : { sec: 'sec-libraries', media } }
})
const route = useRoute()
const router = useRouter()
const appliedQuery = computed(() => String(route.query.q || ''))
const filterScope = computed(() => listMediaLibs().find(m => Number(m.id) === curMediaId.value)?.name || '当前媒体库')
const selectedChips = computed(() => filterChips(sel.value, facets.value, 'movie'))
function applyFilters(value) {
  sel.value = normalizeBrowseFilters(value, 'movie')
  closeSuggest()
  applyAndLoad()
}
function removeFilter(chip) { applyFilters(removeBrowseFilter(sel.value, chip, 'movie')) }
function clearQuery() {
  q.value = ''
  closeSuggest()
  applyAndLoad()
}

const q = ref('')
const suggestOpen = ref(false)
const suggestItems = ref([])
const suggestPersons = ref([])
const suggestNoMatch = ref(false)
const suggestIdx = ref(-1)
let suggestTimer = null
let suggestSeq = 0
let composing = false
const loadSentinel = ref(null)
let loadIO = null
const items = ref([])
const msg = ref('')
const scanOpen = ref(false)
const PAGE = 60                 // 每页条数（评审 P1-11：>500 部不再被后端默认截断）
const hasMore = ref(false)
const loadingMore = ref(false)
const loadError = ref('')
const loading = ref(false)
const loadErrorMore = ref(false)
let loadSeq = 0
let disposed = false
const emptyFacets = () => ({ genres: [], regions: [], countries: [], years: [], decades: [], tags: [], collections: [], watched: { watched: 0, unwatched: 0 }, ratings: { tmdb: [], douban: [], custom: [] } })
const facets = ref(emptyFacets())
const facetsLoading = ref(false)
const facetsError = ref('')
let facetsSeq = 0
let facetsScope

const sel = ref(defaultBrowseFilters())

// 海报墙排序：默认按入库时间倒序（localStorage 记忆 + URL ?sort=&order= 可分享）
const sort = ref(loadWallSort(localStorage))
const curMediaId = ref(currentMediaId())
const discovery = useAiDiscovery({ kind: 'movie', mediaId: curMediaId, ordinaryQuery: q, selection: sel, route, viewing })
const ai = discovery.state
const searchInput = discovery.input
function setAiOpen(value, reveal = false) {
  composing = false
  clearTimeout(suggestTimer)
  suggestSeq++
  closeSuggest()
  filtersOpen.value = false
  if (value) discovery.open({ reveal })
  else discovery.close()
}

const cwRef = ref(null)
const featureRef = ref(null)
const browseReady = ref(false)
const featureReady = ref(false)
const loadedWallKey = ref('')
let mediaRevision = 0
const firstFeatureItem = computed(() => items.value.length && loadedWallKey.value === historyKey() ? items.value[0] : null)
watch(curMediaId, () => {
  mediaRevision++
  resetBatchScope()
  loadSeq++
  items.value = []
  hasMore.value = false
}, { flush: 'sync' })
const showContinue = computed(() =>
  !activeCount.value && !q.value.trim())
const playVid = ref(null)
const playTitle = ref('')
const playBusy = ref(null)   // 多版本智能选版中的卡片 id（转圈防重复点）
const playerRef = ref(null)

function pickSort(key) {
  sort.value = toggleWallSort(sort.value, key)
  saveWallSort(localStorage, sort.value)
  applyAndLoad()
}
async function refreshContinue() {
  if (cwRef.value && cwRef.value.reload) await cwRef.value.reload()
}

const watchedCounts = computed(() => facets.value.watched || { watched: 0, unwatched: 0 })

// 「全部影片」标题行计数：有筛选/搜索时显示筛选结果数，否则显示库内总片数（海报粒度）
const wallCountText = computed(() => {
  if (q.value.trim() || activeCount.value) {
    return `筛选出 ${items.value.length}${hasMore.value ? '+' : ''} 部`
  }
  const w = watchedCounts.value
  const total = (w.watched || 0) + (w.unwatched || 0)
  return total ? `共 ${total} 部` : ''
})

// 入库流程深链：媒体库级（设置页库工具顶层为媒体库，视频库作为分表维度）
const pipelineLink = computed(() => {
  const mid = currentMediaId()
  return { path: '/settings', query: mid != null ? { sec: 'sec-pipeline', media: String(mid) } : { sec: 'sec-pipeline' } }
})

// 多选态（海报粒度：selectedIds 存代表行 id，服务端展开到同 tmdb 全版本）
// Plex 式：首勾自动进入，清空/Esc 自动退出，无手动开关
const selectedIds = ref(new Set())
const selecting = computed(() => selectedIds.value.size > 0)
const batching = ref(false)
const batchMsg = ref('')
const tagDlg = ref(false)
const tagMode = ref('add')
const newTag = ref('')
const pendingTags = ref([])
const colDlg = ref(false)
const colQ = ref('')
const newCol = ref('')
const colItems = ref([])
const filteredCols = computed(() => {
  const q = colQ.value.trim()
  const src = colItems.value.length ? colItems.value : (facets.value.collections || [])
  if (!q) return src
  return src.filter(c => (c.name || '').includes(q))
})

const activeCount = computed(() => countBrowseFilters(sel.value))
const firstLoaded = ref(false)
const showEmptyGuide = computed(() =>
  firstLoaded.value && !items.value.length && !activeCount.value &&
  !q.value.trim() && !loadError.value)

function _qNorm(query) {
  const parts = []
  for (const k of Object.keys(query || {}).sort()) {
    const v = query[k]
    parts.push(k + '=' + (Array.isArray(v) ? v.join(',') : String(v ?? '')))
  }
  return parts.join('&')
}
function syncUrl() {
  const query = {}
  if (q.value.trim()) query.q = q.value.trim()
  if (sel.value.genres.length) query.genre = sel.value.genres.join(',')
  if (sel.value.regions.length) query.region = sel.value.regions.join(',')
  if (sel.value.countries.length) query.country = sel.value.countries.join(',')
  if (sel.value.years.length) query.year = sel.value.years.join(',')
  if (sel.value.decades.length) query.decade = sel.value.decades.join(',')
  if (sel.value.tags.length) query.tag = sel.value.tags.join(',')
  if (sel.value.watched != null) query.watched = String(sel.value.watched)
  if (sel.value.rating != null) query.min_rating = String(sel.value.rating)
  if (sel.value.rating != null || sort.value.key === 'rating') {
    if (sel.value.ratingSource !== 'tmdb') query.rating_source = sel.value.ratingSource
  }
  Object.assign(query, wallSortParams(sort.value))
  const lp = mediaParam()
  if (lp != null) query.media = String(lp)
  const changed = _qNorm(query) !== _qNorm(route.query)
  if (changed) router.replace({ path: '/', query })
  return changed   // 变则交给 route.query watcher 加载；未变由调用方显式刷新
}
function readUrl() {
  const s = (v) => v ? String(v).split(',').map(x => x.trim()).filter(Boolean) : []
  const src = String(route.query.rating_source || 'tmdb')
  const wq = Array.isArray(route.query.watched) ? route.query.watched[0] : route.query.watched
  // 分享链接可携带 media（媒体库）；旧链接的 lib（视频库）兼容映射到其媒体库
  const qmedia = Array.isArray(route.query.media) ? route.query.media[0] : route.query.media
  if (qmedia != null && qmedia !== '' && Number(qmedia) !== currentMediaId()) {
    try { switchMedia(Number(qmedia)) } catch (e) { /* 未知媒体库忽略 */ }
  }
  const qlib = Array.isArray(route.query.lib) ? route.query.lib[0] : route.query.lib
  if (qmedia == null || qmedia === '') {
    if (qlib != null && qlib !== '') {
      try { switchLib(Number(qlib)) } catch (e) { /* 未知库忽略 */ }
    }
  }
  q.value = route.query.q || ''
  // 排序：URL 带 sort 优先（可分享）；否则用 localStorage 记忆（默认最近添加↓）
  const qs = Array.isArray(route.query.sort) ? route.query.sort[0] : route.query.sort
  if (qs != null && qs !== '') {
    const qo = Array.isArray(route.query.order) ? route.query.order[0] : route.query.order
    sort.value = normalizeWallSort({ key: qs, order: qo })
  } else {
    sort.value = loadWallSort(localStorage)
  }
  sel.value = normalizeBrowseFilters({
    genres: s(route.query.genre),
    regions: s(route.query.region),
    countries: s(route.query.country),
    years: s(route.query.year),
    decades: s(route.query.decade),
    tags: s(route.query.tag),
    watched: wq != null && wq !== '' ? Number(wq) : null,
    rating: route.query.min_rating != null && route.query.min_rating !== '' ? Number(route.query.min_rating) : null,
    ratingSource: src,
  })
  curMediaId.value = currentMediaId()
}
function buildParams(offset = 0) {
  const p = new URLSearchParams()
  if (q.value.trim()) p.set('q', q.value.trim())
  // 选了具体国家时大区自动让位（后端两者是AND，避免华语+US这种空交集）
  const useRegion = sel.value.countries.length ? [] : sel.value.regions
  for (const [key, vals] of [['genre', sel.value.genres], ['region', useRegion],
      ['country', sel.value.countries], ['year', sel.value.years],
      ['decade', sel.value.decades], ['tag', sel.value.tags]]) {
    for (const v of vals) p.append(key, v)
  }
  if (sel.value.watched != null) p.set('watched', String(sel.value.watched))
  if (sel.value.rating != null) p.set('min_rating', String(sel.value.rating))
  if (sel.value.rating != null || sort.value.key === 'rating') {
    p.set('rating_source', sel.value.ratingSource)
  }
  p.set('sort', sort.value.key)
  p.set('order', sort.value.order)
  const lp = mediaParam()
  if (lp != null) p.set('media_library', String(lp))
  p.set('limit', String(PAGE))   // 分页（评审 P1-11）：加载更多而非一次全量
  p.set('offset', String(Math.max(0, offset)))
  return p.toString()
}
async function load() {
  const seq = ++loadSeq
  const key = historyKey()
  loading.value = true
  loadingMore.value = false
  loadError.value = ''
  loadErrorMore.value = false
  try {
    const d = await api('/api/search?' + buildParams(0))
    if (seq !== loadSeq) return          // 更新的筛选已接管，丢弃过期回包
    items.value = d.items || []
    loadedWallKey.value = key
    hasMore.value = !!d.has_more
    loadError.value = ''
  } catch (e) {
    if (seq === loadSeq) loadError.value = '加载失败：' + e.message
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
async function loadMore() {
  if (ai.open || !hasMore.value || loading.value) return
  const seq = ++loadSeq
  loading.value = true
  loadingMore.value = true
  loadError.value = ''
  try {
    const d = await api('/api/search?' + buildParams(items.value.length))
    if (seq !== loadSeq) return
    const known = new Set(items.value.map(m => m.id))
    for (const m of (d.items || [])) {
      if (!known.has(m.id)) items.value.push(m)
    }
    hasMore.value = !!d.has_more
    loadError.value = ''
  } catch (e) {
    if (seq === loadSeq) { loadError.value = '加载更多失败：' + e.message; loadErrorMore.value = true }
  } finally {
    if (seq === loadSeq) { loading.value = false; loadingMore.value = false }
  }
}
function retryLoad() { return loadErrorMore.value ? loadMore() : load() }
async function applyAndLoad() {
  // URL 变了 → route.query watcher 统一加载；没变（如回车搜索词未改）才显式刷新。
  // 修复评审 B5a-9/R04-D3：此前这里与 watcher 各发一次完全相同的 /api/search
  if (!syncUrl()) await load()
}
// 搜索联想：输入防抖拉本地库（影片 标题+年份 / 演员 名字+参演数），↑↓选择 / Enter选中 / Esc关闭
function onQInput(e) {
  if (composing || (e && e.isComposing)) return
  scheduleSuggest()
}
function onCompositionEnd() {
  composing = false
  scheduleSuggest()
}
function scheduleSuggest() {
  clearTimeout(suggestTimer)
  const term = q.value.trim()
  if (!term) {
    closeSuggest()
    return
  }
  suggestTimer = setTimeout(fetchSuggest, 180)
}
async function fetchSuggest() {
  const term = q.value.trim()
  if (!term) return
  const seq = ++suggestSeq
  try {
    const d = await api('/api/search/suggest?q=' + encodeURIComponent(term) + '&limit=8'
      + (mediaParam() != null ? '&media_library=' + mediaParam() : ''))
    if (seq !== suggestSeq) return
    suggestItems.value = (d && d.items) || []
    suggestPersons.value = (d && d.persons) || []
    suggestNoMatch.value = !suggestItems.value.length && !suggestPersons.value.length
    suggestIdx.value = -1
    suggestOpen.value = true
  } catch (e) {
    if (seq === suggestSeq) closeSuggest()
  }
}
function suggestRow(idx) {
  if (idx < 0) return null
  if (idx < suggestItems.value.length) return { kind: 'movie', v: suggestItems.value[idx] }
  const j = idx - suggestItems.value.length
  if (j < suggestPersons.value.length) return { kind: 'person', v: suggestPersons.value[j] }
  return null
}
function suggestMove(d) {
  if (!suggestOpen.value) return
  const n = suggestItems.value.length + suggestPersons.value.length
  if (!n) return
  let i = suggestIdx.value + d
  if (i < 0) i = n - 1
  if (i >= n) i = 0
  suggestIdx.value = i
}
function pickRow(row) {
  q.value = row.kind === 'movie' ? row.v.title : row.v.name
  submitQuery()
}
function pickMovie(s) { pickRow({ kind: 'movie', v: s }) }
// 演员联想直跳人物页（评审 B8/R04-B5：比“按名字搜片”更准，避免同名混淆）
function pickPerson(p) {
  clearTimeout(suggestTimer)
  suggestSeq++
  closeSuggest()
  router.push('/p/' + p.tmdb_id)
}
function closeSuggest() {
  suggestOpen.value = false
  suggestItems.value = []
  suggestPersons.value = []
  suggestNoMatch.value = false
  suggestIdx.value = -1
}
function onQBlur() {
  setTimeout(closeSuggest, 120)
}
function onSearchEnter(e) {
  if (e && (e.isComposing || e.keyCode === 229)) return
  const row = suggestOpen.value ? suggestRow(suggestIdx.value) : null
  if (row) {
    pickRow(row)
    return
  }
  submitQuery()
}
function submitQuery() {
  clearTimeout(suggestTimer)
  suggestSeq++
  closeSuggest()
  return applyAndLoad()
}
async function showAll() {
  q.value = ''
  sel.value = defaultBrowseFilters()
  if (!syncUrl()) await load()
}
async function clearFilters() {
  closeSuggest()
  sel.value = defaultBrowseFilters()
  await applyAndLoad()
}
async function clearAll() { await showAll() }
// 上传/归档完成后刷新海报墙与 facets（UploadDialog 内部只发信号）
async function onUpDone() { await loadFacets(); await load() }
async function loadFacets() {
  const seq = ++facetsSeq
  const scope = currentMediaId()
  if (facetsScope !== scope) {
    facets.value = emptyFacets()
    colItems.value = []
    facetsScope = scope
  }
  facetsLoading.value = true
  facetsError.value = ''
  const lq = mediaParam() != null ? ('?media_library=' + mediaParam()) : ''
  try {
    const [facetResult, collectionsResult] = await Promise.allSettled([
      api('/api/facets' + lq), api('/api/collections' + lq),
    ])
    if (disposed || seq !== facetsSeq) return
    if (facetResult.status === 'fulfilled') facets.value = facetResult.value
    else facetsError.value = '筛选选项加载失败：' + facetResult.reason.message
    if (collectionsResult.status === 'fulfilled') colItems.value = collectionsResult.value.items || []
    else msg.value = '合集列表加载失败：' + collectionsResult.reason.message
  } catch (e) {
    if (!disposed && seq === facetsSeq) facetsError.value = '筛选选项加载失败：' + e.message
  } finally {
    if (!disposed && seq === facetsSeq) facetsLoading.value = false
  }
}

function onCard(m) {
  if (selecting.value) toggleSelect(m.id)
  else router.push('/m/' + m.id)
}
function onCardSpace(event, movie) {
  if (!selecting.value) return
  event.preventDefault()
  toggleSelect(movie.id)
}
// 继续观看行 ▶：本页直接续播（断点已存在，PlayerModal 自己会按 version_id 续起）
function resumeMovie(m) {
  playVid.value = Number((m.progress && m.progress.version_id) || m.id)
  playTitle.value = m.title || ''
}
// 海报墙 ▶：直接播放。单版本零等待；多版本先问服务端 best_version_id（带客户端 caps），
// 避免误播浏览器不可播的 DV/4K 版（与详情页选版一致），失败回落代表版本。
async function playMovie(m) {
  if (playBusy.value !== null) return
  const id = Number(m.id)
  let vid = id
  if (Number(m.version_count || 1) > 1) {
    playBusy.value = id
    try {
      const caps = await getCaps()
      const agg = await api('/api/stream/versions', {
        method: 'POST',
        body: JSON.stringify({ movie_id: id, quality: 'auto', caps }),
      })
      if (agg && agg.best_version_id) vid = Number(agg.best_version_id)
    } catch (e) { /* 选版失败回落代表版本 */ }
    finally { playBusy.value = null }
  }
  playTitle.value = (m.title || '') + (m.year ? ` (${m.year})` : '')
  playVid.value = vid
}
async function closeStream() {
  // 与 Detail 一致：先取最终存档 Promise，再关窗，落库后刷新继续观看行与海报墙
  const saving = (playerRef.value && playerRef.value.saveFinal)
    ? playerRef.value.saveFinal() : Promise.resolve()
  playVid.value = null
  Promise.resolve(saving).catch(() => { /* 忽略 */ }).then(() => {
    refreshContinue()
    load()
  })
}
async function onPlayWatched() {
  // 海报粒度：同片全版本标已看；继续观看行重拉，已看完的自动消失
  try {
    await api('/api/movies/batch', {
      method: 'POST',
      body: JSON.stringify({ ids: [Number(playVid.value)], ops: { watched: true } }),
    })
  } catch (e) { /* 忽略 */ }
  refreshContinue()
}
async function onPolicySaved() { clearSelection(); await loadFacets(); await load(); await refreshContinue() }
function toggleSelect(id) {
  if (batching.value) return
  const s = new Set(selectedIds.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  selectedIds.value = s
}
function selectAllVisible() {
  const s = new Set(selectedIds.value)
  for (const m of items.value) s.add(m.id)
  selectedIds.value = s
}
function clearSelection() {
  selectedIds.value = new Set()
}
function resetBatchScope() {
  clearSelection()
  tagDlg.value = false
  colDlg.value = false
  delDlg.value = false
  pendingTags.value = []
  newTag.value = ''
  newCol.value = ''
  colQ.value = ''
  delPlans.value = []
  previewIds.value = []
  batchMsg.value = ''
  batching.value = false
}
function currentBatch(revision) {
  return !disposed && revision === mediaRevision
}
async function batchRequest(path, payload, errorPrefix, success) {
  if (batching.value || !selectedIds.value.size) return
  const revision = mediaRevision
  batching.value = true
  batchMsg.value = ''
  try {
    const response = await api(path, { method: 'POST', body: JSON.stringify(payload) })
    if (currentBatch(revision)) await success(response, revision)
  } catch (e) {
    if (currentBatch(revision)) batchMsg.value = errorPrefix + e.message
  } finally {
    if (currentBatch(revision)) batching.value = false
  }
}
async function refreshAfterBatch(revision) {
  await loadFacets()
  if (!currentBatch(revision)) return
  await load()
  if (currentBatch(revision)) await refreshContinue()
}
function batchCall(ops) {
  return batchRequest('/api/movies/batch', { ids: [...selectedIds.value], ops }, '批量失败：', async (d, revision) => {
    batchMsg.value = `完成：${d.affected_versions} 个文件版本`
    clearSelection()
    tagDlg.value = false
    pendingTags.value = []
    await refreshAfterBatch(revision)
  })
}
async function batchWatched(w) {
  await batchCall({ watched: w })
}
function openTagDlg() {
  pendingTags.value = []
  newTag.value = ''
  tagMode.value = 'add'
  batchMsg.value = ''
  tagDlg.value = true
}
function queueNewTag() {
  const t = newTag.value.trim().slice(0, 20)
  if (t && !pendingTags.value.includes(t)) pendingTags.value.push(t)
  newTag.value = ''
}
function onTagEnter(e) {
  if (e && (e.isComposing || e.keyCode === 229)) return
  queueNewTag()
}
function queueExisting(t) {
  if (!pendingTags.value.includes(t)) pendingTags.value.push(t)
}
function dropPending(t) {
  pendingTags.value = pendingTags.value.filter(x => x !== t)
}
async function confirmBatchTags() {
  if (!pendingTags.value.length) return
  const ops = tagMode.value === 'add'
    ? { add_tags: pendingTags.value }
    : { remove_tags: pendingTags.value }
  await batchCall(ops)
}
function openColDlg() {
  colQ.value = ''
  newCol.value = ''
  batchMsg.value = ''
  colDlg.value = true
}
// 整片删除：先预览影响（片名+版本/附属/大小），二次确认后执行
const delDlg = ref(false)
const delPlans = ref([])
const previewIds = ref([])
const delSummary = ref({ total_movies: 0, total_versions: 0, total_files: 0, total_bytes: 0 })
const delDone = ref(false)
const showAllDel = ref(false)
const visibleDelPlans = computed(() => showAllDel.value ? delPlans.value : delPlans.value.slice(0, 20))
function openDelDlg() {
  if (batching.value) return
  delDone.value = false
  showAllDel.value = false
  const ids = [...selectedIds.value]
  return batchRequest('/api/movies/batch-delete', { ids, dry_run: true }, '删除预览失败：', d => {
    previewIds.value = ids
    delPlans.value = d.plans || []
    delSummary.value = {
      total_movies: d.total_movies || 0, total_versions: d.total_versions || 0,
      total_files: d.total_files || 0, total_bytes: d.total_bytes || 0
    }
    delDlg.value = true
  })
}
function confirmDel() {
  if (!delDlg.value || !delPlans.value.length || delDone.value) return
  return batchRequest('/api/movies/batch-delete', { ids: previewIds.value, dry_run: false, confirm: true }, '删除失败：', async (d, revision) => {
    delDone.value = true
    batchMsg.value = `已删除 ${d.deleted_movies}/${d.total_movies} 部（${d.total_files} 个文件）`
    clearSelection()
    await refreshAfterBatch(revision)
  })
}
function joinCollection(cid) {
  return batchRequest(`/api/collections/${cid}/members`, { movie_ids: [...selectedIds.value] }, '加入失败：', async r => {
    batchMsg.value = r?.skipped ? `已加入合集（跳过 ${r.skipped} 部：不属于该媒体库）` : '已加入合集'
    clearSelection()
    await loadFacets()
  })
}
function createAndJoin() {
  const name = newCol.value.trim()
  if (!name) return
  const payload = { name, member_ids: [...selectedIds.value], media_library_id: curMediaId.value }
  return batchRequest('/api/collections', payload, '创建失败：', async d => {
    batchMsg.value = `已建合集「${d.name}」`
    newCol.value = ''
    clearSelection()
    await loadFacets()
  })
}
// 库页上传：多选文件 / 整个文件夹 → POST /api/uploads 逐个顺序上传。
// 文件夹模式透传 webkitRelativePath，后端原样还原结构；落盘即调 scan_one，
// 所以完成后只需刷新海报墙，未匹配的走设置页现有流程。
const upDlg = ref(false)
function historyKey() { return browseKey('/', currentMediaId(), buildParams()) }
onBeforeRouteLeave(() => {
  if (!firstLoaded.value) return
  saveBrowse(historyKey(), { path: '/', mediaId: currentMediaId(), query: route.query,
    items: items.value, hasMore: hasMore.value, filtersOpen: false,
    anchor: ai.open ? null : captureAnchor(), scrollTop: ai.open ? ai.wallTop : window.scrollY })
})
async function restoreWall() {
  const key = historyKey()
  const snapshot = readBrowse(key)
  if (snapshot) {
    items.value = snapshot.items
    loadedWallKey.value = key
    hasMore.value = snapshot.hasMore
    filtersOpen.value = false
  } else await load()
  firstLoaded.value = true
  await nextTick()
  await cwRef.value?.ready()
  if (disposed || route.path !== '/') return
  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))
  const restoredAi = await discovery.restore()
  featureReady.value = true
  await nextTick()
  if (!restoredAi && !ai.open) restoreBrowsePosition(snapshot)
  if (snapshot && !disposed) refreshSnapshot(snapshot)
}

// Restore immediately, then refresh the same number of pages so edits/deletions
// made in detail are visible without discarding the user's browsing position.
async function refreshSnapshot(snapshot) {
  const seq = ++loadSeq
  loading.value = true
  try {
    const fresh = []
    let more = false
    for (let offset = 0; offset < snapshot.items.length; offset += PAGE) {
      const d = await api('/api/search?' + buildParams(offset))
      if (seq !== loadSeq) return
      fresh.push(...(d.items || []))
      more = !!d.has_more
      if (!more || !d.items?.length) break
    }
    const position = { anchor: captureAnchor(), scrollTop: window.scrollY }
    items.value = fresh
    hasMore.value = more
    loadError.value = ''
    await nextTick()
    if (seq === loadSeq && !ai.open) restoreBrowsePosition(position)
  } catch (e) { if (seq === loadSeq) loadError.value = '刷新列表失败：' + e.message }
  finally { if (seq === loadSeq) loading.value = false }
}

let unsubLib = null
let readingRoute = false
onMounted(async () => {
  try { await loadLibs(api) } catch (e) { /* 后端不可用时按单库旧行为 */ }
  if (disposed) return
  readUrl()
  browseReady.value = true
  await loadFacets()
  if (disposed) return
  await restoreWall()
  if (disposed) return
  window.addEventListener('keydown', escExit)
  unsubLib = onLibChange(() => {
    if (readingRoute || route.path !== '/' || curMediaId.value === currentMediaId()) return
    curMediaId.value = currentMediaId()
    loadFacets()
    load()
  })
  // 无限滚动（评审 P1-11）：哨兵进入视口前 600px 自动加载下一页；按钮仍保留作兜底
  try {
    if (window.IntersectionObserver && loadSentinel.value) {
      loadIO = new IntersectionObserver((entries) => {
        if (entries.some(e => e.isIntersecting)) loadMore()
      }, { rootMargin: '600px 0px' })
      loadIO.observe(loadSentinel.value)
    }
  } catch (e) { /* 不支持则只用按钮 */ }
})
onUnmounted(() => {
  disposed = true
  loadSeq++
  facetsSeq++
  window.removeEventListener('keydown', escExit)
  clearTimeout(suggestTimer)
  if (unsubLib) { try { unsubLib() } catch (e) { /* 忽略 */ } unsubLib = null }
  if (loadIO) { try { loadIO.disconnect() } catch (e) { /* 忽略 */ } loadIO = null }
})
function escExit(e) {
  if (e.key === 'Escape' && !e.defaultPrevented && !filtersOpen.value && selecting.value && !suggestOpen.value && !tagDlg.value && !colDlg.value && !delDlg.value && !upDlg.value) {
    clearSelection()
  }
}
watch(() => route.query, () => {
  if (route.path !== '/') return
  readingRoute = true
  try { readUrl() } finally { readingRoute = false }
  // Facets describe the whole media library, so applying filters need not reload them.
  if (facetsScope !== currentMediaId()) loadFacets()
  load()
})
useAssistantPage('movie', () => {
  const blocked = !!playVid.value || playBusy.value !== null || selecting.value || upDlg.value
    || tagDlg.value || colDlg.value || delDlg.value || filtersOpen.value || scanOpen.value
    || suggestOpen.value || ai.open
  if (blocked || viewing.locallyLocked || viewing.suspended) return { blocked: true, tips: [] }
  if (!firstLoaded.value || loading.value || loadError.value) return { tips: [] }
  const tips = []
  if (!isChildren.value) tips.push({
    id: 'movie-discovery', manualOnly: !!items.value.length || !appliedQuery.value.trim(),
    text: '只记得电影里的一幕？打开智能搜索，描述你记得的情节就行。',
    actionLabel: '打开智能搜索', run: () => setAiOpen(true, true),
  })
  if (items.value.length || activeCount.value) tips.push({
    id: 'movie-filters', manualOnly: !!activeCount.value || !!appliedQuery.value
      || !watchedCounts.value.watched || !watchedCounts.value.unwatched,
    text: '想挑一部还没看过的电影？在全部筛选里可以一起选观看状态和类型。',
    actionLabel: '打开全部筛选', run: () => { filtersOpen.value = true },
  })
  return { tips }
})
</script>
<style scoped>

.card.sel { outline: 2px solid var(--jz-accent); }
.card.sel img { filter: brightness(.75); }
.sel-circle {
  position: absolute; top: 6px; left: 6px; z-index: 2;
  width: 26px; height: 26px; padding: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  border: 2px solid rgba(255,255,255,.85); background: var(--jz-overlay-soft); color: transparent;
  opacity: 0; transition: opacity .15s; cursor: pointer;
}
.poster-wrap:hover .sel-circle, .sel-circle:focus-visible, .sel-circle.on { opacity: 1; }
.sel-circle.on { background: var(--jz-accent); border-color: var(--jz-accent); color: var(--jz-on-accent); }
@media (hover: none) { .sel-circle { opacity: 1; } }
.floatbar {
  position: fixed; bottom: max(16px, env(safe-area-inset-bottom)); left: 50%; transform: translateX(-50%); z-index: 40;
  display: flex; gap: 4px; align-items: center;
  background: rgba(28,28,28,.96); border: 1px solid var(--jz-accent); border-radius: 999px;
  padding: 8px 14px; box-shadow: 0 8px 28px rgba(0,0,0,.6);
  max-width: calc(100vw - 24px);
}
.floatbar .count {
  min-width: 26px; min-height: 26px; border-radius: 999px; white-space: nowrap;
  background: var(--jz-accent); color: var(--jz-on-accent); font-weight: bold; font-size: 0.8125rem;
  display: flex; align-items: center; justify-content: center; padding: 0 10px;
}
.floatbar button {
  display: flex; gap: 5px; align-items: center; white-space: nowrap;
}
.floatbar button svg { width: 16px; height: 16px; flex-shrink: 0; }
.floatbar .fmsg { color: var(--jz-green); font-size: 0.75rem; overflow-wrap: anywhere; }
.batch-desktop-actions { display: flex; gap: 4px; align-items: center; }
.batch-mobile-actions { display: none; }
.browse-page.has-selection { padding-bottom: calc(96px + env(safe-area-inset-bottom)); }
.batch-dialog-fields { border: 0; margin: 0; padding: 0; min-width: 0; }
.batch-dialog-fields > label { display: block; margin: 16px 0 6px; font-size: var(--jz-font-m); }
.batch-dialog-fields .bar { display: flex; flex-wrap: wrap; gap: 8px; padding: 0; margin: 8px 0 16px; }
.batch-dialog-fields .bar > input { flex: 1; min-width: 0; }
.batch-tag-mode label { display: inline-flex; align-items: center; min-height: 44px; gap: 8px; padding-right: 16px; }
.batch-result { flex-basis: 100%; margin: 0; color: var(--jz-text-dim); overflow-wrap: anywhere; }
@media (max-width: 1000px) {
  .floatbar { left: 12px; right: 12px; transform: none; width: auto; max-width: none; border-radius: var(--jz-radius-m); padding: 8px; flex-wrap: wrap; }
  .floatbar .count { margin-right: auto; background: none; padding: 0 4px; font-size: var(--jz-font-m); }
  .batch-desktop-actions { display: none; }
  .batch-mobile-actions { display: flex; align-items: center; gap: 4px; }
  .batch-more :deep(.action-menu-items) { top: auto; bottom: calc(100% + 8px); max-height: calc(100dvh - 150px); overflow-y: auto; overscroll-behavior: contain; }
  .floatbar .fmsg { flex-basis: 100%; padding: 0 4px; }
  .collist li { flex-wrap: wrap; }
}

.unmatched-badge { position: absolute; bottom: 6px; right: 6px; font-size: 0.75rem; padding: 2px 8px;
  border-radius: 999px; background: rgba(224, 166, 60, .92); color: var(--jz-surface); font-weight: bold; }
.watched-badge { position: absolute; bottom: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: var(--jz-overlay); color: var(--jz-green); }

.warn-text { color: var(--jz-warn); }
.taglist { display: flex; gap: 6px; flex-wrap: wrap; padding: 0; }
.collist { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.collist li { display: flex; justify-content: space-between; gap: 8px; align-items: center; background: var(--jz-surface-3); border-radius: 8px; padding: 6px 10px; }
.del-warn { color: var(--jz-danger); font-size: 0.875rem; margin: 0 0 8px; }
.del-sum { color: var(--jz-warn); font-size: 0.9375rem; margin: 0 0 8px; font-weight: bold; }
@media (max-width: 700px), (pointer: coarse) {
  /* 热区保持 44px 触摸目标，视觉圆缩回 26px 居中（::before），避免占满小海报；
     盒子左上各外扩 3px，使视觉圆上沿/左沿回到 6px，与右上评分徽章对齐 */
  .sel-circle { width: var(--jz-touch-target); height: var(--jz-touch-target); top: -3px; left: -3px; background: transparent; border-color: transparent; }
  .sel-circle.on { background: transparent; border-color: transparent; }
  .sel-circle::before {
    content: ""; position: absolute; inset: 9px; border-radius: 50%;
    border: 2px solid rgba(255,255,255,.85); background: var(--jz-overlay-soft);
  }
  .sel-circle.on::before { background: var(--jz-accent); border-color: var(--jz-accent); }
  .sel-circle svg { width: 14px; height: 14px; position: relative; }
}
</style>
