<template>
  <section :id="active ? 'sec-restore' : undefined" class="card-block">
    <h3>恢复到原始位置 <span class="fhint" v-if="library">作用于「{{ library.name }}」视频库</span></h3>
    <p class="hint">整理/搬迁后偏离首次入库位置的影片可搬回原处。勾选恢复；
      目标被占用或源文件缺失会跳过上报、绝不覆盖。</p>
    <div class="bar">
      <button @click="doRestore(checkedAll(), 'all')" :disabled="!!busy || !checkedRestore.length">
        {{ busy === 'restore' ? '恢复中…' : (armRestore === 'all' ? `确认恢复全部 (${checkedRestore.length})` : `恢复全部选中 (${checkedRestore.length})`) }}
      </button>
      <span>{{ restoreMsg || selHint }}</span>
    </div>
    <p v-if="armRestore === 'all'" class="hint warn-text">再点一次执行恢复，无二次弹窗。目标被占用/源缺失的项会自动跳过。</p>
    <div v-for="g in restoreGroups" :key="'rg' + g.key" class="lib-group">
      <div class="lib-group-head">
        <b>{{ groupTitle(g) }}</b>
        <span class="fhint">{{ g.items.length }} 项</span>
        <button @click="toggleRestore(g)">{{ groupAllChecked(g) ? '全不选本表' : '全选本表' }}</button>
        <button @click="doRestore(groupChecked(g), g.key)" :disabled="!!busy || !groupChecked(g).length">
          {{ armRestore === g.key ? `确认恢复本表 (${groupChecked(g).length})` : `恢复本表选中 (${groupChecked(g).length})` }}
        </button>
      </div>
      <ul class="plan-list">
        <li v-for="p in seeMore(g)" :key="'r' + p.id" class="plan-row">
          <input type="checkbox" :value="p.id" v-model="checkedRestore" />
          <span class="conflict-title">{{ p.title || '(未命名)' }}<span v-if="p.year"> ({{ p.year }})</span></span>
          <span class="miss-path" @mouseenter="showTip(p, $event)" @mouseleave="hideTip">{{ p.from }} → {{ p.to }}</span>
          <span v-if="p.status" :class="['plan-status', p.status === 'restored' ? 'ok' : 'fail']">{{ restoreStatusText(p.status) }}</span>
        </li>
        <li v-if="g.items.length > COLLAPSE_N" class="plan-row collapse-row">
          <button @click="toggleExpand(restoreExpand, g.key)">
            {{ expanded(restoreExpand, g.key) ? '收起' : `展开全部 (${g.items.length})` }}
          </button>
        </li>
      </ul>
    </div>
    <div v-if="tip.show" class="path-tip" :class="{ up: tip.up }"
         :style="{ left: tip.left + 'px', top: tip.top + 'px' }">
      <div class="tip-row"><span class="tip-k">当前</span>{{ tip.from }}</div>
      <div class="tip-row"><span class="tip-k">原始</span>{{ tip.to }}</div>
    </div>
  </section>
</template>
<script setup>
import { ref, computed, reactive, watch, onMounted, onUnmounted } from 'vue'
import { api } from '../api.js'
import { groupByVideoLib, kindText } from '../libraryToolGroups.js'

const COLLAPSE_N = 20
const props = defineProps({
  library: { type: Object, default: null },
  active: { type: Boolean, default: false },
  preselectIds: { type: Array, default: () => [] },
})
const emit = defineEmits(['count', 'changed'])

const busy = ref(null)
const loaded = ref(false)
let ensurePending = null
const restorePlans = ref([])
const checkedRestore = ref([])
const restoreMsg = ref('')
const armRestore = ref(null)       // null | 'all' | 视频库 key
const restoreExpand = reactive({})
const scopeLibs = computed(() => (props.library ? [props.library] : []))
const restoreGroups = computed(() => groupByVideoLib(restorePlans.value, scopeLibs.value)
  .map(g => ({ ...g, key: String(g.library_id) })))
function groupTitle(g) {
  return g.lib ? `${g.lib.name} · ${kindText(g.lib.kind)}` : '未识别库'
}
function expanded(map, key) { return !!map[key] }
function toggleExpand(map, key) { map[key] = !map[key] }
function seeMore(g) {
  return expanded(restoreExpand, g.key) ? g.items : g.items.slice(0, COLLAPSE_N)
}
// 勾选数实时提示（H-UI：此前只在预览时算一次，勾选后仍显示“已预选 0 个”）
const selHint = computed(() => {
  if (!loaded.value) return ''
  const total = restorePlans.value.length
  if (!total) return '没有偏离原始位置的影片'
  const n = checkedRestore.value.length
  return n === total ? `共 ${total} 项，已全选` : `共 ${total} 项，已勾选 ${n} 项`
})
function checkedAll() { return [...checkedRestore.value] }
function groupChecked(g) {
  const ids = new Set(g.items.map(p => p.id))
  return checkedRestore.value.filter(id => ids.has(id))
}
function groupAllChecked(g) {
  const ids = groupChecked(g)
  return g.items.length > 0 && ids.length === g.items.length
}
function toggleRestore(g) {
  const ids = g.items.map(p => p.id)
  if (groupAllChecked(g)) checkedRestore.value = checkedRestore.value.filter(id => !ids.includes(id))
  else checkedRestore.value = [...new Set([...checkedRestore.value, ...ids])]
}
const restoreStatusMap = {
  restored: '已恢复',
  planned: '待恢复',
  conflict_disk_exists: '目标被占跳过',
  conflict_db_occupied: '库内已占用跳过',
  skipped_missing_src: '源缺失跳过'
}
function restoreStatusText(s) {
  if (!s) return ''
  return restoreStatusMap[s] || (/^error/.test(s) ? '失败' : s)
}
async function load(preselect) {
  loaded.value = true
  restoreMsg.value = ''
  armRestore.value = null
  const ids = (Array.isArray(preselect) ? preselect : []).map(Number).filter(Number.isFinite)
  try {
    const q = props.library && props.library.id != null ? '?library=' + props.library.id : ''
    const d = await api('/api/files/restore-candidates' + q)
    // 预览接口字段是 file_path/original_file_path，统一映射成 from/to（含标题供展示）
    restorePlans.value = (Array.isArray(d.items) ? d.items : []).map(e => ({
      id: e.id, title: e.title || '', year: e.year || '',
      library_id: e.library_id,
      from: e.file_path || '', to: e.original_file_path || ''
    }))
    if (ids.length) {
      // 详情页“去恢复”带 ids：只勾选这些
      checkedRestore.value = restorePlans.value.filter(p => ids.includes(p.id)).map(p => p.id)
    } else {
      // 预览只刷新列表，不改变勾选（H-UI：此前等于全选）；仅保留仍存在的已勾选项
      const alive = new Set(restorePlans.value.map(p => p.id))
      checkedRestore.value = checkedRestore.value.filter(id => alive.has(id))
    }
  } catch (e) {
    restoreMsg.value = '加载失败：' + e.message
  }
  emit('count', restorePlans.value.length)
}
async function doRestore(ids, key = 'all') {
  const list = (ids || []).map(Number).filter(Number.isFinite)
  if (!list.length) {
    restoreMsg.value = '先勾选要恢复的项'
    return
  }
  if (armRestore.value !== key) {
    armRestore.value = key
    restoreMsg.value = `再点一次确认恢复 ${list.length} 项（目标被占/源缺失会自动跳过）`
    return
  }
  armRestore.value = null
  busy.value = 'restore'
  restoreMsg.value = ''
  try {
    const d = await api('/api/files/restore-original', {
      method: 'POST',
      body: JSON.stringify({
        ids: list,
        dry_run: false,
        ...(props.library && props.library.id != null ? { library_id: props.library.id } : {}),
      })
    })
    const doneIds = new Set(list)
    const others = restorePlans.value.filter(p => !doneIds.has(p.id))
    // 结果行是最终状态；失败项留在列表（勾选保留）
    const results = (d.results || []).map(r => ({
      id: r.id, title: r.title, year: r.year || '', library_id: r.library_id,
      from: r.from, to: r.to, status: r.status
    }))
    restorePlans.value = [...others, ...results]
    checkedRestore.value = results.filter(r => r.status !== 'restored').map(r => r.id)
    const ok = results.filter(r => r.status === 'restored').length
    restoreMsg.value = `执行完毕：恢复 ${ok}/${results.length}`
    emit('count', restorePlans.value.length)
    emit('changed')
  } catch (e) {
    restoreMsg.value = '恢复失败：' + e.message
  } finally {
    busy.value = null
  }
}
// 长路径悬浮弹层（H-UI）：行内文本截断，悬停显示完整“当前 → 原始”
const tip = ref({ show: false, up: false, left: 0, top: 0, from: '', to: '' })
function showTip(p, ev) {
  const r = ev.currentTarget.getBoundingClientRect()
  const width = Math.min(560, window.innerWidth - 24)
  const left = Math.max(8, Math.min(r.left, window.innerWidth - 24 - width))
  const up = r.bottom > window.innerHeight - 120
  tip.value = { show: true, up, left,
                top: up ? r.top - 6 : r.bottom + 6,
                from: p.from, to: p.to }
}
function hideTip() {
  if (tip.value.show) tip.value.show = false
}
onMounted(() => window.addEventListener('scroll', hideTip, true))
onUnmounted(() => window.removeEventListener('scroll', hideTip, true))

// 进入区块时自动加载一次（H-UI：原先靠「预览」按钮，按钮语义弱且只做首次加载）
async function ensure() {
  if (ensurePending) return ensurePending
  if (loaded.value) return
  ensurePending = load(props.preselectIds)
  try { await ensurePending } finally { ensurePending = null }
}
// 归档整理改变了路径后，若恢复清单已加载则静默刷新（保留勾选）
async function reloadIfLoaded() {
  if (loaded.value) await load()
}
// 挂载即激活（视频库 Tab 更多工具内）时自动加载；深链 ids 到达后重新预选
watch(() => props.active, (v) => { if (v) ensure() }, { immediate: true })
watch(() => props.preselectIds, (ids) => {
  if (loaded.value && (ids || []).length) load(ids)
})
defineExpose({ load, ensure, reloadIfLoaded, selectedIds: checkedAll })
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.lib-group { margin: 8px 0; }
.lib-group-head { display: flex; gap: 8px; align-items: center; font-size: 0.8125rem; color: #ccc; margin-bottom: 4px; }
.plan-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.plan-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.plan-status { font-size: 0.75rem; }
.plan-status.ok { color: #7ed321; }
.plan-status.fail { color: #ff8a8a; }
.conflict-title { display: block; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.collapse-row { background: transparent; border: none; padding: 0; }
.path-tip { position: fixed; z-index: 60; max-width: min(560px, calc(100vw - 24px)); background: #262626; border: 1px solid #555; border-radius: 8px; padding: 8px 10px; font-size: 0.8125rem; color: #ddd; box-shadow: 0 6px 24px rgba(0,0,0,.5); pointer-events: none; }
.path-tip.up { transform: translateY(-100%); }
.tip-row { line-height: 1.5; overflow-wrap: anywhere; }
.tip-row + .tip-row { margin-top: 4px; padding-top: 4px; border-top: 1px dashed #3a3a3a; }
.tip-k { color: #888; margin-right: 6px; }
</style>
