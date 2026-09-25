<template>
  <section id="sec-libtools" class="card-block">
    <h3>媒体库工具 <span class="fhint">每个视频库一个标签页：电影/剧集工具完全分开，按「扫描 → 处理 → 整理」单步引导</span></h3>

    <p v-if="!tabs.length" class="hint">还没有视频库——请先在上方「媒体库」新建（媒体库下可添加电影/剧集视频库）。</p>
    <template v-else>
      <div class="lib-tabs">
        <button v-for="t in tabs" :key="t.id"
          :class="{ on: t.id === selectedId, off: !t.enabled }"
          :title="tabTitle(t)" @click="select(t.id)">
          {{ t.label }}
          <span class="tab-kind">{{ t.kind_text }}</span>
          <span v-if="badgeOf(t.id)" class="nav-badge">{{ badgeOf(t.id) }}</span>
        </button>
      </div>

      <p v-if="selectedTab && !selectedTab.enabled" class="hint warn-text">该视频库已停用：扫描/写入会被跳过，这里仅作查看。</p>

      <template v-for="t in tabs" :key="'lt' + t.id">
        <div v-show="t.id === selectedId">
          <MovieLibraryTools v-if="t.kind !== 'tv'" :tab="t" :active="t.id === selectedId"
            :request="requests[t.id] || null"
            @changed="onChanged" @status="s => onStatus(t.id, s)" />
          <TvLibraryTools v-else :tab="t" :active="t.id === selectedId"
            :request="requests[t.id] || null"
            @changed="onChanged" @status="s => onStatus(t.id, s)" />
        </div>
      </template>
    </template>
  </section>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { buildMediaLibs, getStoredLibId, setStoredLibId } from '../libraries.js'
import { buildTabs, pickTab, resolveFocusTab } from '../libraryToolsTabs.js'
import MovieLibraryTools from './MovieLibraryTools.vue'
import TvLibraryTools from './TvLibraryTools.vue'

// 媒体库工具容器（视频库 Tab 版）：顶层标签 = 每个视频库；按 kind 挂
// MovieLibraryTools / TvLibraryTools，各自内部单步聚焦 + 更多工具折叠。
// 深链：?library=<视频库id> 直选；?media=<媒体库id> 按区块类型偏好选库。
const props = defineProps({
  libs: { type: Array, default: () => [] },
  currentMediaId: { type: Number, default: null },
})
const emit = defineEmits(['changed'])

const route = useRoute()
const router = useRouter()

const mediaLibs = computed(() => buildMediaLibs(props.libs))
const tabs = computed(() => buildTabs(mediaLibs.value))
const selectedId = ref(null)
const selectedTab = computed(() => tabs.value.find(t => t.id === selectedId.value) || null)
const requests = ref({})          // 深链请求：tab id → { sec, ids, nonce }
const statusByTab = ref({})       // 子组件状态上报：tab id → { pending, ... }

function onStatus(id, s) {
  statusByTab.value = { ...statusByTab.value, [id]: s || {} }
}
function badgeOf(id) {
  return Number((statusByTab.value[id] || {}).pending) || 0
}
function tabTitle(t) {
  const s = statusByTab.value[t.id] || {}
  const parts = []
  if (s.pending) parts.push(`待处理 ${s.pending}`)
  if (s.organize) parts.push(`可归档 ${s.organize}`)
  if (s.restore) parts.push(`待恢复 ${s.restore}`)
  if (s.shows) parts.push(`${s.shows} 部剧`)
  return parts.length ? parts.join(' · ') : t.label
}

function syncUrl() {
  try {
    const q = { ...route.query }
    const t = selectedTab.value
    if (t) {
      q.library = String(t.id)
      if (t.media_id != null) q.media = String(t.media_id)
      else delete q.media
    } else {
      delete q.library
      delete q.media
    }
    router.replace({ path: '/settings', query: q })
  } catch (e) { /* URL 同步失败不影响功能 */ }
}

function select(id, opts = {}) {
  const t = tabs.value.find(x => x.id === Number(id))
  if (!t) return
  selectedId.value = t.id
  setStoredLibId(t.id)
  syncUrl()
  if (opts.scroll) {
    document.getElementById('sec-libtools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function pickInitial() {
  const list = tabs.value
  if (!list.length) { selectedId.value = null; return }
  if (selectedId.value != null && list.some(t => t.id === selectedId.value)) return
  const q = route.query || {}
  const ql = q.library != null && q.library !== '' ? Number(q.library) : null
  const qm = q.media != null && q.media !== '' ? Number(q.media) : null
  const target = (ql != null ? list.find(t => t.id === ql) : null)
    || (qm != null ? list.find(t => t.media_id === qm) : null)
    || pickTab(list, { storedId: getStoredLibId(), currentMediaId: props.currentMediaId })
  if (target) select(target.id)
}
watch(tabs, pickInitial)

// 深链承接：?sec=…&media=…&library=…&ids=…
async function focus({ media, library, sec, ids } = {}) {
  // 库列表可能还在异步路上（父级 loadLibs 后 prop 才到）：等它就绪再选中
  for (let i = 0; i < 40 && !tabs.value.length; i++) {
    await new Promise(r => setTimeout(r, 50))
  }
  const target = resolveFocusTab(tabs.value, { media, library, sec })
  if (!target) {
    if (sec) document.getElementById(sec)?.scrollIntoView({ block: 'start' })
    return
  }
  select(target.id)
  await nextTick()
  const restoreIds = (Array.isArray(ids) ? ids : []).map(Number).filter(Number.isFinite)
  requests.value = {
    ...requests.value,
    [target.id]: { sec: sec || '', ids: restoreIds, nonce: Date.now() },
  }
  await nextTick()
  if (!sec && !restoreIds.length) {
    document.getElementById('sec-libtools')?.scrollIntoView({ block: 'start' })
  }
}

function onChanged() {
  emit('changed')
}

onMounted(pickInitial)
defineExpose({ select, focus })
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.lib-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
.lib-tabs button { display: inline-flex; align-items: center; gap: 6px; }
.lib-tabs button.on { border-color: #e50914; color: #ff8a8a; }
.lib-tabs button.off { opacity: 0.6; }
.tab-kind { font-size: 0.6875rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 0 6px; }
.nav-badge { font-size: 0.75rem; color: #e0a63c; }
</style>
