<template>
  <section id="sec-restore" class="card-block">
    <h3>恢复到原始位置</h3>
    <p class="hint">整理/搬迁后偏离首次入库位置的影片可搬回原处。先预览再执行，目标被占用或源文件缺失会跳过上报、绝不覆盖。</p>
    <div class="bar">
      <button @click="load()" :disabled="!!busy">预览</button>
      <button @click="doRestore" :disabled="!!busy || !checkedRestore.length">{{ busy === 'restore' ? '恢复中…' : (armRestore ? `确认恢复 (${checkedRestore.length})` : '恢复选中') }}</button>
      <button v-if="restorePlans.length" @click="toggleAllRestore">{{ allRestoreChecked ? '全不选' : '全选' }}</button>
      <span>{{ restoreMsg }}</span>
      <button v-if="restorePlans.length > COLLAPSE_N" @click="showAllRestore = !showAllRestore">{{ showAllRestore ? '收起' : `展开全部 (${restorePlans.length})` }}</button>
    </div>
    <p v-if="armRestore" class="hint warn-text">再点一次执行恢复，无二次弹窗。目标被占用/源缺失的项会自动跳过。</p>
    <ul v-if="restorePlans.length" class="plan-list">
      <li v-for="p in visibleRestorePlans" :key="'r' + p.id" class="plan-row">
        <input type="checkbox" :value="p.id" v-model="checkedRestore" />
        <span class="conflict-title">{{ p.title || '(未命名)' }}<span v-if="p.year"> ({{ p.year }})</span></span>
        <span class="miss-path">{{ p.from }} → {{ p.to }}</span>
        <span v-if="p.status" :class="['plan-status', p.status === 'restored' ? 'ok' : 'fail']">{{ restoreStatusText(p.status) }}</span>
      </li>
    </ul>
  </section>
</template>
<script setup>
import { ref, computed } from 'vue'
import { api } from '../api.js'

const COLLAPSE_N = 20
const emit = defineEmits(['count', 'changed'])

const busy = ref(null)
// 恢复到原始位置（读 original_file_path，两段确认防误操作）
const restorePlans = ref([])
const checkedRestore = ref([])
const restoreMsg = ref('')
const armRestore = ref(false)
const showAllRestore = ref(false)
const visibleRestorePlans = computed(() => showAllRestore.value ? restorePlans.value : restorePlans.value.slice(0, COLLAPSE_N))
const allRestoreChecked = computed(() => restorePlans.value.length > 0 && checkedRestore.value.length === restorePlans.value.length)
function toggleAllRestore() {
  checkedRestore.value = allRestoreChecked.value ? [] : restorePlans.value.map(p => p.id)
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
  restoreMsg.value = ''
  armRestore.value = false
  if (!Array.isArray(preselect)) preselect = []
  try {
    const d = await api('/api/files/restore-candidates')
    // 预览接口字段是 file_path/original_file_path，统一映射成 from/to（含标题供展示）
    restorePlans.value = (Array.isArray(d.items) ? d.items : []).map(e => ({
      id: e.id, title: e.title || '', year: e.year || '',
      from: e.file_path || '', to: e.original_file_path || ''
    }))
    const ids = (preselect || []).map(Number).filter(Number.isFinite)
    checkedRestore.value = ids.length
      ? restorePlans.value.filter(p => ids.includes(p.id)).map(p => p.id)
      : restorePlans.value.map(p => p.id)
    if (!restorePlans.value.length) restoreMsg.value = '没有偏离原始位置的影片'
    else if (checkedRestore.value.length !== restorePlans.value.length) restoreMsg.value = `共 ${restorePlans.value.length} 项，已预选 ${checkedRestore.value.length} 项`
  } catch (e) {
    restoreMsg.value = '预览失败：' + e.message
  }
  emit('count', restorePlans.value.length)
}
async function doRestore() {
  if (!armRestore.value) {
    armRestore.value = true
    restoreMsg.value = `再点一次确认恢复 ${checkedRestore.value.length} 项`
    return
  }
  busy.value = 'restore'
  restoreMsg.value = ''
  try {
    const d = await api('/api/files/restore-original', {
      method: 'POST',
      body: JSON.stringify({ ids: checkedRestore.value, dry_run: false })
    })
    const ok = (d.results || []).filter(r => r.status === 'restored').length
    restoreMsg.value = `执行完毕：恢复 ${ok}/${d.results.length}`
    armRestore.value = false
    restorePlans.value = (d.results || []).map(r => ({ id: r.id, title: r.title, from: r.from, to: r.to, status: r.status }))
    checkedRestore.value = (d.results || []).filter(r => r.status !== 'restored').map(r => r.id)
    emit('count', restorePlans.value.length)
    emit('changed')
  } catch (e) {
    restoreMsg.value = '恢复失败：' + e.message
  } finally {
    busy.value = null
  }
}
defineExpose({ load })
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.plan-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.plan-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.plan-status { font-size: 0.75rem; }
.plan-status.ok { color: #7ed321; }
.plan-status.fail { color: #ff8a8a; }
.conflict-title { display: block; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
</style>
