<template>
  <section id="sec-libtools" class="card-block">
    <h3>媒体库工具 <span class="fhint">按库操作：入库流程 / 高级维护 / 恢复 / 文件浏览</span></h3>

    <p v-if="!libraries.length" class="hint">还没有媒体库——请先在上方「媒体库」新建。</p>
    <template v-else>
      <div class="lib-tabs">
        <button v-for="l in libraries" :key="l.id"
          :class="{ on: l.id === selectedId, off: !l.enabled }"
          :title="tabTitle(l)" @click="select(l.id)">
          {{ l.name }}
          <span v-if="l.kind === 'tv'" class="tab-kind">剧集</span>
          <span v-if="!l.enabled" class="tab-kind">停用</span>
          <span v-if="badgeOf(l.id)" class="nav-badge">{{ badgeOf(l.id) }}</span>
        </button>
      </div>

      <template v-for="l in libraries" :key="'lt' + l.id">
        <div v-show="l.id === selectedId">
          <p v-if="!l.enabled" class="hint warn-text">该库已停用：扫描/写入会被跳过，这里仅作查看。</p>
          <p v-if="l.kind === 'tv'" class="hint">剧集库只做「扫描入清单」，不刮削、不归档；匹配确认/归档整理/恢复/元数据维护不适用。</p>

          <LibraryPipelinePanel :ref="el => setPipeRef(l.id, el)" :library="l"
            :active="l.id === selectedId" @changed="onChanged" @organized="onOrganized" />
          <LibraryMaintenancePanel v-if="l.kind !== 'tv'" :library="l"
            :active="l.id === selectedId" @changed="onChanged" />
          <RestorePanel v-if="l.kind !== 'tv'" :ref="el => setRestoreRef(l.id, el)" :library="l"
            :active="l.id === selectedId" @count="n => (restoreCount[l.id] = n)" @changed="onChanged" />
          <FsBrowser v-if="l.id === selectedId" :active="true" :library="l.id"
            @changed="onChanged" @scan="pipeRefs[l.id] && pipeRefs[l.id].startScan()" />
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

// 库标签页容器：每个库一套工具面板（入库/维护/恢复/文件浏览）。
// 面板按库常驻（v-show），首次选中才拉重清单（ensure），切库不重复加载。
// 文件浏览自加载，仅在选中库时挂载。
const props = defineProps({
  libraries: { type: Array, default: () => [] },
  currentLibId: { type: Number, default: null },
})
const emit = defineEmits(['changed'])

const route = useRoute()
const router = useRouter()

const selectedId = ref(null)
const loadedLibs = new Set()
const pipeRefs = ref({})
const restoreRefs = ref({})
const restoreCount = ref({})
const unmatchedAll = ref(null)
let badgeTimer = null

function setPipeRef(id, el) {
  if (el) pipeRefs.value[id] = el
}
function setRestoreRef(id, el) {
  if (el) restoreRefs.value[id] = el
}

// 待处理徽章：一次拉全库 unmatched，按 library_id 归组（未匹配+待确认+疑似英文+未归属花絮）
const pendingByLib = computed(() => {
  const out = {}
  const d = unmatchedAll.value
  if (!d) return out
  for (const key of ['unmatched', 'needs_review', 'suspect_title_high', 'orphan_extras']) {
    for (const it of d[key] || []) {
      const lid = it.library_id
      if (lid == null) continue
      out[lid] = (out[lid] || 0) + 1
    }
  }
  return out
})
function badgeOf(id) {
  return pendingByLib.value[id] || 0
}
function tabTitle(l) {
  const p = pendingByLib.value[l.id] || 0
  const r = restoreCount.value[l.id] || 0
  const parts = []
  if (p) parts.push(`待处理 ${p}`)
  if (r) parts.push(`待恢复 ${r}`)
  return parts.length ? parts.join(' · ') : l.name
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
    if (selectedId.value != null) q.library = String(selectedId.value)
    else delete q.library
    router.replace({ path: '/settings', query: q })
  } catch (e) { /* URL 同步失败不影响功能 */ }
}

async function select(id, opts = {}) {
  const lib = props.libraries.find(l => Number(l.id) === Number(id))
  if (!lib) return
  selectedId.value = lib.id
  syncUrl()
  const first = !loadedLibs.has(lib.id)
  if (first) {
    // 首次选中：等面板挂载后再拉重清单（refs 未就绪时不标记 loaded，下次重试）
    await nextTick()
    const pipe = pipeRefs.value[lib.id]
    if (pipe) {
      loadedLibs.add(lib.id)
      pipe.ensure()
      if (opts.restoreIds && opts.restoreIds.length) restoreRefs.value[lib.id]?.load(opts.restoreIds)
      else restoreRefs.value[lib.id]?.ensure()
    }
  } else if (opts.restoreIds && opts.restoreIds.length) {
    restoreRefs.value[lib.id]?.load(opts.restoreIds)
  }
  if (opts.scroll) {
    document.getElementById('sec-libtools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 深链承接：?sec=sec-restore&ids=… / ?sec=sec-pipeline / ?library=N
async function focus({ library, sec, ids } = {}) {
  // 库列表可能还在异步路上（父级 loadLibs 后 prop 才到）：等它就绪再选中
  for (let i = 0; i < 40 && !props.libraries.length; i++) {
    await new Promise(r => setTimeout(r, 50))
  }
  let id = library != null && props.libraries.some(l => Number(l.id) === Number(library))
    ? Number(library) : null
  if (id == null) id = selectedId.value
  if (id == null) {
    id = props.currentLibId != null
      ? Number(props.currentLibId) : (props.libraries[0] && props.libraries[0].id)
  }
  const restoreIds = ids && ids.length ? ids : []
  if (id != null) await select(id, { restoreIds })
  await nextTick()
  if (restoreIds.length) {
    // 详情页「去恢复」：预选该片并定位
    const el = document.getElementById('sec-restore')
    if (el) { el.scrollIntoView({ block: 'start' }); return }
  }
  if (sec) document.getElementById(sec)?.scrollIntoView({ block: 'start' })
}

function pickInitial() {
  const list = props.libraries
  if (!list.length) { selectedId.value = null; return }
  if (selectedId.value != null && list.some(l => l.id === selectedId.value)) return
  const qid = route.query.library != null && route.query.library !== ''
    ? Number(route.query.library) : null
  const target = (qid != null && list.find(l => l.id === qid)) ||
    (props.currentLibId != null && list.find(l => l.id === Number(props.currentLibId))) ||
    list.find(l => l.enabled !== false) || list[0]
  select(target.id)
}

// 库列表异步到达/删库时校正选中库
watch(() => props.libraries, pickInitial)

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
.lib-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 10px; }
.lib-tabs button { display: inline-flex; align-items: center; gap: 6px; }
.lib-tabs button.on { border-color: #e50914; color: #ff8a8a; }
.lib-tabs button.off { opacity: 0.6; }
.tab-kind { font-size: 0.6875rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 0 6px; }
.nav-badge { font-size: 0.75rem; color: #e0a63c; }
</style>
