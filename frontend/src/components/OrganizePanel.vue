<template>
  <div :id="active ? 'sec-organize' : undefined" class="pipe-step">
    <div v-if="!embedded" class="pipe-head pipe-toggle" @click="open = !open">
      <h4>③ 归档整理 <span v-if="orgPlans.length + orgConflicts.length" class="nav-badge">{{ orgPlans.length + orgConflicts.length }}</span></h4>
      <span class="fhint">{{ open ? '收起' : '展开' }}</span>
    </div>
    <div v-show="embedded || open" class="pipe-body">
      <HelpLink page="user-guide/organizing" label="整理前先看预览与撤销图解" />
      <details class="settings-details"><summary>两种整理方式有什么区别</summary><p class="hint">就地整理：规范影片目录与文件名，保留合集等父目录。移至库根目录：将影片集中到当前视频库根目录下。先核对预览，再执行移动或改名。</p></details>
      <div class="bar">
        <label><input type="radio" value="inplace" v-model="orgMode" /> 就地整理</label>
        <label><input type="radio" value="relocate" v-model="orgMode" /> 移至库根目录</label>
      </div>
      <div class="bar">
        <JzButton @click="loadOrgPreview" :disabled="!!busy" type="button" icon="refresh">刷新预览</JzButton>
        <JzButton @click="execOrganize(checkedPlanRows())" :disabled="!!busy || !execCount()" type="button" icon="organize">
          {{ busy === 'organize' ? '执行中…' : `整理选中 (${execCount()})` }}
        </JzButton>
        <span>{{ orgMsg }}</span>
      </div>
      <div v-for="g in planGroups" :key="'op' + g.key" class="lib-group">
        <div class="lib-group-head">
          <b>{{ groupTitle(g) }}</b>
          <span class="fhint">{{ g.items.length }} 项</span>
          <JzButton @click="togglePlans(g)" type="button">{{ groupAllChecked(g) ? '取消本组选中' : '选中本组' }}</JzButton>
          <JzButton @click="execOrganize(groupChecked(g))" :disabled="!!busy || !groupChecked(g).length" type="button" icon="organize">
            整理本组选中 ({{ groupChecked(g).length }})
          </JzButton>
        </div>
        <ul class="plan-list">
          <li v-for="p in seeMore(planExpand, g)" :key="p.id" class="plan-item"
              :class="{ skipped: rowViews[p.id] && rowViews[p.id].skipped }">
            <div class="plan-row">
              <input type="checkbox" :value="p.id" v-model="checkedPlans" />
              <span v-if="p.kind === 'dir'" class="kind-badge" :title="dirTip(p)">{{ planDetails[p.id] && planDetails[p.id].badge }}</span>
              <span class="plan-from" :title="p.from">{{ p.kind === 'dir' ? p.from + '/' : p.from }}</span>
              <AppIcon class="plan-arrow" name="arrow-right" :size="18" />
              <span class="plan-to" :title="rowViews[p.id] && rowViews[p.id].plan.to">{{ toText(p) }}</span>
              <span v-if="rowViews[p.id] && rowViews[p.id].forcedRelocate" class="kind-badge act">搬到顶层</span>
              <span v-if="rowViews[p.id] && rowViews[p.id].skipped" class="kind-badge act">保持不动</span>
              <select v-model="planAction[p.id]" class="act-sel" :disabled="!!busy" title="本行动作（不改的可选「保持不动」）">
                <option value="auto">跟随上方</option>
                <option value="relocate">强制搬到顶层</option>
                <option value="skip">保持不动</option>
              </select>
              <span v-if="p.numbered" class="plan-status warn">编号{{ p.numbered }}·可改备注</span>
              <span v-if="p.plex_warnings && p.plex_warnings.length" class="plan-status warn" :title="p.plex_warnings.join('；')">Plex 兼容性 {{ p.plex_warnings.length }}</span>
              <span v-if="p.status" :class="['plan-status', p.status === 'moved' ? 'ok' : 'fail']">{{ planStatusText(p.status) }}</span>
            </div>
            <div v-if="p.kind === 'dir' && planDetails[p.id]" class="plan-file-lines">
              <div v-for="(l, i) in planDetails[p.id].lines" :key="'f' + i" class="plan-file-line" :title="l.from + ' → ' + l.to">
                <span class="pf-from">{{ l.nameFrom }}</span>
                <AppIcon class="pf-arrow" name="arrow-right" :size="16" />
                <span class="pf-to">{{ l.nameTo }}</span>
              </div>
              <div v-if="planDetails[p.id].more" class="pf-more">还有 {{ planDetails[p.id].more }} 个影片文件同样改名</div>
              <div v-else-if="!planDetails[p.id].lines.length" class="pf-more">仅目录改名（文件名已规范）</div>
            </div>
          </li>
          <li v-if="g.items.length > COLLAPSE_N" class="plan-item collapse-row">
            <JzButton @click="toggleExpand(planExpand, g.key)" type="button">
              {{ expanded(planExpand, g.key) ? '收起' : `展开全部 (${g.items.length})` }}
            </JzButton>
          </li>
        </ul>
      </div>
      <p v-if="orgConflicts.length" class="hint warn-text">冲突 {{ orgConflicts.length }} 项：
        <span v-if="mismatchCount">疑似错配 {{ mismatchCount }}（需重匹配，不自动加后缀）</span>
        <span v-if="diskCount">磁盘占用 {{ diskCount }}</span>
        <span v-if="dbCount">库内占用 {{ dbCount }}</span>
        <JzButton @click="loadOrgPreview" :disabled="!!busy" type="button" icon="eye">重新预览</JzButton>
      </p>
      <div v-if="orgConflicts.length" class="conflict-groups">
        <div v-for="lg in conflictLibGroups" :key="'cl' + lg.key">
          <div class="lib-group-head"><b>{{ groupTitle(lg) }}</b><span class="fhint">{{ lg.items.length }} 项冲突</span></div>
          <div v-for="g in lg.targets" :key="lg.key + '|' + g.to" class="conflict-card">
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
                <JzButton @click="$router.push('/m/' + p.id)" type="button" icon="match">去详情匹配</JzButton>
              </div>
              <div class="conflict-note">
                <input v-model="noteEdits[p.id].edition" placeholder="版本" style="width:90px" />
                <input v-model="noteEdits[p.id].spec" placeholder="规格/备注" style="width:90px" />
                <JzButton @click="saveNote(p.id)" :disabled="!!busy" type="button" icon="edit">改备注</JzButton>
                <span>{{ noteMsg[p.id] }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'

import HelpLink from './HelpLink.vue'
import { ref, computed, reactive, watch, onUnmounted } from 'vue'
import { api } from '../api.js'
import { dirBadge, dirFileLines, projectPlan } from '../organizePlans.js'
import { groupByVideoLib, kindText } from '../libraryToolGroups.js'

const COLLAPSE_N = 20
const props = defineProps({
  embedded: { type: Boolean, default: false },
  library: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed', 'status'])

const busy = ref(null)
const open = ref(false)
const orgMode = ref('inplace')      // inplace=就地归档 | relocate=搬到顶层（本视频库根）
const orgPlans = ref([])
const orgConflicts = ref([])
const orgMsg = ref('')
const planExpand = reactive({})
const checkedPlans = ref([])
const planAction = ref({})

const scopeLibs = computed(() => [props.library])
const planGroups = computed(() => groupByVideoLib(orgPlans.value, scopeLibs.value)
  .map(g => ({ ...g, key: String(g.library_id) })))
const conflictLibGroups = computed(() => groupByVideoLib(orgConflicts.value, scopeLibs.value)
  .map(g => {
    const byTarget = new Map()
    for (const p of g.items) {
      if (!byTarget.has(p.to)) byTarget.set(p.to, { to: p.to, kind: p.kind || '', items: [] })
      byTarget.get(p.to).items.push(p)
    }
    return { ...g, key: String(g.library_id), targets: [...byTarget.values()] }
  }))
function groupTitle(g) {
  return g.lib ? `${g.lib.name} · ${kindText(g.lib.kind)}` : '未识别库'
}
function expanded(map, key) { return !!map[key] }
function toggleExpand(map, key) { map[key] = !map[key] }
function seeMore(map, g) {
  return expanded(map, g.key) ? g.items : g.items.slice(0, COLLAPSE_N)
}
function planRows() {
  return orgPlans.value.filter(p => checkedPlans.value.includes(p.id)
    && (planAction.value[p.id] || 'auto') !== 'skip')
}
function checkedPlanRows() { return planRows() }
function execCount() { return planRows().length }
function groupChecked(g) {
  const ids = new Set(g.items.map(p => p.id))
  return planRows().filter(p => ids.has(p.id))
}
function groupAllChecked(g) {
  const n = g.items.filter(p => checkedPlans.value.includes(p.id)).length
  return g.items.length > 0 && n === g.items.length
}
function togglePlans(g) {
  const ids = g.items.map(p => p.id)
  if (groupAllChecked(g)) checkedPlans.value = checkedPlans.value.filter(id => !ids.includes(id))
  else checkedPlans.value = [...new Set([...checkedPlans.value, ...ids])]
}
function syncActions() {
  const next = {}
  for (const p of orgPlans.value) next[p.id] = planAction.value[p.id] || 'auto'
  planAction.value = next
}
function dirTip(p) {
  const n = (p.files || []).length
  return `整目录改名：含 ${n} 个影片文件（多版本按规格改名）；Sample/封面/截图等子目录与文件原名跟随`
}
// 逐行动作即时投影：切换下拉时立即反映该行将要执行的路径（不改执行参数）
const rowViews = computed(() => {
  const out = {}
  for (const p of orgPlans.value) {
    out[p.id] = projectPlan(p, { action: planAction.value[p.id] || 'auto', orgMode: orgMode.value })
  }
  return out
})
function toText(p) {
  const view = rowViews.value[p.id]
  const to = view && view.plan ? view.plan.to : p.to
  return p.kind === 'dir' ? to + '/' : to
}
// 目录计划行内明细（徽标+影片改名）；读投影后的 plan，tooltip 路径与显示一致
const planDetails = computed(() => {
  const out = {}
  for (const p of orgPlans.value) {
    if (p.kind !== 'dir') continue
    const view = rowViews.value[p.id]
    const pp = view && view.plan ? view.plan : p
    out[p.id] = { badge: dirBadge(pp), ...dirFileLines(pp) }
  }
  return out
})
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

function syncNotes() {
  for (const p of orgConflicts.value) ensureNote(p.id)
}

async function loadOrgPreview() {
  orgMsg.value = ''
  try {
    const d = await api('/api/files/organize', {
      method: 'POST',
      body: JSON.stringify({ mode: orgMode.value, dry_run: true, library_id: props.library.id })
    })
    orgPlans.value = d.plans || []
    orgConflicts.value = d.conflicts || []
    checkedPlans.value = orgPlans.value.map(p => p.id)
    syncNotes()
    syncActions()
    if (!orgPlans.value.length) {
      orgMsg.value = orgConflicts.value.length ? `无可整理，冲突 ${orgConflicts.value.length} 项` : '没有需要整理的'
    }
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

async function execOrganize(chosen) {
  if (!chosen || !chosen.length) {
    orgMsg.value = '先勾选要执行的项'
    return
  }
  // 按动作分组；同片多版本（同 tmdb）整组执行，服务端按目录/分组重算才稳定
  const byMode = new Map()
  const addWithSiblings = (p, mode) => {
    if (!byMode.has(mode)) byMode.set(mode, new Set())
    const set = byMode.get(mode)
    set.add(p.id)
    if (p.tmdb_id) {
      for (const q of orgPlans.value) {
        if (q.tmdb_id === p.tmdb_id) set.add(q.id)
      }
    }
  }
  for (const p of chosen) {
    const act = planAction.value[p.id] || 'auto'
    if (act === 'skip') continue
    addWithSiblings(p, act === 'relocate' ? 'relocate' : orgMode.value)
  }
  if (!byMode.size) {
    orgMsg.value = '选中的项都是「保持不动」'
    return
  }
  busy.value = 'organize'
  orgMsg.value = ''
  try {
    const results = []
    let conflicts = []
    for (const [mode, ids] of byMode) {
      const body = { mode, dry_run: false, ids: [...ids], library_id: props.library.id }
      const d = await api('/api/files/organize', { method: 'POST', body: JSON.stringify(body) })
      results.push(...(d.results || []))
      conflicts = d.conflicts || conflicts
    }
    orgPlans.value = results
    orgConflicts.value = conflicts
    planAction.value = {}          // 结果行是最终路径，动作重置为 auto 防二次投影
    syncNotes()
    syncActions()
    checkedPlans.value = results.filter(r => r.status !== 'moved').map(r => r.id)
    const byStatus = {}
    for (const r of results) byStatus[r.status] = (byStatus[r.status] || 0) + 1
    const parts = Object.entries(byStatus).map(([k, v]) => `${planStatusText(k)} ${v}`)
    orgMsg.value = `执行完毕（${results.length} 项）：` + parts.join(' · ')
    emit('changed')
  } catch (e) {
    orgMsg.value = '执行失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 模式一变自动重跑预览（防“列表与模式不符”），防抖 300ms，忙时跳过
let orgPreviewTimer = null
watch([orgMode], () => {
  if (orgPreviewTimer) clearTimeout(orgPreviewTimer)
  orgPreviewTimer = setTimeout(() => {
    if (!busy.value) loadOrgPreview()
  }, 300)
})
onUnmounted(() => {
  if (orgPreviewTimer) clearTimeout(orgPreviewTimer)
})

// 供父级触发：进入「入库流程」/扫描完成后加载，ensure 只加载一次
const loaded = ref(false)
async function ensure(auto = false) {
  if (loaded.value) return
  loaded.value = true
  await loadOrgPreview()
  if (auto && orgPlans.value.length) {
    open.value = true
    orgMsg.value = orgMsg.value || `检测到 ${orgPlans.value.length} 项可归档，可在此核对`
  }
}
async function refresh(auto = false) {
  await loadOrgPreview()
  if (auto && orgPlans.value.length) {
    open.value = true
    orgMsg.value = `检测到 ${orgPlans.value.length} 项可归档，请在「目录整理」中核对后执行`
  } else if (auto) {
    open.value = false
  }
}
watch(() => orgPlans.value.length + orgConflicts.value.length, n => emit('status', n))

// 供父级步骤状态：可归档项数（计划 + 冲突）
function count() {
  return orgPlans.value.length + orgConflicts.value.length
}
defineExpose({ ensure, refresh, count })
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
.lib-group { margin: 8px 0; }
.lib-group-head { display: flex; gap: 8px; align-items: center; font-size: 0.8125rem; color: #ccc; margin-bottom: 4px; }
.plan-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.plan-item { background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.plan-item.skipped { opacity: 0.55; }
.plan-row { display: flex; gap: 8px; align-items: center; }
.kind-badge.act { color: #6ab0ff; border-color: #2c4a6e; }
.plan-file-lines { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; padding-left: 22px; }
.plan-file-line { display: flex; gap: 6px; align-items: baseline; color: #9a9a9a; font-size: 0.75rem; }
.pf-from { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.pf-arrow { color: #6ab0ff; }
.pf-to { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; color: #c9c9c9; }
.pf-more { color: #777; font-size: 0.75rem; }
.act-sel { background: #1c1c1c; color: #ddd; border: 1px solid #444; border-radius: 6px; padding: 2px 4px; font-size: 0.75rem; }
.plan-from { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-arrow { color: #6ab0ff; }
.plan-to { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-status { font-size: 0.75rem; }
.plan-status.ok { color: #7ed321; }
.plan-status.fail { color: #ff8a8a; }
.plan-status.warn { color: #e0a63c; }
.collapse-row { background: transparent; border: none; padding: 0; }
.conflict-groups { display: flex; flex-direction: column; gap: 8px; margin: 4px 0; }
.conflict-card { background: #262626; border: 1px solid #6e2b2b; border-radius: 8px; padding: 8px 10px; margin: 4px 0; }
.conflict-target { font-size: 0.8125rem; color: #ccc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kind-badge { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; margin-left: 0; color: #aaa; }
.kind-badge.bad { color: #ff8a8a; border-color: #6e2b2b; }
.conflict-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 6px 0; border-top: 1px dashed #3a3a3a; font-size: 0.8125rem; }
.conflict-file { flex: 1; min-width: 200px; }
.conflict-title { display: block; }
.conflict-actions { display: flex; gap: 6px; }
.conflict-note { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
</style>
