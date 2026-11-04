<template>
  <div>
    <div v-if="searched && !progressive" class="online-update-state">
      <span v-if="hasUpdates" class="online-update-notice" data-online-update-notice role="status"><span>内容有更新</span><JzButton data-online-apply-update :disabled="searching || applyingUpdates" :loading="applyingUpdates" @click="$emit('apply-update')">查看更新</JzButton></span>
      <span v-if="checkingUpdates" class="online-loading-label" data-online-background-refreshing role="status"><Spinner :size="16" />正在检查更新…</span>
      <span v-else-if="updateCheckError" data-online-refresh-check-error :title="updateCheckError" role="status">检查更新未完成，仍显示上次结果。</span>
      <span v-if="checkedAt" data-online-checked-at :data-timestamp="checkedAt">上次检查 {{ checkedTime }}</span>
    </div>
    <div v-if="searchError && results.length" class="online-refresh-error" role="status"><span>{{ searchError }} {{ progressive ? '已找到的内容仍可查看。' : '以下仍为上次结果。' }}</span><JzButton @click="$emit('retry')">重试</JzButton></div>
    <OnlineSkeleton v-if="searching && !results.length && !progressive" :label="loadedQuery || query.trim() ? '正在搜索' : '正在加载内容'" />
    <EmptyState v-else-if="searchError && !results.length" state="error" :title="progressive ? '搜索未完成' : '搜索失败'" :text="searchError" retry @retry="$emit('retry')" />
    <EmptyState v-else-if="!searched" state="empty" :title="selectedSource?.provider === 'open_catalog' ? '浏览开放样例目录' : '查找想看的内容'" :text="selectedSource?.provider === 'open_catalog' ? '点击“浏览目录”查看开放影片及许可。' : '输入片名，仅向当前选中的来源发起查询。'" />
    <template v-else>
      <div class="online-results-heading">
        <h2>{{ loadedQuery ? '搜索结果' : loadedCategoryName || '最近更新' }}<span v-if="loadedQuery && loadedCategoryName" class="online-results-year"> · {{ loadedCategoryName }}</span><span v-if="loadedYear" class="online-results-year"> · {{ loadedYear }} 年</span></h2>
        <span v-if="!progressive">{{ total }} 项 · {{ selectedSource?.name }}</span>
        <span v-else data-online-search-progress role="status">{{ progressive.status === 'complete' ? `${progressive.total} 项` : `已找到 ${progressive.found} 项，尚未检索完` }} · {{ selectedSource?.name }}<span v-if="progressive.status !== 'complete'"> · 已检查 {{ progressive.scanned }} 条</span></span>
        <JzButton variant="ghost" icon="refresh" data-online-refresh :loading="searching && !progressive" @click="$emit('refresh')">刷新</JzButton>
      </div>
      <p v-if="progressive && loadedQuery" class="online-search-term" data-online-search-term>搜索：{{ loadedQuery }}</p>
      <EmptyState v-if="!results.length && progressive && progressive.status !== 'complete'" :state="searching ? 'loading' : 'empty'" :title="searching ? '正在查找匹配内容' : '尚未找到匹配内容'" text="还有搜索结果尚未检索，可以继续查找或调整筛选条件。" />
      <EmptyState v-else-if="!results.length" state="no-results" title="没有找到相关内容" text="试试其他片名、清除筛选，或选择其他来源。" />
      <div v-else class="grid online-results"><article v-for="item in results" :key="item.external_id" class="card online-result" :data-online-browse-id="item.external_id">
        <JzButton class="online-poster-button" variant="ghost" :data-online-result="item.external_id" :aria-label="'查看详情 ' + item.title" :disabled="searching && !progressive" @click="$emit('detail', item.external_id)"><OnlineArtwork :retry-key="posterGeneration" :path="item.poster_path" :title="item.title" /><span v-if="item.remarks" class="online-poster-remark" :title="item.remarks" data-online-item-status>{{ item.remarks }}</span><span class="online-poster-action"><AppIcon name="play" :size="22" />查看详情</span></JzButton>
        <div class="t"><h3 class="card-title" :title="item.title">{{ item.title }}</h3><p class="card-meta">{{ [item.year, item.category || (item.kind === 'tv' ? '剧集' : item.kind === 'movie' ? '电影' : '')].filter(Boolean).join(' · ') }}</p></div>
      </article></div>
      <nav v-if="!progressive && pageCount > 1" class="online-pagination" aria-label="在线搜索分页"><JzButton icon="arrow-left" data-online-page-prev :loading="searching && lastQuery.page < page" :disabled="searching || !!searchError || page <= 1" @click="$emit('page', page - 1)">上一页</JzButton><span>第 {{ page }} / {{ pageCount }} 页</span><JzButton icon="forward" data-online-page-next :loading="searching && lastQuery.page > page" :disabled="searching || !!searchError || page >= pageCount" @click="$emit('page', page + 1)">下一页</JzButton></nav>
    </template>
    <div v-if="progressive && progressive.has_more" class="online-pagination">
      <JzButton data-online-search-more :loading="searching" @click="$emit('more')">{{ results.length ? '加载更多' : '继续检索' }}</JzButton>
    </div>
  </div>
</template>

<script setup>
import JzButton from './JzButton.vue'
import EmptyState from './EmptyState.vue'
import OnlineSkeleton from './OnlineSkeleton.vue'
import OnlineArtwork from './OnlineArtwork.vue'
import AppIcon from './AppIcon.vue'
import Spinner from './Spinner.vue'
defineProps(['searched', 'hasUpdates', 'searching', 'applyingUpdates', 'checkingUpdates', 'updateCheckError', 'checkedAt', 'checkedTime', 'searchError', 'results', 'loadedQuery', 'query', 'selectedSource', 'loadedCategoryName', 'loadedYear', 'total', 'posterGeneration', 'pageCount', 'lastQuery', 'page', 'progressive'])
defineEmits(['apply-update', 'retry', 'refresh', 'detail', 'page', 'more'])
</script>
