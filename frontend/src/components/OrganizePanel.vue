<template>
  <div id="sec-organize" class="pipe-step">
    <div class="pipe-head pipe-toggle" @click="open = !open">
      <h4>③ 归档整理 <span v-if="orgPlans.length + orgConflicts.length" class="nav-badge">{{ orgPlans.length + orgConflicts.length }}</span></h4>
      <span class="fhint">{{ open ? '收起' : '展开' }}</span>
    </div>
    <div v-show="open" class="pipe-body">
      <p class="hint">已匹配确认的片在这里归档到正式库。就地归档：保留原父目录，只建“标题 (年份)/”子目录；搬到顶层：如 待整理 → 电影，按“电影/大区/标题 (年份)/文件”归类。命名均为“标题 (年份)[-版本][-规格][-分卷][-版本N].ext”，先预览再执行。</p>
      <div class="bar">
        <label><input type="radio" value="inplace" v-model="orgMode" /> 就地归档</label>
        <label><input type="radio" value="relocate" v-model="orgMode" /> 搬到顶层</label>
      </div>
      <div v-if="orgMode === 'relocate'" class="bar">
        <label>源 <input v-model="relocateFrom" placeholder="待整理" style="width:120px" /></label>
        <label>目标 <input v-model="relocateTo" placeholder="电影" style="width:120px" /></label>
        <label><input type="checkbox" v-model="groupByRegion" /> 按大区分二级目录</label>
      </div>
      <div class="bar">
        <button @click="loadOrgPreview" :disabled="!!busy">预览</button>
        <button @click="doOrganize" :disabled="!!busy || !orgPlans.length">{{ busy === 'organize' ? '执行中…' : '执行' }}</button>
        <span>{{ orgMsg }}</span>
        <button v-if="orgPlans.length > COLLAPSE_N" @click="showAllPlans = !showAllPlans">{{ showAllPlans ? '收起' : `展开全部 (${orgPlans.length})` }}</button>
      </div>
      <p class="hint">当前：{{ orgMode === 'inplace' ? '就地归档（全库）' : `搬到顶层（${relocateFrom || '待整理'} → ${relocateTo || '电影'}${groupByRegion ? '，按大区' : ''}，含目标下分区过期/未分区）` }} · 列表随参数自动刷新</p>
      <ul v-if="orgPlans.length" class="plan-list">
        <li v-for="p in visiblePlans" :key="p.id" class="plan-row">
          <span class="plan-from" :title="p.from">{{ p.from }}</span>
          <span class="plan-arrow">→</span>
          <span class="plan-to" :title="p.to">{{ p.to }}</span>
          <span v-if="p.numbered" class="plan-status warn">编号{{ p.numbered }}·可改备注</span>
          <span v-if="p.region_stale" class="plan-status warn">原分区过期</span>
          <span v-if="p.status" :class="['plan-status', p.status === 'moved' ? 'ok' : 'fail']">{{ p.status }}</span>
        </li>
      </ul>
      <p v-if="orgConflicts.length" class="hint warn-text">冲突 {{ orgConflicts.length }} 项：
        <span v-if="mismatchCount">疑似错配 {{ mismatchCount }}（需重匹配，不自动加后缀）</span>
        <span v-if="diskCount">磁盘占用 {{ diskCount }}</span>
        <span v-if="dbCount">库内占用 {{ dbCount }}</span>
        <button @click="loadOrgPreview" :disabled="!!busy">重新预览</button>
        <button v-if="conflictGroups.length > COLLAPSE_N" @click="showAllConflicts = !showAllConflicts">{{ showAllConflicts ? '收起' : '展开全部' }}</button>
      </p>
      <div v-if="orgConflicts.length" class="conflict-groups">
        <div v-for="g in visibleConflictGroups" :key="g.to" class="conflict-card">
          <div class="conflict-target">→ {{ g.to }}
            <span v-if="g.kind === 'suspect_mismatch'" class="kind-badge bad">疑似错配·请重匹配</span>
            <span v-else-if="g.kind === 'db'" class="kind-badge">库内占用</span>
            <span v-else class="kind-badge">磁盘占用</span>
          </div>
          <div v-for="p in g.items" :key="'c' + p.id" class="conflict-row">
            <div class="conflict-file" :title="(p.title || '') + ' ' + p.from">
              <span class="conflict-title">{{ p.title || '(未命名)' }}<span v-if="p.tmdb_id"> · TMDB {{ p.tmdb_id }}</span></span>
              <span class="miss-path">{{ p.from }}</span>
            </div>
            <div class="conflict-actions">
              <button @click="$router.push('/m/' + p.id)">去详情匹配</button>
            </div>
            <div class="conflict-note">
              <input v-model="noteEdits[p.id].edition" placeholder="版本" style="width:90px" />
              <input v-model="noteEdits[p.id].spec" placeholder="规格/备注" style="width:90px" />
              <button @click="saveNote(p.id)" :disabled="!!busy">改备注</button>
              <span>{{ noteMsg[p.id] }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { api } from '../api.js'

const COLLAPSE_N = 20
const emit = defineEmits(['changed'])

const busy = ref(null)
const open = ref(false)
// 归档整理（统一口 /api/files/organize）
const orgMode = ref('inplace')
const relocateFrom = ref('待整理')
const relocateTo = ref('电影')
const groupByRegion = ref(true)
const orgPlans = ref([])
const orgConflicts = ref([])
const orgMsg = ref('')
const showAllPlans = ref(false)
const showAllConflicts = ref(false)
const visiblePlans = computed(() => showAllPlans.value ? orgPlans.value : orgPlans.value.slice(0, COLLAPSE_N))
// 冲突按目标分组（一张卡放一起：保留方 + 冲突方）
const conflictGroups = computed(() => {
  const map = new Map()
  for (const p of orgConflicts.value) {
    if (!map.has(p.to)) map.set(p.to, { to: p.to, kind: p.kind || '', items: [] })
    map.get(p.to).items.push(p)
  }
  return [...map.values()]
})
const visibleConflictGroups = computed(() => showAllConflicts.value ? conflictGroups.value : conflictGroups.value.slice(0, COLLAPSE_N))
const mismatchCount = computed(() => orgConflicts.value.filter(p => p.kind === 'suspect_mismatch').length)
const diskCount = computed(() => orgConflicts.value.filter(p => p.status === 'conflict_disk_exists').length)
const dbCount = computed(() => orgConflicts.value.filter(p => p.status === 'conflict_db_occupied').length)
// 行内改备注（版本/规格）编辑态
const noteEdits = ref({})
const noteMsg = ref({})
function ensureNote(id) {
  if (!noteEdits.value[id]) noteEdits.value[id] = { edition: '', spec: '' }
  return noteEdits.value[id]
}
async function saveNote(id) {
  const n = ensureNote(id)
  noteMsg.value[id] = ''
  try {
    await api('/api/movies/' + id, {
      method: 'PATCH',
      body: JSON.stringify({ edition: (n.edition || '').trim(), spec: (n.spec || '').trim() })
    })
    noteMsg.value[id] = '已保存，请重新预览'
  } catch (e) {
    noteMsg.value[id] = '保存失败：' + e.message
  }
}

function orgBody(dry_run) {
  const b = { mode: orgMode.value, dry_run }
  if (orgMode.value === 'relocate') {
    b.from_prefix = relocateFrom.value.trim()
    b.to_dir = relocateTo.value.trim()
    b.group_by_region = !!groupByRegion.value
  }
  return JSON.stringify(b)
}

function syncNotes() {
  for (const p of orgConflicts.value) ensureNote(p.id)
}

async function loadOrgPreview() {
  orgMsg.value = ''
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: orgBody(true) })
    orgPlans.value = d.plans
    orgConflicts.value = d.conflicts || []
    syncNotes()
    if (!d.plans.length) orgMsg.value = orgConflicts.value.length ? `无可整理，冲突 ${orgConflicts.value.length} 项` : '没有需要整理的'
  } catch (e) {
    orgMsg.value = '预览失败：' + e.message
  }
}

// 整理状态文案（评审 R09-D5）：失败原因不再只给数字
const PLAN_STATUS_TEXT = {
  moved: '已移动', skipped_missing_src: '源文件缺失', conflict_disk_exists: '磁盘占用',
  conflict_db_occupied: '库内占用', conflict_needs_rematch: '需重新匹配',
  source_missing: '源文件缺失（预览提示）', restored: '已恢复', planned: '待执行',
  error: '错误', skipped: '已跳过',
}
function planStatusText(s) {
  const k = String(s || '')
  if (k.startsWith('error')) return '错误：' + k.slice(6).trim()
  return PLAN_STATUS_TEXT[k] || k
}
async function doOrganize() {
  busy.value = 'organize'
  orgMsg.value = ''
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: orgBody(false) })
    orgPlans.value = d.results
    orgConflicts.value = d.conflicts || []
    syncNotes()
    const byStatus = {}
    for (const r of d.results) byStatus[r.status] = (byStatus[r.status] || 0) + 1
    const parts = Object.entries(byStatus)
      .map(([k, v]) => `${planStatusText(k)} ${v}`)
    orgMsg.value = `执行完毕（${d.results.length} 项）：` + parts.join(' · ')
    emit('changed')
  } catch (e) {
    orgMsg.value = '执行失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 模式/参数一变自动重跑预览（防“列表与模式不符”），防抖 300ms，忙时跳过
let orgPreviewTimer = null
watch([orgMode, relocateFrom, relocateTo, groupByRegion], () => {
  if (orgPreviewTimer) clearTimeout(orgPreviewTimer)
  orgPreviewTimer = setTimeout(() => {
    if (!busy.value) loadOrgPreview()
  }, 300)
})
onUnmounted(() => {
  if (orgPreviewTimer) clearTimeout(orgPreviewTimer)
})

// 供父级触发（评审 R09-Q5）：进入「入库流程」/扫描完成后加载，ensure 只加载一次
const loaded = ref(false)
async function ensure(auto = false) {
  if (loaded.value) return
  loaded.value = true
  await loadOrgPreview()
  if (auto && orgPlans.value.length) {
    open.value = true
    orgMsg.value = orgMsg.value || `检测到 ${orgPlans.value.length} 项可归档，已为你展开`
  }
}
async function refresh(auto = false) {
  await loadOrgPreview()
  if (auto && orgPlans.value.length) {
    open.value = true
    orgMsg.value = `检测到 ${orgPlans.value.length} 项可归档，「③ 归档整理」已为你展开，点「执行」搬迁`
  }
}
defineExpose({ ensure, refresh })
</script>
<style scoped>
.pipe-step { margin: 12px 0 0; border-top: 1px dashed #3a3a3a; padding-top: 10px; }
.pipe-head { display: flex; gap: 8px; align-items: baseline; }
.pipe-head h4 { margin: 0; font-size: 0.9375rem; color: #ccc; }
.pipe-toggle { cursor: pointer; user-select: none; }
.pipe-toggle:hover h4 { color: #fff; }
.pipe-body { margin-top: 6px; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
.plan-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.plan-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.plan-from { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-arrow { color: #6ab0ff; }
.plan-to { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-status { font-size: 0.75rem; }
.plan-status.ok { color: #7ed321; }
.plan-status.fail { color: #ff8a8a; }
.plan-status.warn { color: #e0a63c; }
.conflict-groups { display: flex; flex-direction: column; gap: 8px; margin: 4px 0; }
.conflict-card { background: #262626; border: 1px solid #6e2b2b; border-radius: 8px; padding: 8px 10px; }
.conflict-target { font-size: 0.8125rem; color: #ccc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kind-badge { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; margin-left: 8px; color: #aaa; }
.kind-badge.bad { color: #ff8a8a; border-color: #6e2b2b; }
.conflict-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 6px 0; border-top: 1px dashed #3a3a3a; font-size: 0.8125rem; }
.conflict-file { flex: 1; min-width: 200px; }
.conflict-title { display: block; }
.conflict-actions { display: flex; gap: 6px; }
.conflict-note { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
</style>
