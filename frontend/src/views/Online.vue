<template>
  <div class="browse-page online-page">
    <header class="browse-heading online-heading"><div><h1>在线</h1><p>发现想看的电影和剧集</p></div><router-link v-if="!isChildren" class="manage-link" to="/settings?sec=sec-online">管理视频源</router-link></header>
    <EmptyState v-if="isChildren" state="empty" title="儿童模式暂不支持在线内容" text="切换观看模式后再浏览。" />
    <template v-else-if="safeMode">
      <p v-if="stateNotice" class="online-state-notice" data-online-state-notice role="status">{{ stateNotice }}</p>
      <div v-if="nextLoading" class="online-next-state" data-online-next-loading role="status"><span>正在读取下一集…</span><JzButton @click="cancelNext">取消连续播放</JzButton></div>
      <EmptyState v-if="loadingSources" state="loading" title="正在加载视频源" />
      <EmptyState v-else-if="sourcesError" state="error" title="视频源加载失败" :text="sourcesError" retry @retry="loadSources" />
      <EmptyState v-else-if="!enabled" state="empty" title="在线点播尚未启用" text="在设置中启用在线点播并添加视频源；已有记录仍会保留。"><router-link to="/settings?sec=sec-online">打开在线视频源设置</router-link></EmptyState>
      <EmptyState v-else-if="!sources.length" state="empty" title="还没有可用的视频源" text="从来源目录选择视频源，即可浏览和搜索；也支持添加自定义接口。"><router-link to="/settings?sec=sec-online">添加视频源</router-link></EmptyState>
      <template v-else>
        <form class="online-search" data-online-search role="search" aria-label="搜索在线视频" @submit.prevent="search(1)">
          <JzField id="online-source" label="视频源"><template #default="field"><select :id="field.id" v-model="sourceId" data-online-source :disabled="loadingSources" @change="sourceChanged"><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option></select></template></JzField>
          <JzField id="online-query" label="片名"><template #default="field"><input :id="field.id" v-model="query" maxlength="100" autocomplete="off" placeholder="输入电影或剧集名称" /></template></JzField>
          <JzButton type="submit" variant="primary" icon="search" :disabled="!canSearch || searching" :loading="searching">{{ !query.trim() ? '浏览目录' : '搜索' }}</JzButton>
        </form>
        <section v-if="categories.length" class="online-discovery-filters" aria-label="目录分类与年份">
          <nav class="online-channels" aria-label="内容频道">
            <JzButton :aria-pressed="!channelId && !loadedQuery" :disabled="searching" data-online-channel="" @click="selectChannel('')">发现</JzButton>
            <JzButton v-for="channel in mainChannels" :key="channel.id" :data-online-channel="channel.id" :data-online-category="!channel.children.length ? channel.id : undefined" :aria-pressed="channelId === channel.id && !loadedQuery" :disabled="searching" @click="selectChannel(channel.id)">{{ channel.label }}</JzButton>
            <JzButton ref="categoriesTrigger" variant="ghost" icon="filter" data-online-categories-open :aria-expanded="categoriesOpen" @click="openCategories"><span class="online-class-label-wide">全部分类</span><span class="online-class-label-narrow">选择分类</span></JzButton>
          </nav>
          <div v-if="selectedChannel?.children.length" class="online-subcategories" role="group" :aria-label="selectedChannel.label + '分类'">
            <JzButton :data-online-category="selectedChannel.id" :aria-pressed="categoryId === selectedChannel.id" :disabled="searching" @click="selectCategory(selectedChannel.id)">{{ selectedChannel.name }}综合条目</JzButton>
            <JzButton v-for="category in quickCategories" :key="category.id" :data-online-category="category.id" :aria-pressed="categoryId === category.id" :disabled="searching" @click="selectCategory(category.id)">{{ category.name }}</JzButton>
            <JzButton v-if="selectedChannel.children.length > quickCategories.length" variant="ghost" @click="openCategories">更多分类</JzButton>
          </div>
          <p v-if="categoryId" class="online-category-current" data-online-category-current>{{ selectedChannel?.label }}<span v-if="selectedCategoryName && selectedCategoryName !== selectedChannel?.label"> / {{ selectedCategoryName }}</span></p>
          <p v-if="selectedChannel?.children.length && categoryId === selectedChannel.id" class="online-category-hint">综合条目与细分类分别浏览，可选择下方的具体分类。</p>
        </section>
        <div v-if="canFilterYear" class="online-year-controls">
          <JzField id="online-year" label="年份"><template #default="field"><select :id="field.id" :value="yearChoice" :disabled="searching" data-online-year @change="chooseYear($event.target.value)"><option value="">全部年份</option><option v-for="value in recentYears" :key="value" :value="value">{{ value }} 年</option><option value="custom">其他年份…</option></select></template></JzField>
          <form v-if="yearChoice === 'custom'" class="online-custom-year" data-online-year-form @submit.prevent="applyCustomYear">
            <JzField id="online-year-custom" label="指定年份" :error="customYearError"><template #default="field"><input :id="field.id" v-model="customYear" inputmode="numeric" maxlength="4" autocomplete="off" placeholder="如 2018" :disabled="searching" :aria-invalid="field.invalid || undefined" :aria-describedby="field.describedby" data-online-year-custom @input="customYearError = ''" /></template></JzField>
            <JzButton type="submit" :disabled="searching">应用年份</JzButton>
          </form>
        </div>
        <JzDialog v-if="categoriesOpen" title="选择内容分类" size="large" @close="categoriesOpen = false">
          <div class="online-class-dialog" data-online-categories-dialog>
            <p class="online-scope">选择具体分类查看内容，综合条目与细分类分别浏览。</p>
            <JzField id="online-category-query" label="查找分类"><template #default="field"><input :id="field.id" v-model="categoryQuery" maxlength="80" autocomplete="off" placeholder="输入分类名称" data-online-category-query /></template></JzField>
            <JzButton :aria-pressed="!categoryId" :disabled="searching" @click="selectDialogCategory('')">发现 · 最近更新</JzButton>
            <EmptyState v-if="!dialogChannels.length" state="no-results" title="没有匹配的分类" text="换个分类名称试试。" />
            <section v-for="channel in dialogChannels" :key="channel.id" class="online-class-group" :aria-label="channel.name">
              <h3>{{ channel.name }}</h3>
              <div class="online-class-options" role="group" :aria-label="channel.name + '分类'">
                <JzButton v-if="channel.showRoot" :data-online-category="channel.id" :aria-pressed="categoryId === channel.id" :disabled="searching" @click="selectDialogCategory(channel.id)">{{ categoryTree.channels.find(value => value.id === channel.id)?.children.length ? channel.name + '综合条目' : channel.name }}</JzButton>
                <JzButton v-for="category in channel.children" :key="category.id" :data-online-category="category.id" :aria-pressed="categoryId === category.id" :disabled="searching" @click="selectDialogCategory(category.id)">{{ category.name }}</JzButton>
              </div>
            </section>
          </div>
        </JzDialog>
        <p class="online-discovery-note">{{ loadedQuery ? '搜索当前视频源' : '发现推荐 · 按视频源最近更新浏览' }}<span v-if="selectedSource?.provider === 'open_catalog'"> · Blender 开放影片</span></p>
        <details class="online-connection"><summary>连接状态</summary>
          <div class="online-health" data-online-health><span>目录：{{ healthLoading ? '读取中' : health ? onlineHealthLabel(health.purposes?.api) : healthError || '尚未使用' }}</span><span>播放：{{ healthLoading ? '读取中' : health ? onlineHealthLabel(health.purposes?.media) : healthError || '尚未使用' }}</span><JzButton variant="ghost" :disabled="healthLoading" @click="loadHealth">刷新状态</JzButton><small>来自最近请求的记录。卡顿时可在来源网络设置中尝试代理。</small></div>
        </details>
        <EmptyState v-if="detailLoading" state="loading" title="正在加载详情" text="正在读取所选来源的线路与分集。"><JzButton @click="backToResults">返回搜索结果</JzButton></EmptyState>
        <EmptyState v-else-if="detailError" state="error" title="详情加载失败" :text="detailError" retry @retry="openDetail(lastDetailId)"><JzButton @click="backToResults">返回搜索结果</JzButton></EmptyState>
        <section v-else-if="detail" class="online-detail" data-online-detail>
          <JzButton variant="ghost" icon="arrow-left" @click="backToResults">返回搜索结果</JzButton>
          <header class="online-detail-heading"><OnlineArtwork :path="detail.poster_path" :title="detail.item.title" class="online-artwork" /><div><p class="online-eyebrow">{{ detail.source.name }} · {{ detail.category || (detail.item.kind === 'tv' ? '剧集' : '电影') }}</p><h2>{{ detail.item.title }}</h2><p v-if="detail.year || detail.area || detail.language" class="online-year">{{ [detail.year, detail.area, detail.language].filter(Boolean).join(' · ') }}</p><p v-if="detail.remarks" class="online-detail-status" data-online-detail-status>{{ detail.remarks }}</p><ul v-if="detailGenres.length" class="online-genre-tags" aria-label="题材"><li v-for="genre in detailGenres" :key="genre">{{ genre }}</li></ul></div></header>
          <div class="online-detail-actions"><JzButton :data-online-favorite="detail.item.id" :icon="itemState?.favorite ? 'heart-filled' : 'heart'" :disabled="!itemState || mutationBusy.has(detail.item.id)" @click="favoriteDetail">{{ itemState?.favorite ? '取消收藏' : '收藏' }}</JzButton><JzButton v-if="resumeEntry" icon="play" data-online-detail-resume :disabled="starting" @click="play(resumeEntry)">继续播放</JzButton><span v-if="starting" role="status">正在读取续播位置…</span></div>
          <dl v-if="detailMetadata.length" class="online-detail-metadata" data-online-metadata><div v-for="row in detailMetadata" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl>
          <p v-if="detail.description" class="online-description">{{ detail.description }}</p>
          <div v-if="detail.attribution?.length" class="online-attribution"><span>内容来源与许可</span><a v-for="credit in detail.attribution" :key="credit.url" :href="safeAttribution(credit.url)" target="_blank" rel="noopener noreferrer">{{ credit.label }}</a></div>
          <div v-if="needsHostUpdate" class="online-host-help" data-online-host-help role="status">
            <p>此来源使用了尚未允许的媒体主机。请在来源设置中检查并确认新增域名，再返回这里重新加载。</p>
            <router-link :to="{ path: '/settings', query: { sec: 'sec-online', source: detail.source.id, item: lastDetailId } }" data-online-host-repair>检查媒体主机</router-link>
          </div>
          <h3>播放线路</h3>
          <div class="online-lines" role="group" aria-label="播放线路"><JzButton v-for="line in detail.lines" :key="line.id" :data-online-line="line.id" :aria-pressed="line.id === lineId" @click="selectLine(line.id)">{{ line.label }}</JzButton></div>
          <label v-if="detail.item.kind === 'tv'" class="online-autoplay"><input v-model="autoplay" type="checkbox" data-online-autoplay @change="autoplayChanged" />自动播放下一集（当前浏览器）</label>
          <p class="online-scope">{{ selectedLine?.label || '暂无线路' }}<span v-if="selectedLine"> · {{ selectedLine.entries.length }} {{ detail.item.kind === 'tv' ? '集' : '个播放版本' }}</span><span v-if="detail.item.kind === 'tv'">；连续播放仅使用当前来源和线路，不跳过片尾。</span></p>
          <div v-if="selectedLine" class="online-entries"><div v-for="(entry, index) in selectedLine.entries" :key="entry.identity?.episode_id || entry.label + ':' + index" class="online-entry"><JzButton :data-online-entry="entry.label" :disabled="!entry.supported || !entry.identity || starting" :variant="entry.supported ? 'primary' : 'secondary'" :icon="entry.supported ? 'play' : undefined" @click="play(entry)">{{ entry.label }}</JzButton><span v-if="!entry.supported">{{ unsupportedReason(entry.reason) }}</span><span v-else>{{ entry.transport?.toUpperCase() }} · 由当前设备播放</span></div></div>
          <EmptyState v-else state="empty" title="此来源暂无可播放线路" text="选择其他来源或稍后重新查询。" />
        </section>
        <div v-show="!detail && !detailLoading && !detailError" ref="browseRoot" class="online-browser" :aria-busy="searching" data-online-browser>
          <p v-if="searching && results.length" class="online-refresh-state" data-online-refreshing role="status">正在更新结果…</p>
          <div v-if="searchError && results.length" class="online-refresh-error" role="status"><span>{{ searchError }} 以下仍为上次结果。</span><JzButton @click="search(lastQuery.page, lastQuery.query, { force: true })">重试</JzButton></div>
          <EmptyState v-if="searching && !results.length" state="loading" title="正在搜索" :text="'正在查询 ' + selectedSource?.name" />
          <EmptyState v-else-if="searchError && !results.length" state="error" title="搜索失败" :text="searchError" retry @retry="search(lastQuery.page, lastQuery.query, { force: true })" />
          <EmptyState v-else-if="!searched" state="empty" :title="selectedSource?.provider === 'open_catalog' ? '浏览开放样例目录' : '查找想看的内容'" :text="selectedSource?.provider === 'open_catalog' ? '点击“浏览目录”查看开放影片及许可。' : '输入片名，仅向当前选中的来源发起查询。'" />
          <EmptyState v-else-if="!results.length" state="no-results" title="没有找到相关内容" text="试试其他片名，或选择其他来源。" />
          <template v-else>
            <div class="online-results-heading"><h2>{{ loadedQuery ? '搜索结果' : loadedCategoryName || '最近更新' }}<span v-if="loadedYear" class="online-results-year"> · {{ loadedYear }} 年</span></h2><span>{{ total }} 项 · {{ selectedSource?.name }}</span><JzButton variant="ghost" icon="refresh" data-online-refresh :disabled="searching" @click="refreshResults">刷新</JzButton></div>
            <div class="grid online-results"><article v-for="item in results" :key="item.external_id" class="card online-result" :data-online-browse-id="item.external_id">
              <JzButton class="online-poster-button" variant="ghost" :data-online-result="item.external_id" :aria-label="'查看详情 ' + item.title" :disabled="searching" @click="openDetail(item.external_id)"><OnlineArtwork :key="posterGeneration" :path="item.poster_path" :title="item.title" /><span v-if="item.remarks" class="online-poster-remark" :title="item.remarks" data-online-item-status>{{ item.remarks }}</span><span class="online-poster-action"><AppIcon name="play" :size="22" />查看详情</span></JzButton>
              <div class="t"><h3 class="card-title" :title="item.title">{{ item.title }}</h3><p class="card-meta">{{ [item.year, item.category || (item.kind === 'tv' ? '剧集' : item.kind === 'movie' ? '电影' : '')].filter(Boolean).join(' · ') }}</p></div>
            </article></div>
            <nav v-if="pageCount > 1" class="online-pagination" aria-label="在线搜索分页"><JzButton icon="arrow-left" :disabled="searching || !!searchError || page <= 1" @click="search(page - 1, loadedQuery)">上一页</JzButton><span>第 {{ page }} / {{ pageCount }} 页</span><JzButton icon="forward" :disabled="searching || !!searchError || page >= pageCount" @click="search(page + 1, loadedQuery)">下一页</JzButton></nav>
          </template>
        </div>
      </template>
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
    </template>
    <OnlinePlayerModal v-if="playing" :key="playing.key" :online-identity="playing.identity" :title="playing.title" :initial-position="playing.position" :prepare-session="playing.prepareSession" @close="playing.close" @progress="playing.progress" @ended="playing.ended" />
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createOnlineCatalogClient, createOnlineHealthClient, onlineHealthLabel, onlineCatalogError, onlineRouteTarget } from '../onlineCatalog.js'
import { createOnlineProgressMemory, onlineIdentityKey, validateOnlineIdentity } from '../onlinePlayback.js'
import { createOnlineStateClient, createOnlineProgressWriter, loadOnlineAutoplay, saveOnlineAutoplay, nextOnlineEpisode, onlineStateError } from '../onlineState.js'
import { isChildren, registerViewingPlaybackCleanup, viewing } from '../viewing.js'
import { onlineBrowseMemory, onlineBrowseScope, clearOnlineBrowse, captureOnlinePosition, restoreOnlinePosition } from '../onlineBrowse.js'
import { onlineCategoryTree, visibleOnlineChannels, onlineCategoryLabel, onlineChannelCategory, visibleOnlineCategories, filterOnlineChannels, validOnlineYear } from '../onlineCategories.js'
import { fmtTime as fmt } from '../playerLabels.js'
import EmptyState from '../components/EmptyState.vue'
import JzButton from '../components/JzButton.vue'
import JzField from '../components/JzField.vue'
import JzDialog from '../components/JzDialog.vue'
import AppIcon from '../components/AppIcon.vue'
import OnlinePlayerModal from '../components/OnlinePlayerModal.vue'
import OnlineArtwork from '../components/OnlineArtwork.vue'
import '../styles/online.css'

const client = createOnlineCatalogClient(), nextClient = createOnlineCatalogClient(), stateClient = createOnlineStateClient()
const route = useRoute(), router = useRouter()
const healthClient = createOnlineHealthClient(), health = ref(null), healthLoading = ref(false), healthError = ref('')
const progress = createOnlineProgressMemory()
const sources = ref([]), sourceId = ref(''), enabled = ref(false), query = ref('')
const loadingSources = ref(true), sourcesError = ref(''), searching = ref(false), searched = ref(false), searchError = ref('')
const categories = ref([]), categoryId = ref('')
const loadedCategoryId = ref(''), browseRoot = ref(null)
const channelId = ref(''), loadedChannelId = ref(''), channelSelections = ref({})
const year = ref(''), loadedYear = ref(''), yearChoice = ref(''), customYear = ref(''), customYearError = ref('')
const categoriesOpen = ref(false), categoriesTrigger = ref(null)
const categoryQuery = ref('')
const recentYears = Array.from({ length: 6 }, (_, index) => String(new Date().getFullYear() - index))
const posterGeneration = ref(0)
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
const canFilterYear = computed(() => selectedSource.value?.capabilities?.year_filter === true)
const categoryTree = computed(() => onlineCategoryTree(categories.value))
const mainChannels = computed(() => visibleOnlineChannels(categoryTree.value, channelId.value))
const selectedChannel = computed(() => categoryTree.value.channels.find(channel => channel.id === channelId.value))
const quickCategories = computed(() => visibleOnlineCategories(selectedChannel.value, categoryId.value))
const dialogChannels = computed(() => filterOnlineChannels(categoryTree.value, categoryQuery.value))
const selectedCategoryName = computed(() => onlineCategoryLabel(categoryTree.value, categoryId.value))
const loadedCategoryName = computed(() => onlineCategoryLabel(categoryTree.value, loadedCategoryId.value) || (loadedCategoryId.value ? '所选分类' : ''))
const canBrowse = computed(() => selectedSource.value?.capabilities?.browse || selectedSource.value?.provider === 'open_catalog')
const canSearch = computed(() => !!sourceId.value && (query.value.trim().length > 0 || canBrowse.value))
const selectedLine = computed(() => detail.value?.lines.find(line => line.id === lineId.value))
const needsHostUpdate = computed(() => detail.value?.lines.some(line => line.entries.some(entry => ['host_not_allowed', 'target_host_not_allowed'].includes(entry.reason))))
const lastQuery = ref({ page: 1, query: '' })
const resumeEntry = computed(() => !itemState.value?.last_progress?.completed ? findEntry(detail.value, itemState.value?.last_progress?.identity) : null)
const detailGenres = computed(() => textValues(detail.value?.genres).slice(0, 8))
const detailMetadata = computed(() => [
  { label: '导演', value: textValues(detail.value?.directors).join(' / ') },
  { label: '演员', value: textValues(detail.value?.actors).join(' / ') },
  { label: '别名', value: typeof detail.value?.subtitle === 'string' ? detail.value.subtitle : '' },
].filter(row => row.value))
let disposed = false, generation = 0, libraryGeneration = 0, healthGeneration = 0, playIntent = 0, nextIntent = 0, runId = 0, activeRun = null
let lastSourceId = ''
let cacheScope = '', loadedAt = 0, browsePosition = null, positionIntent = 0, suspendedWork = null
function current(turn) { return !disposed && turn === generation && safeMode.value }
function sameIdentity(a, b) { try { return onlineIdentityKey(a) === onlineIdentityKey(b) } catch { return false } }
function findEntry(data, identity) { return data?.lines?.flatMap(line => line.entries).find(entry => entry.supported && entry.identity && sameIdentity(entry.identity, identity)) || null }
function cancelNext() { nextIntent++; nextLoading.value = false; nextClient.cancel() }
function autoplayChanged() { saveOnlineAutoplay(autoplay.value, mode); if (!autoplay.value) cancelNext() }
function availabilityReason(reason) { return reason === 'online_source_disabled' || reason === 'online_disabled' ? '来源或在线功能已停用，记录仍会保留' : '当前内容暂不可播放，记录仍会保留' }
function textValues(values) { return Array.isArray(values) ? [...new Set(values.filter(value => typeof value === 'string' && value))].slice(0, 32) : [] }
function setYear(value) {
  year.value = canFilterYear.value && validOnlineYear(value) ? value : ''
  yearChoice.value = !year.value || recentYears.includes(year.value) ? year.value : 'custom'
  customYear.value = year.value
  customYearError.value = ''
}
function syncChannel() {
  channelId.value = categoryTree.value.rootByCategory.get(categoryId.value) || channelId.value
  if (!categoryId.value) channelId.value = ''
  if (channelId.value && categoryId.value) channelSelections.value = { ...channelSelections.value, [channelId.value]: categoryId.value }
}
function rememberBrowse(source = selectedSource.value, capture = true) {
  if (!safeMode.value || !searched.value || !cacheScope || !source || !loadedAt
    || cacheScope !== onlineBrowseScope(cacheScope.slice(0, 64), viewing)) return
  if (capture && !detail.value && !detailLoading.value && !detailError.value) browsePosition = captureOnlinePosition(browseRoot.value) || browsePosition
  onlineBrowseMemory.save({ scope: cacheScope, source, categoryId: loadedCategoryId.value, channelId: loadedChannelId.value,
    channelSelections: channelSelections.value, year: loadedYear.value, query: loadedQuery.value,
    inputQuery: query.value, loadedAt, position: browsePosition,
    response: { items: results.value, categories: categories.value, page: page.value, page_count: pageCount.value, total: total.value } })
}
function applyBrowse(snapshot) {
  categoryId.value = loadedCategoryId.value = snapshot.categoryId
  channelId.value = loadedChannelId.value = snapshot.channelId || ''
  channelSelections.value = { ...(snapshot.channelSelections || {}), ...channelSelections.value }
  setYear(snapshot.year || ''); loadedYear.value = year.value
  query.value = snapshot.inputQuery; loadedQuery.value = snapshot.query
  browsePosition = snapshot.position; loadedAt = snapshot.loadedAt
  if (!snapshot.response) return false
  results.value = snapshot.response.items || []; categories.value = snapshot.response.categories || []
  syncChannel(); loadedChannelId.value = channelId.value
  page.value = snapshot.response.page; pageCount.value = snapshot.response.page_count
  total.value = snapshot.response.total; searched.value = true
  return true
}
async function restoreBrowse(position = browsePosition, { top = false } = {}) {
  const ticket = ++positionIntent
  await nextTick()
  if (!disposed && ticket === positionIntent && safeMode.value && !detail.value && !detailLoading.value && !detailError.value) {
    if (top) browseRoot.value?.scrollIntoView?.({ block: 'start', behavior: 'instant' })
    else restoreOnlinePosition(position, browseRoot.value)
    rememberBrowse()
  }
}
function resetBrowse() {
  results.value = []; categories.value = []; categoryId.value = loadedCategoryId.value = ''; loadedQuery.value = ''
  channelId.value = loadedChannelId.value = ''; channelSelections.value = {}; categoriesOpen.value = false; categoryQuery.value = ''
  setYear(''); loadedYear.value = ''
  searched.value = false; searchError.value = ''; searching.value = false; page.value = pageCount.value = 1; total.value = 0
  loadedAt = 0; browsePosition = null; positionIntent++
}
async function loadSources() {
  const turn = ++generation
  const target = onlineRouteTarget(route.query)
  let restored = null
  loadingSources.value = true; sourcesError.value = ''
  try {
    const response = await client.sources()
    if (!current(turn) || !response) return
    enabled.value = response.enabled; sources.value = response.items || []
    cacheScope = onlineBrowseScope(response.scope_token, viewing)
    restored = response.enabled ? onlineBrowseMemory.last(cacheScope, sources.value, target?.sourceId || '') : null
    if (target && sources.value.some(source => source.id === target.sourceId)) sourceId.value = target.sourceId
    else if (!target && restored) sourceId.value = restored.sourceId
    if (!sources.value.some(source => source.id === sourceId.value)) sourceId.value = sources.value[0]?.id || ''
    if (!canFilterYear.value) setYear('')
    if (restored?.sourceId === sourceId.value) applyBrowse(restored)
    lastSourceId = sourceId.value
    void loadHealth()
  } catch (error) { if (current(turn)) sourcesError.value = onlineCatalogError(error) }
  finally {
    if (current(turn)) {
      loadingSources.value = false
      if (!sourcesError.value && enabled.value) {
        if (target?.externalId && sourceId.value === target.sourceId) void openDetail(target.externalId)
        else if (target?.externalId) stateNotice.value = '该来源已停用或不存在，请选择其他来源。'
        else if (restored?.response) void restoreBrowse()
        else if (restored) void search(restored.page, restored.query, { position: restored.position })
        else if (selectedSource.value?.capabilities?.browse) void search(1, '')
      }
    }
  }
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
function sourceChanged(discover = true) {
  if (discover && sourceId.value === lastSourceId) return
  rememberBrowse(sources.value.find(source => source.id === lastSourceId))
  lastSourceId = sourceId.value
  if (route.query.source || route.query.item) {
    const target = { ...route.query, source: sourceId.value }
    delete target.item
    void router.replace({ path: '/online', query: target })
  }
  healthGeneration++; healthClient.cancel(); health.value = null; void loadHealth()
  generation++; client.cancel(); void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false
  resetBrowse()
  const restored = discover ? onlineBrowseMemory.last(cacheScope, sources.value, sourceId.value) : null
  if (restored && applyBrowse(restored)) void restoreBrowse()
  else if (restored) void search(restored.page, restored.query, { position: restored.position })
  else if (discover && selectedSource.value?.capabilities?.browse) void search(1, '')
}
function selectCategory(id) {
  if (searching.value || id === categoryId.value && !loadedQuery.value && searched.value) return
  rememberBrowse()
  categoryId.value = id; syncChannel(); query.value = ''; void search(1, '')
}
function selectChannel(id) {
  if (searching.value) return
  if (!id) {
    rememberBrowse(); channelId.value = ''; categoryId.value = ''; setYear(''); query.value = ''; void search(1, '')
    return
  }
  const channel = categoryTree.value.channels.find(value => value.id === id)
  if (!channel) return
  selectCategory(onlineChannelCategory(channel, channelSelections.value[id]))
}
function selectDialogCategory(id) {
  if (searching.value) return
  categoriesOpen.value = false
  if (id) selectCategory(id)
  else selectChannel('')
}
function openCategories() { categoryQuery.value = ''; categoriesOpen.value = true }
function chooseYear(value) {
  customYearError.value = ''
  if (value === 'custom') { yearChoice.value = 'custom'; customYear.value = year.value; return }
  if (!canFilterYear.value || !validOnlineYear(value) || searching.value) return
  if (value === year.value && !loadedQuery.value) { setYear(value); return }
  rememberBrowse(); setYear(value); query.value = ''; void search(1, '')
}
function applyCustomYear() {
  if (!canFilterYear.value || searching.value) return
  if (!customYear.value || !validOnlineYear(customYear.value)) { customYearError.value = '请输入 1000 至 9999 的四位年份'; return }
  rememberBrowse(); setYear(customYear.value); query.value = ''; void search(1, '')
}
async function search(nextPage = 1, text = query.value, options = {}) {
  if (!sourceId.value || !safeMode.value || !text.trim() && !canBrowse.value) return
  rememberBrowse()
  if (text.trim()) { categoryId.value = ''; channelId.value = ''; setYear('') }
  const requestedCategory = categoryId.value
  const requestedYear = canFilterYear.value && !text.trim() ? year.value : ''
  const requestedChannel = channelId.value
  const changingView = searched.value && (nextPage !== page.value || requestedCategory !== loadedCategoryId.value || requestedYear !== loadedYear.value || text.trim() !== loadedQuery.value)
  const turn = ++generation
  positionIntent++
  client.cancel()
  void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false
  searching.value = true; searchError.value = ''; lastQuery.value = { page: nextPage, query: text,
    year: requestedYear, channelId: requestedChannel, force: !!options.force, position: options.position || (options.force ? browsePosition : null) }
  try {
    const cached = !options.force && onlineBrowseMemory.read(cacheScope, selectedSource.value,
      { page: nextPage, categoryId: requestedCategory, year: requestedYear, channelId: requestedChannel, query: text.trim() })
    if (cached?.response) {
      applyBrowse(cached)
      browsePosition = options.position || (changingView ? null : cached.position)
      await restoreBrowse(browsePosition, { top: changingView && !options.position })
      return
    }
    const browsing = !text.trim() && selectedSource.value?.capabilities?.browse
    const response = await (browsing ? client.browse(sourceId.value, nextPage, requestedCategory, requestedYear) : client.search(sourceId.value, text, nextPage))
    if (!current(turn) || !response) return
    results.value = response.items || []; page.value = response.page; pageCount.value = response.page_count
    if (browsing) categories.value = response.categories || []
    total.value = response.total; loadedQuery.value = text.trim(); loadedCategoryId.value = requestedCategory; searched.value = true; loadedAt = Date.now()
    syncChannel(); loadedChannelId.value = channelId.value; loadedYear.value = requestedYear
    if (options.force) posterGeneration.value++
    browsePosition = options.position || (options.force ? browsePosition : null)
    // New metadata must not capture an anchor from the previous Vue render.
    rememberBrowse(selectedSource.value, false)
    await restoreBrowse(browsePosition, { top: changingView && !options.position && !options.force })
  } catch (error) { if (current(turn)) searchError.value = onlineCatalogError(error) }
  finally { if (current(turn)) { searching.value = false; void loadHealth() } }
}
function refreshResults() {
  categoryId.value = loadedCategoryId.value
  channelId.value = loadedChannelId.value; setYear(loadedYear.value)
  void search(page.value, loadedQuery.value, { force: true })
}
async function readItemState(data, turn) {
  try {
    const response = await stateClient.item(data.item.id)
    if (current(turn) && detail.value?.item.id === data.item.id && response) itemState.value = response
  } catch (error) { if (current(turn)) stateNotice.value = onlineStateError(error) }
}
async function openDetail(externalId, options = {}) {
  rememberBrowse()
  positionIntent++
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
  sourceId.value = row.source.id; sourceChanged(false)
  await openDetail(row.item.external_id, { resume: resume && !!row.progress, identity: row.progress?.identity, itemId: row.item.id })
}
function backToResults() {
  generation++; client.cancel(); void closePlayer(); detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false
  if (route.query.item) void router.replace({ path: '/online', query: { source: sourceId.value } })
  if (!searched.value && canBrowse.value) void search(1, '')
  else if (loadedAt && Date.now() - loadedAt >= 60000) void search(page.value, loadedQuery.value, { force: true, position: browsePosition })
  else void restoreBrowse()
}
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
  clearOnlineBrowse(); cacheScope = ''; positionIntent++; suspendedWork = null
  generation++; libraryGeneration++; playIntent++; cancelNext(); client.cancel(); stateClient.cancel()
  healthGeneration++; healthClient.cancel(); health.value = null; healthError.value = ''; healthLoading.value = false
  const closing = stopRun(activeRun)
  detail.value = null; itemState.value = null; resetBrowse(); libraryRows.value = []; libraryScope.value = ''; libraryTotal.value = 0
  stateNotice.value = ''; searching.value = detailLoading.value = starting.value = false
  await closing
})
watch(() => [viewing.mode, viewing.generation, viewing.revision, viewing.locallyLocked], () => {
  clearOnlineBrowse(); cacheScope = ''; positionIntent++
  generation++; libraryGeneration++; client.cancel(); stateClient.cancel(); cancelNext()
  healthGeneration++; healthClient.cancel(); health.value = null; healthLoading.value = false; healthError.value = ''
  detail.value = null; itemState.value = null; detailError.value = ''; detailLoading.value = false; resetBrowse()
  libraryRows.value = []; libraryScope.value = ''; libraryTotal.value = 0
  void closePlayer()
  suspendedWork = { type: 'sources' }
  const turn = generation
  if (safeMode.value) void nextTick(() => { if (current(turn)) { suspendedWork = null; void loadSources(); void loadLibrary(0) } })
}, { flush: 'sync' })
watch(() => viewing.suspended, suspended => {
  positionIntent++
  if (suspended) {
    categoriesOpen.value = false; categoryQuery.value = ''
    // Verification temporarily hides content. It does not revoke an otherwise
    // unchanged session or discard the selected detail and active player.
    if (!detail.value && !detailLoading.value && !detailError.value) browsePosition = captureOnlinePosition(browseRoot.value) || browsePosition
    if (loadingSources.value) suspendedWork = { type: 'sources' }
    else if (detailLoading.value && !detail.value) suspendedWork = { type: 'detail', id: lastDetailId.value }
    else if (searching.value) suspendedWork = { type: 'search', page: lastQuery.value.page, query: lastQuery.value.query,
      category: categoryId.value, year: lastQuery.value.year, channelId: lastQuery.value.channelId,
      force: lastQuery.value.force, position: lastQuery.value.position }
    generation++; libraryGeneration++; client.cancel(); stateClient.cancel(); cancelNext()
    healthGeneration++; healthClient.cancel(); healthLoading.value = false
    searching.value = detailLoading.value = starting.value = libraryLoading.value = false
    return
  }
  if (!safeMode.value) return
  const work = suspendedWork
  suspendedWork = null
  if (work?.type === 'sources') void loadSources()
  else if (work?.type === 'detail') void openDetail(work.id)
  else if (work?.type === 'search') { categoryId.value = work.category; channelId.value = work.channelId || ''; setYear(work.year || ''); void search(work.page, work.query, { force: work.force, position: work.position }) }
  else {
    void loadHealth()
    if (detail.value) {
      const turn = generation
      // Refresh the current detail before the secondary history shelf. Both
      // remain cancellable if another verification or user intent intervenes.
      void readItemState(detail.value, turn).then(() => { if (current(turn)) void loadLibrary(0) })
      return
    }
    void restoreBrowse()
  }
  void loadLibrary(0)
}, { flush: 'sync' })
onMounted(() => {
  if (safeMode.value) { void loadSources(); void loadLibrary(0) }
  else loadingSources.value = libraryLoading.value = false
})
onBeforeUnmount(() => rememberBrowse())
onUnmounted(() => {
  disposed = true; generation++; libraryGeneration++; healthGeneration++; playIntent++; cancelNext(); client.close(); nextClient.close(); stateClient.close(); healthClient.close(); stopCleanup()
  void stopRun(activeRun)
})
</script>
