<template>
  <section v-if="active && mode !== 'off' && (items.length || error || loading)" class="tv-updates" aria-label="最近播出 · 尚未收藏">
    <div class="updates-head">
      <h3>最近播出<span v-if="items.length" class="updates-count">{{ items.length }} 部尚未收藏新集</span></h3>
      <JzButton v-if="items.length" size="compact" variant="ghost" :aria-expanded="expanded" aria-controls="tv-updates-list" @click="expanded = !expanded">{{ expanded ? '收起' : '查看更新' }}</JzButton>
    </div>
    <p v-if="loading && !items.length" class="updates-note" role="status">正在读取剧集更新…</p>
    <p v-if="error" class="updates-note" role="status">{{ error }} <JzButton size="compact" variant="ghost" @click="reload" :disabled="loading">重试</JzButton></p>
    <div v-if="expanded && items.length" id="tv-updates-list" class="updates-list">
      <router-link v-for="item in visibleItems" :key="item.tmdb_id" :ref="el => rememberCard(item.tmdb_id, el)"
        :to="'/tv/' + item.show_id" class="update-card">
        <img v-if="item.poster_path" :src="posterUrl(item.poster_path)" alt="" loading="lazy" />
        <ArtworkPlaceholder v-else kind="poster" :label="item.title" class="update-poster" />
        <span class="update-copy"><strong>{{ item.title }}<span v-if="item.year" class="updates-year"> ({{ item.year }})</span></strong>
          <span>{{ tvUpdateEpisodeLabel(item.events[0]) }}{{ item.events.length > 1 ? ' 等 ' + item.events.length + ' 集' : '' }} · 已播未收藏</span>
          <span>{{ item.events[0].air_date }}<span v-if="item.events[0].title"> · {{ item.events[0].title }}</span></span>
        </span>
      </router-link>
    </div>
    <JzButton v-if="expanded && items.length > 6" size="compact" variant="ghost" class="updates-more" :aria-expanded="showAll" @click="showAll = !showAll">{{ showAll ? '仅显示前 6 部' : '展开其余 ' + (items.length - 6) + ' 部' }}</JzButton>
  </section>
</template>
<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import JzButton from './JzButton.vue'
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'
import { api, posterUrl } from '../api.js'
import { airingErrorText } from '../tvCollection.js'
import { loadTvUpdatesHistory, saveTvUpdatesHistory, unseenTvUpdates, shouldExpandTvUpdates,
  markTvUpdatesSeen, restoreTvUpdatesBatch, tvUpdateEpisodeLabel, TV_UPDATES_WEEK } from '../tvUpdates.js'

const props = defineProps({ mediaLibraryId: { type: Number, default: null }, active: Boolean,
  mode: { type: String, default: 'weekly' }, initialSnapshot: { type: Object, default: null } })
const items = ref([])
const expanded = ref(false)
const showAll = ref(false)
const loading = ref(false)
const error = ref('')
const visibleItems = computed(() => showAll.value ? items.value : items.value.slice(0, 6))
const elements = new Map()
let generation = 0, observation = 0, controller, observer, pending = Promise.resolve(), loaded = false, intervalClaimed = false
let history = { lastShown: 0, seen: {} }
let batchAt = 0

function rememberCard(id, component) {
  const el = component?.$el || component
  if (el) elements.set(Number(id), el)
  else elements.delete(Number(id))
}
function stopObserving() { observation++; observer?.disconnect(); observer = null }
function recordVisible(ids) {
  if (!props.active || props.mode === 'off' || !expanded.value ||
      (typeof document !== 'undefined' && document.visibilityState === 'hidden')) return
  const visible = visibleItems.value.filter(item => ids.includes(Number(item.tmdb_id)))
  if (!visible.length) return
  const updated = markTvUpdatesSeen(history, visible, Date.now(), !intervalClaimed)
  if (updated.lastShown !== history.lastShown) intervalClaimed = true
  history = saveTvUpdatesHistory(localStorage, props.mediaLibraryId, updated)
}
async function observeCards() {
  stopObserving()
  const seq = generation
  const observing = observation
  await nextTick()
  if (seq !== generation || observing !== observation || !props.active || !expanded.value || props.mode === 'off') return
  if (typeof IntersectionObserver === 'undefined') {
    // Older browsers only acknowledge cards actually inside the viewport.
    const ids = [...elements].filter(([, el]) => {
      const rect = el.getBoundingClientRect?.()
      return rect && rect.top < window.innerHeight && rect.bottom > 0 && rect.left < window.innerWidth && rect.right > 0
    }).map(([id]) => id)
    recordVisible(ids)
    return
  }
  observer = new IntersectionObserver(entries => {
    if (seq !== generation || observing !== observation) return
    recordVisible(entries.filter(entry => entry.isIntersecting && entry.intersectionRatio >= 0.5)
      .flatMap(entry => [...elements].filter(([, el]) => el === entry.target).map(([id]) => id)))
  }, { threshold: 0.5 })
  for (const el of elements.values()) observer.observe(el)
}
function snapshot() {
  return { mediaId: props.mediaLibraryId, items: items.value, expanded: expanded.value,
    showAll: showAll.value, intervalClaimed, batchAt }
}
function reload() {
  controller?.abort()
  const seq = ++generation
  stopObserving()
  if (!props.active || props.mode === 'off' || !props.mediaLibraryId) return Promise.resolve()
  controller = new AbortController()
  const saved = loaded ? snapshot() : props.initialSnapshot
  const restoring = saved?.items?.length && Number(saved.mediaId) === Number(props.mediaLibraryId) &&
    (!saved.batchAt || Date.now() - saved.batchAt < TV_UPDATES_WEEK)
  history = loadTvUpdatesHistory(localStorage, props.mediaLibraryId)
  if (restoring) {
    items.value = saved.items || []
    expanded.value = !!saved.expanded
    showAll.value = !!saved.showAll
    intervalClaimed = !!saved.intervalClaimed
    batchAt = saved.batchAt || Date.now()
  } else {
    showAll.value = false
    intervalClaimed = false
    batchAt = Date.now()
  }
  loading.value = true
  error.value = ''
  pending = (async () => {
    try {
      const response = await api('/api/tv/updates?media_library=' + props.mediaLibraryId, { signal: controller.signal })
      if (seq !== generation) return
      items.value = restoreTvUpdatesBatch(restoring ? saved : null, response.items, props.mediaLibraryId)
        ?? unseenTvUpdates(response.items, history)
      if (!restoring) expanded.value = shouldExpandTvUpdates(items.value, history, props.mode)
      if (response.error) error.value = '播出资料暂未更新：' + airingErrorText(response.error)
      loaded = true
    } catch (e) {
      if (seq === generation) error.value = '暂时无法读取剧集更新：' + airingErrorText(e.message)
    } finally {
      if (seq === generation) { loading.value = false; await observeCards() }
    }
  })()
  return pending
}
watch(() => props.mediaLibraryId, () => {
  controller?.abort(); generation++; stopObserving()
  items.value = []; elements.clear(); expanded.value = false; showAll.value = false
  error.value = ''; loading.value = false; loaded = false; intervalClaimed = false
  reload()
}, { immediate: true })
watch(() => [props.active, props.mode], () => {
  if (!props.active || props.mode === 'off') {
    controller?.abort(); generation++; stopObserving(); loading.value = false
  } else if (!loaded) reload()
  else observeCards()
})
watch([expanded, showAll, items], observeCards)
onMounted(() => { if (typeof document !== 'undefined') document.addEventListener('visibilitychange', observeCards) })
onUnmounted(() => {
  generation++; controller?.abort(); stopObserving()
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', observeCards)
})
defineExpose({ ready: () => pending, snapshot, reload })
</script>
<style scoped>
.tv-updates { margin-top: var(--jz-gap-xl); }
.updates-head { display: flex; align-items: center; justify-content: space-between; gap: var(--jz-gap-m); }
.updates-head h3 { margin: 0; min-width: 0; font-size: 1rem; font-weight: 600; }
.updates-head > button { flex: none; }
.updates-count { margin-left: var(--jz-gap-s); font-size: var(--jz-font-s); color: var(--jz-text-dim); font-weight: 400; }
.updates-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--jz-gap-s); margin-top: var(--jz-gap-s); }
.update-card { display: flex; gap: var(--jz-gap-s); min-width: 0; padding: var(--jz-gap-s); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); background: var(--jz-surface); color: var(--jz-text); text-decoration: none; }
.update-card:hover { background: var(--jz-surface-2); border-color: var(--jz-border-strong); }
.update-card > img, .update-poster { width: 48px; height: 72px; flex: 0 0 48px; object-fit: cover; border-radius: var(--jz-radius-s); }
.update-copy { display: flex; flex-direction: column; justify-content: center; gap: 3px; min-width: 0; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.update-copy strong { color: var(--jz-text); font-size: var(--jz-font-m); font-weight: 500; }
.update-copy > span, .update-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.updates-year, .updates-note { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.updates-note { margin: var(--jz-gap-s) 0 0; overflow-wrap: anywhere; }
.updates-more { margin-top: var(--jz-gap-xs); }
@media (max-width: 1000px) { .updates-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) {
  .tv-updates { margin-top: var(--jz-gap-m); }
  .updates-list { display: flex; overflow-x: auto; scroll-snap-type: x proximity; padding: 3px; margin-inline: -3px; }
  .update-card { flex: 0 0 260px; box-sizing: border-box; scroll-snap-align: start; }
  .updates-count { display: block; margin: 2px 0 0; }
}
</style>
