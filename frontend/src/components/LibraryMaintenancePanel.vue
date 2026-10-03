<template>
  <section :id="active ? 'sec-meta' : undefined" class="card-block library-maintenance">
    <h3>影片资料</h3>
    <p class="hint">以下操作仅作用于「{{ library.name }}」。</p>
    <div class="maintenance-group"><h4>更新影片资料</h4>

    <div class="bar">
      <JzButton class="tool-action" icon="refresh" @click="doBackfill" :disabled="!!busy" type="button">{{ busy === 'backfill' ? '补数据中…' : '补全产地与人物' }}</JzButton>
      <span>{{ backfillMsg }}</span>
    </div>

    <div class="bar">
      <JzButton class="tool-action" @click="doRefreshAll" :disabled="!!busy" type="button" icon="refresh">
        {{ busy === 'refresh' ? '刷新中…' : (armRefresh ? '确认刷新本库资料' : '更新本库影片资料') }}
      </JzButton>
      <JzButton v-if="armRefresh" @click="armRefresh = false" :disabled="!!busy" type="button">取消</JzButton>
      <span>{{ refreshMsg }}</span>
    </div>
    <p v-if="armRefresh" class="hint warn-text">将联网更新本库已匹配影片的资料（最多 5000 部），保留手工标题。再次点击确认执行。</p>

    </div>
    <div class="maintenance-group"><h4>重写媒体目录中的资料文件</h4><p class="hint">资料正确但 NFO 或海报文件缺失时使用。</p>
    <div class="bar">
      <label>修复内容 <select v-model="repairMode" :disabled="!!busy" @change="armMeta = false">
        <option value="both">NFO 与海报</option><option value="nfo">仅 NFO</option><option value="art">仅海报</option>
      </select></label>
      <JzButton icon="refresh" @click="doRebuildMeta" :disabled="!!busy" type="button">{{ busy === 'meta' || busy === 'nfo' ? '修复中…' : armMeta ? '确认修复' : '修复资料文件' }}</JzButton>
      <JzButton v-if="armMeta" @click="armMeta = false" type="button">取消</JzButton>
      <JzButton v-if="busy === 'meta'" @click="cancelMeta" type="button">取消任务</JzButton><span>{{ metaMsg }}</span>
    </div>
    <p v-if="armMeta" class="hint warn-text">将按当前匹配结果写入所选资料文件，保留手工标题。再次点击确认执行。</p>

    </div>
    <details class="settings-details tool-actions-wide"><summary>清理误入库记录与挂载残留</summary>
    <div class="bar">
      <JzButton class="tool-action" @click="doCleanBdmv" :disabled="!!busy" type="button" icon="delete">
        {{ busy === 'bdmv' ? '清理中…' : (armBdmv ? '确认移除蓝光碎片记录' : '移除蓝光碎片记录') }}
      </JzButton>
      <JzButton v-if="armBdmv" @click="armBdmv = false" :disabled="!!busy" type="button">取消</JzButton>
      <span>{{ bdmvMsg }}</span>
    </div>
    <p v-if="armBdmv" class="hint warn-text">删除原盘结构（BDMV/VIDEO_TS）里的碎片记录（只删库记录，不动物理文件）。再点一次执行。</p>

    <div class="bar">
      <JzButton class="tool-action" @click="doCleanSamples" :disabled="!!busy" type="button" icon="delete">
        {{ busy === 'samples' ? '清理中…' : (armSamples ? '确认移除样片与花絮误入库记录' : '移除样片与花絮误入库记录') }}
      </JzButton>
      <JzButton v-if="armSamples" @click="armSamples = false" :disabled="!!busy" type="button">取消</JzButton>
      <span>{{ samplesMsg }}</span>
    </div>
    <p v-if="armSamples" class="hint warn-text">删除路径属于 Sample/Screens/Behind The Scenes 等样片/花絮目录的影片记录（只删库记录，不动物理文件）。再点一次执行。</p>

    <div v-if="library.source !== 'local'" class="bar">
      <JzButton class="tool-action" @click="doCleanMount" :disabled="!!busy" type="button" icon="link">
        {{ busy === 'mount' ? '清理中…' : (armMount ? '确认清理挂载残留' : '清理挂载残留') }}
      </JzButton>
      <JzButton v-if="armMount" @click="armMount = false" :disabled="!!busy" type="button">取消</JzButton>
      <span>{{ mountMsg }}</span>
    </div>
    <p v-if="armMount" class="hint warn-text">清理挂载点目录里被历史误写的 NFO/图片（仅在未真正挂载时执行，绝不动 NAS）。再点一次执行。</p>
    </details>
    <PreviewMaintenance :library-id="library.id" :active="active" />
  </section>
</template>
<script setup>
import JzButton from './JzButton.vue'

import PreviewMaintenance from './PreviewMaintenance.vue'
import { ref } from 'vue'
import { api } from '../api.js'
import { usePolling } from '../usePolling.js'

const props = defineProps({
  library: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const busy = ref(null)
const backfillMsg = ref('')
const refreshMsg = ref('')
const repairMode = ref('both')
const metaMsg = ref('')
const bdmvMsg = ref('')
const mountMsg = ref('')

function libBody(extra = {}) {
  return JSON.stringify({ ...extra, library_id: props.library.id })
}

async function doBackfill() {
  busy.value = 'backfill'
  backfillMsg.value = ''
  try {
    const d = await api('/api/jobs/backfill-meta', { method: 'POST', body: libBody() })
    backfillMsg.value = `回填完成：${d.ok}/${d.total}，失败 ${d.failed.length}`
    emit('changed')
  } catch (e) {
    backfillMsg.value = '回填失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armRefresh = ref(false)
async function doRefreshAll() {
  if (!armRefresh.value) {
    armRefresh.value = true
    refreshMsg.value = '再点一次确认执行'
    return
  }
  armRefresh.value = false
  busy.value = 'refresh'
  refreshMsg.value = ''
  try {
    const d = await api('/api/jobs/tmdb-refresh', {
      method: 'POST', body: libBody({ limit: 5000 })
    })
    const changed = d.results.filter(r => r.changed).length
    refreshMsg.value = `完成：${d.total} 部中有变化 ${changed} 部，失败 ${d.failed.length}`
    emit('changed')
  } catch (e) {
    refreshMsg.value = '刷新失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doRebuildNfo() {
  busy.value = 'nfo'
  metaMsg.value = ''
  try {
    const d = await api('/api/jobs/rebuild-nfo', { method: 'POST', body: libBody() })
    metaMsg.value = `完成：重写 ${d.ok}/${d.total}，跳过缺失 ${d.skipped_missing}，失败 ${d.failed.length}`
    emit('changed')
  } catch (e) {
    metaMsg.value = '重建失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 重写 NFO 与海报：jobkit 后台任务（进度轮询，可取消）
const armMeta = ref(false)
const metaDone = ref(0)
const metaTotal = ref(0)
let metaJobId = ''
const metaPoll = usePolling(pollMetaJob, { interval: 1000 })
async function doRebuildMeta() {
  if (!armMeta.value) {
    armMeta.value = true
    metaMsg.value = '再点一次确认执行'
    return
  }
  armMeta.value = false
  if (repairMode.value === 'nfo') return doRebuildNfo()
  busy.value = 'meta'
  metaMsg.value = ''
  metaDone.value = 0
  metaTotal.value = 0
  try {
    const d = await api('/api/jobs/rebuild-meta', {
      method: 'POST', body: libBody({ dry_run: false, nfo: repairMode.value !== 'art', artwork: true, backdrops: false })
    })
    metaJobId = d.job_id || ''
    metaTotal.value = d.total || 0
    if (d.resumed) metaMsg.value = '已有重建任务在跑，跟踪进度…'
    if (!metaJobId) return finishMeta('没有可重建的影片（都需要 TMDB 匹配）')
    metaPoll.start()
  } catch (e) {
    metaMsg.value = '重建失败：' + e.message
    busy.value = null
  }
}
async function pollMetaJob() {
  if (!metaJobId) return
  try {
    const st = await api('/api/jobs/rebuild-meta/' + metaJobId)
    if (st.state === 'running') {
      metaDone.value = st.done || 0
      if (st.total) metaTotal.value = st.total
      return
    }
    if (st.state === 'done') {
      finishMeta(`完成：重写 ${st.done || 0}/${st.total || 0}`
        + ((st.failed || []).length ? `，失败 ${(st.failed || []).length}` : ''))
      emit('changed')
    } else if (st.state === 'cancelled') {
      finishMeta(`已取消（${st.done || 0}/${st.total || 0}）`)
    } else {
      finishMeta('重建失败：' + (st.error || '未知错误'))
    }
  } catch (e) { /* 轮询失败下次继续 */ }
}
function finishMeta(msg) {
  metaPoll.stop()
  metaJobId = ''
  metaMsg.value = msg
  busy.value = null
}
async function cancelMeta() {
  if (!metaJobId) return
  try { await api('/api/jobs/rebuild-meta/' + metaJobId + '/cancel', { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

const armBdmv = ref(false)
async function doCleanBdmv() {
  if (!armBdmv.value) {
    armBdmv.value = true
    bdmvMsg.value = '再点一次确认执行'
    return
  }
  armBdmv.value = false
  busy.value = 'bdmv'
  bdmvMsg.value = ''
  try {
    const d = await api('/api/jobs/clean-bdmv', { method: 'POST', body: libBody() })
    bdmvMsg.value = d.total ? `已删除 ${d.deleted}/${d.total} 条碎片记录` : '没有需要清理的记录'
    emit('changed')
  } catch (e) {
    bdmvMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armSamples = ref(false)
const samplesMsg = ref('')
async function doCleanSamples() {
  if (!armSamples.value) {
    armSamples.value = true
    samplesMsg.value = '再点一次确认执行'
    return
  }
  armSamples.value = false
  busy.value = 'samples'
  samplesMsg.value = ''
  try {
    const d = await api('/api/jobs/clean-samples', { method: 'POST', body: libBody() })
    samplesMsg.value = d.total ? `已删除 ${d.deleted}/${d.total} 条误入库记录` : '没有需要清理的记录'
    emit('changed')
  } catch (e) {
    samplesMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armMount = ref(false)
async function doCleanMount() {
  if (!armMount.value) {
    armMount.value = true
    mountMsg.value = '再点一次确认执行'
    return
  }
  armMount.value = false
  busy.value = 'mount'
  mountMsg.value = ''
  try {
    const d = await api('/api/jobs/clean-mount-artifacts', { method: 'POST', body: libBody() })
    mountMsg.value = d.total ? `已清理 ${d.removed}/${d.total} 个文件` : '没有挂载残留'
    emit('changed')
  } catch (e) {
    mountMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}
</script>
<style scoped>

.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: var(--jz-text); }
.hint { color: var(--jz-text-dim); font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: var(--jz-warn); }
.fhint { font-size: 0.75rem; color: var(--jz-text-dim); font-weight: normal; }
</style>
