<template>
  <section id="sec-libtools" class="card-block">
    <h3>媒体库工具 <span class="fhint">按媒体库操作：扫描入库 / 待匹配确认 / 归档整理 / 高级维护 / 恢复 / 文件浏览（列表按视频库分表）</span></h3>

    <p v-if="!mediaLibs.length" class="hint">还没有媒体库——请先在上方「媒体库」新建。</p>
    <template v-else>
      <div class="lib-tabs">
        <button v-for="m in mediaLibs" :key="m.id"
          :class="{ on: Number(m.id) === Number(selectedId), off: m.enabled === false }"
          :title="tabTitle(m)" @click="select(m.id)">
          {{ m.name || '媒体库' }}
          <span v-if="m.source && m.source !== 'local'" class="tab-kind">{{ m.source === 'smb' ? 'SMB' : 'NFS' }}</span>
          <span v-if="m.enabled === false" class="tab-kind">停用</span>
          <span v-if="badgeOf(m.id)" class="nav-badge">{{ badgeOf(m.id) }}</span>
        </button>
      </div>

      <template v-for="m in mediaLibs" :key="'lt' + m.id">
        <div v-show="Number(m.id) === Number(selectedId)">
          <p v-if="m.enabled === false" class="hint warn-text">该媒体库已停用：扫描/写入会被跳过，这里仅作查看。</p>
          <p v-if="!(m.video_libraries || []).length" class="hint warn-text">该媒体库下还没有视频库：请先在上方「媒体库」添加视频库。</p>

          <div v-if="(m.video_libraries || []).length > 1" class="lib-filter">
            <span class="fhint">视频库筛选</span>
            <button :class="{ on: libFilter == null }" @click="setFilter(null)">全部（{{ m.video_libraries.length }}）</button>
            <button v-for="v in m.video_libraries" :key="v.id"
              :class="{ on: Number(libFilter) === Number(v.id), off: v.enabled === false }"
              @click="setFilter(v.id)">
              {{ v.name }}<span class="tab-kind">{{ kindText(v.kind) }}</span>
            </button>
          </div>

          <LibraryPipelinePanel :ref="el => setPanelRef(m.id, el)" :media="m" :lib-filter="libFilter"
            :active="Number(m.id) === Number(selectedId)" @changed="onChanged" @organized="onOrganized" />
          <LibraryMaintenancePanel v-if="(m.video_libraries || []).length" :media="m" :lib-filter="libFilter"
            :active="Number(m.id) === Number(selectedId)" @changed="onChanged" />
          <TvOrganizePanel v-if="tvLibsOf(m).length" :media="m"
            :active="Number(m.id) === Number(selectedId)" @changed="onChanged" />
          <RestorePanel v-if="hasMovieLibs(m)" :ref="el => setRestoreRef(m.id, el)" :media="m" :lib-filter="libFilter"
            :active="Number(m.id) === Number(selectedId)"
            @count="n => (restoreCount[m.id] = n)" @changed="onChanged" />
          <FsBrowser v-if="Number(m.id) === Number(selectedId)" :active="true" :media="m"
            :video-libs="m.video_libraries"
            @changed="onChanged" @scan="panelRefs[m.id] && panelRefs[m.id].startScan()" />
        </div>
      </template>
    </template>
  </section>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import FsBrowser from './FsBrowser.vue'
import LibraryMaintenancePanel from './LibraryMaintenancePanel.vue'
import LibraryPipelinePanel from './LibraryPipelinePanel.vue'
import RestorePanel from './RestorePanel.vue'
import TvOrganizePanel from './TvOrganizePanel.vue'
import { kindText, mediaPendingCount } from '../libraryToolGroups.js'

// 媒体库工具容器（2026-09 媒体库级重构）：顶层标签 = 媒体库；
// 视频库降级为分组维度（列表分表 + 顶部筛选）。面板按媒体库常驻（v-show），
// 首次选中才拉重清单（ensure），切库不重复加载；文件浏览仅选中库挂载。
const props = defineProps({
  mediaLibs: { type: Array, default: () => [] },
  currentMediaId: { type: Number, default: null },
})
const emit = defineEmits(['changed'])

const route = useRoute()
const router = useRouter()

const selectedId = ref(null)
const libFilter = ref(null)          // 视频库筛选（null=全部）
const loadedMedia = new Set()
const panelRefs = ref({})
const restoreRefs = ref({})
const restoreCount = ref({})
const unmatchedAll = ref(null)
let badgeTimer = null

function setPanelRef(id, el) {
  if (el) panelRefs.value[id] = el
}
function setRestoreRef(id, el) {
  if (el) restoreRefs.value[id] = el
}
function hasMovieLibs(m) {
  return (m.video_libraries || []).some(v => (v.kind || 'movie') !== 'tv')
}
function tvLibsOf(m) {
  return (m.video_libraries || []).filter(v => (v.kind || 'movie') === 'tv')
}
function mediaLibIds(m) {
  return (m.video_libraries || []).map(v => v.id)
}

// 待处理徽章：一次拉全库 unmatched，按媒体库归组（未匹配+待确认+疑似英文+未归属花絮）
const pendingByMedia = computed(() => {
  const out = {}
  for (const m of props.mediaLibs) {
    out[m.id] = mediaPendingCount(unmatchedAll.value, mediaLibIds(m))
  }
  return out
})
function badgeOf(id) {
  return pendingByMedia.value[id] || 0
}
function tabTitle(m) {
  const p = pendingByMedia.value[m.id] || 0
  const r = restoreCount.value[m.id] || 0
  const parts = []
  if (p) parts.push(`待处理 ${p}`)
  if (r) parts.push(`待恢复 ${r}`)
  return parts.length ? parts.join(' · ') : (m.name || '')
}

async function loadBadges() {
  try {
    unmatchedAll.value = await api('/api/files/unmatched')
  } catch (e) { /* 徽章失败不挡工具区 */ }
}
function scheduleBadges() {
  if (badgeTimer) clearTimeout(badgeTimer)
  badgeTimer = setTimeout(loadBadges, 800)
}

function syncUrl() {
  try {
    const q = { ...route.query }
    if (selectedId.value != null) q.media = String(selectedId.value)
    else delete q.media
    if (libFilter.value != null) q.library = String(libFilter.value)
    else delete q.library
    router.replace({ path: '/settings', query: q })
  } catch (e) { /* URL 同步失败不影响功能 */ }
}

async function select(id, opts = {}) {
  const m = props.mediaLibs.find(x => Number(x.id) === Number(id))
  if (!m) return
  selectedId.value = m.id
  if ('libFilter' in opts) libFilter.value = opts.libFilter
  else if (!opts.keepFilter) libFilter.value = null
  syncUrl()
  const first = !loadedMedia.has(m.id)
  if (first) {
    // 首次选中：等面板挂载后再拉重清单（refs 未就绪时不标记 loaded，下次重试）
    await nextTick()
    const pipe = panelRefs.value[m.id]
    if (pipe) {
      loadedMedia.add(m.id)
      pipe.ensure()
      if (opts.restoreIds && opts.restoreIds.length) restoreRefs.value[m.id]?.load(opts.restoreIds)
      else restoreRefs.value[m.id]?.ensure()
    }
  } else if (opts.restoreIds && opts.restoreIds.length) {
    restoreRefs.value[m.id]?.load(opts.restoreIds)
  }
  if (opts.scroll) {
    document.getElementById('sec-libtools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}
function setFilter(id) {
  libFilter.value = id
  syncUrl()
}

// 深链承接：?sec=sec-restore&ids=… / ?sec=sec-pipeline / ?media=N（旧 ?library=视频库 自动映射）
async function focus({ media, library, sec, ids } = {}) {
  // 媒体库列表可能还在异步路上（父级 loadLibs 后 prop 才到）：等它就绪再选中
  for (let i = 0; i < 40 && !props.mediaLibs.length; i++) {
    await new Promise(r => setTimeout(r, 50))
  }
  const libId = library != null && library !== '' ? Number(library) : null
  let mid = media != null && media !== '' && props.mediaLibs.some(m => Number(m.id) === Number(media))
    ? Number(media) : null
  if (mid == null && libId != null) {
    const hit = props.mediaLibs.find(m => (m.video_libraries || [])
      .some(v => Number(v.id) === libId))
    if (hit) mid = Number(hit.id)
  }
  if (mid == null) mid = selectedId.value != null ? Number(selectedId.value) : null
  if (mid == null && props.currentMediaId != null) mid = Number(props.currentMediaId)
  if (mid == null) {
    const fallback = props.mediaLibs.find(m => m.enabled !== false) || props.mediaLibs[0]
    mid = fallback ? Number(fallback.id) : null
  }
  // 视频库筛选只在该媒体库确有该视频库时生效（旧深链/跨库参数回退到全部）
  const target = props.mediaLibs.find(m => Number(m.id) === mid) || null
  const libInMedia = libId != null && target
    && (target.video_libraries || []).some(v => Number(v.id) === libId)
  const restoreIds = ids && ids.length ? ids : []
  if (mid != null) await select(mid, { libFilter: libInMedia ? libId : null, restoreIds })
  await nextTick()
  if (restoreIds.length) {
    // 详情页「去恢复」：预选该片并定位
    const el = document.getElementById('sec-restore')
    if (el) { el.scrollIntoView({ block: 'start' }); return }
  }
  if (sec) document.getElementById(sec)?.scrollIntoView({ block: 'start' })
}

function pickInitial() {
  const list = props.mediaLibs
  if (!list.length) { selectedId.value = null; return }
  if (selectedId.value != null && list.some(m => Number(m.id) === Number(selectedId.value))) return
  const q = route.query || {}
  const qm = q.media != null && q.media !== '' ? Number(q.media) : null
  const ql = q.library != null && q.library !== '' ? Number(q.library) : null
  let target = qm != null ? list.find(m => Number(m.id) === qm) : null
  if (!target && ql != null) {
    target = list.find(m => (m.video_libraries || []).some(v => Number(v.id) === ql)) || null
  }
  if (!target && props.currentMediaId != null) {
    target = list.find(m => Number(m.id) === Number(props.currentMediaId)) || null
  }
  if (!target) target = list.find(m => m.enabled !== false) || list[0]
  select(target.id, { libFilter: ql, keepFilter: true })
}

// 媒体库列表异步到达/删库时校正选中库
watch(() => props.mediaLibs, pickInitial)

function onChanged() {
  emit('changed')
  scheduleBadges()
}
function onOrganized() {
  onChanged()
  restoreRefs.value[selectedId.value]?.reloadIfLoaded()
}

onMounted(() => {
  pickInitial()
  // 徽章数据较重的全表只读扫描：延后拉，不挡首屏
  badgeTimer = setTimeout(loadBadges, 2500)
})
onUnmounted(() => {
  if (badgeTimer) clearTimeout(badgeTimer)
})

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
.lib-filter { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 0 0 8px; padding: 6px 8px; background: #191919; border: 1px solid #2e2e2e; border-radius: 8px; }
.lib-filter button { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8125rem; }
.lib-filter button.on { border-color: #e50914; color: #ff8a8a; }
.lib-filter button.off { opacity: 0.6; }
.tab-kind { font-size: 0.6875rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 0 6px; }
.nav-badge { font-size: 0.75rem; color: #e0a63c; }
</style>
