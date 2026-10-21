<template>
  <section class="preview-maintenance" aria-label="播放进度缩略图">
    <h4>播放进度缩略图</h4>
    <p class="hint">为电影、剧集和花絮生成进度预览，已有新版预览自动跳过，旧版预览会更新。远程库建议空闲时执行。</p>
    <p v-if="auto && auto.mode !== 'off'" class="hint">新入库的{{ auto.mode === 'local' ? '本地' : '所有' }}视频会在服务器{{ autoWindow }}、无人播放时自动生成；全服务队列共 {{ auto.queued }} 项。</p>
    <p v-else-if="auto?.mode === 'off'" class="hint">服务器已关闭自动生成；仍可在此批量生成。</p>
    <p v-if="auto?.queued && auto.pause_reason === 'data_limit'" class="hint" role="status">应用数据已达到设置的总预算，闲时生成暂时暂停。可到“系统维护”重新分配容量或清理缓存。</p>
    <p v-else-if="auto?.queued && auto.pause_reason === 'preview_limit'" class="hint" role="status">进度缩略图缓存接近上限，闲时生成暂时暂停。可在“系统维护”调整容量或清理旧缩略图。</p>
    <p v-else-if="auto?.queued && auto.pause_reason === 'usage_unavailable'" class="hint" role="status">暂时无法统计应用数据占用，自动生成已暂停。可到“系统维护”重试统计。</p>
    <div class="bar">
      <JzButton class="tool-action" @click="start" :disabled="starting || !!mutating" :loading="starting" type="button" icon="image">{{ starting ? '正在加入…' : '立即批量生成' }}</JzButton>
      <JzButton v-if="canQueueExisting" @click="queueExisting" :disabled="starting || !!mutating" type="button">{{ mutating === 'add' ? '正在加入…' : '将已有影片加入闲时队列' }}</JzButton>
      <span v-if="error" class="hint error" role="alert">{{ error }}</span>
    </div>
    <p v-if="feedback" class="hint" role="status">{{ feedback }}</p>

    <div class="completed-heading">
      <JzButton ref="completedToggleRef" type="button" variant="ghost" class="completed-toggle" icon="image"
        aria-label="已生成缩略图" :aria-expanded="completedOpen" :aria-controls="completedPanelId" @click="toggleCompleted">
        <span>已生成缩略图<span v-if="completed"> · {{ completed.total }} 项</span></span>
        <AppIcon :name="completedOpen ? 'chevron-up' : 'chevron-down'" :size="18" />
      </JzButton>
    </div>
    <div v-if="completedOpen" :id="completedPanelId" class="completed-panel" role="region" aria-label="已生成缩略图清单">
      <div class="completed-summary">
        <p class="hint">当前视频库<template v-if="completed"> · 共 {{ completed.total }} 项</template></p>
        <JzButton ref="completedRefreshRef" type="button" variant="ghost" size="compact" icon="refresh" :loading="completedLoading"
          aria-label="刷新已生成缩略图" @click="loadCompleted">刷新</JzButton>
      </div>
      <p v-if="completedLoading && !completed" class="hint" role="status">正在加载已生成缩略图…</p>
      <p v-else-if="completedError" class="hint error" role="alert">{{ completedError }}
        <JzButton ref="completedRetryRef" type="button" aria-label="重试加载已生成缩略图" @click="loadCompleted">重试</JzButton>
      </p>
      <template v-else-if="completed">
        <p v-if="!completed.total" class="hint">当前视频库暂无可用的缩略图。</p>
        <template v-else>
          <p class="hint completed-note">以下缩略图可用于播放进度预览；已确认的片尾缺失会单独提示覆盖范围。</p>
          <ul class="completed-list">
            <li v-for="item in completed.items" :key="`${item.kind}-${item.id}`" class="completed-row">
              <div class="queue-detail">
                <div class="queue-title-line"><strong :title="item.title">{{ item.title }}</strong>
                  <span v-if="item.needs_update" class="queue-state legacy-state">旧版可用</span>
                  <span v-if="tailDetails(item)" class="queue-state legacy-state">已完成·片尾缺失</span>
                </div>
                <span class="hint queue-file" :title="item.file_name">{{ kindLabel(item.kind) }}<template v-if="item.file_name"> · {{ item.file_name }}</template></span>
                <span class="hint">共 {{ item.frames }} 张<template v-if="item.interval"> · {{ item.interval }} 秒/张</template></span>
                <span v-if="tailDetails(item)" class="hint tail-note">覆盖至 {{ fmtTime(item.coverage_end) }} / 全长 {{ fmtTime(item.duration) }}；已确认片源尾部损坏，末尾部分没有进度预览。</span>
              </div>
              <JzButton v-if="canRevokeTail(item)" :ref="element => setTailRef('revoke', item, element)" type="button" variant="ghost"
                :disabled="starting || !!mutating" :aria-label="`撤销片尾损坏确认 ${item.title}`" @click="openTailDialog(item, 'revoke')">撤销确认</JzButton>
            </li>
          </ul>
          <nav v-if="completed.total > pageSize" class="bar page-bar" aria-label="已生成分页">
            <JzButton ref="completedPreviousRef" type="button" aria-label="已生成上一页" :disabled="completedOffset === 0 || completedLoading" @click="changeCompletedPage(-1)">上一页</JzButton>
            <span class="hint">{{ completedOffset + 1 }}–{{ Math.min(completedOffset + pageSize, completed.total) }} / {{ completed.total }}</span>
            <JzButton ref="completedNextRef" type="button" aria-label="已生成下一页" :disabled="completedOffset + pageSize >= completed.total || completedLoading" @click="changeCompletedPage(1)">下一页</JzButton>
          </nav>
        </template>
      </template>
    </div>

    <div class="completed-heading">
      <JzButton ref="failedToggleRef" type="button" variant="ghost" class="completed-toggle" icon="image"
        aria-label="生成失败" :aria-expanded="failedOpen" :aria-controls="failedPanelId" @click="toggleFailed">
        <span>生成失败<span v-if="failed"> · {{ failed.total }} 项</span></span>
        <AppIcon :name="failedOpen ? 'chevron-up' : 'chevron-down'" :size="18" />
      </JzButton>
    </div>
    <div v-if="failedOpen" :id="failedPanelId" class="completed-panel" role="region" aria-label="生成失败清单">
      <div class="completed-summary">
        <p class="hint">当前视频库<template v-if="failed"> · 共 {{ failed.total }} 项</template></p>
        <JzButton ref="failedRefreshRef" type="button" variant="ghost" size="compact" icon="refresh" :loading="failedLoading"
          aria-label="刷新失败清单" @click="loadFailed()">刷新</JzButton>
      </div>
      <p class="hint completed-note">保留每个视频最近一次失败的原因，重新生成成功后自动移出。</p>
      <p v-if="failedFeedback" class="hint failed-feedback" role="status">{{ failedFeedback }}</p>
      <p v-if="failedActionError" class="hint error failed-feedback" role="alert">{{ failedActionError }}，可再次点击“重新生成”。</p>
      <p v-if="failedLoading && !failed" class="hint" role="status">正在加载失败清单…</p>
      <p v-else-if="failedError" class="hint error" role="alert">{{ failedError }}
        <JzButton ref="failedRetryRef" type="button" aria-label="重试加载失败清单" @click="loadFailed()">重试</JzButton>
      </p>
      <template v-else-if="failed">
        <p v-if="!failed.total" class="hint">当前视频库暂无缩略图生成失败记录。</p>
        <template v-else>
          <ul class="completed-list">
            <li v-for="item in failed.items" :key="`${item.kind}-${item.id}`" class="failed-row">
              <div class="queue-detail">
                <div class="queue-title-line"><strong :title="item.title">{{ item.title }}</strong></div>
                <span class="hint queue-file" :title="item.file_name">{{ kindLabel(item.kind) }}<template v-if="item.file_name"> · {{ item.file_name }}</template></span>
                <span class="hint">最近失败：{{ formatFailedAt(item.failed_at) }}</span>
                <span class="hint failure-reason">{{ item.error || '缩略图生成失败，请重新生成。' }}</span>
              </div>
              <JzButton :ref="element => setFailedItemRef(item, element)" class="queue-action" type="button" icon="refresh"
                :disabled="starting || !!mutating" :loading="mutating === `retry:${itemKey(item)}`"
                :aria-label="`重新生成 ${item.title}`" @click="retryFailed(item)">{{ mutating === `retry:${itemKey(item)}` ? '正在加入…' : '重新生成' }}</JzButton>
              <JzButton v-if="tailCandidate(item)" :ref="element => setTailRef('accept', item, element)" class="queue-action" type="button" variant="ghost"
                :disabled="starting || !!mutating" :aria-label="`确认片尾损坏 ${item.title}`" @click="openTailDialog(item, 'accept')">确认片尾损坏</JzButton>
            </li>
          </ul>
          <nav v-if="failed.total > pageSize" class="bar page-bar" aria-label="生成失败分页">
            <JzButton ref="failedPreviousRef" type="button" aria-label="失败上一页" :disabled="failedOffset === 0 || failedLoading" @click="changeFailedPage(-1)">上一页</JzButton>
            <span class="hint">{{ failedOffset + 1 }}–{{ Math.min(failedOffset + pageSize, failed.total) }} / {{ failed.total }}</span>
            <JzButton ref="failedNextRef" type="button" aria-label="失败下一页" :disabled="failedOffset + pageSize >= failed.total || failedLoading" @click="changeFailedPage(1)">下一页</JzButton>
          </nav>
        </template>
      </template>
    </div>

    <div class="processing-queue" role="region" aria-label="处理队列">
      <div class="queue-heading"><h5 ref="queueHeadingRef" tabindex="-1">处理队列</h5>
        <span v-if="queue" class="hint">共 {{ processingCount }} 项 · 处理中 {{ running ? 1 : 0 }} · 等待 {{ queue.total }}</span>
      </div>
      <p v-if="loading && !queue" class="hint" role="status">正在加载预览任务…</p>
      <p v-else-if="queueError" class="hint error" role="alert">{{ queueError }}
        <JzButton type="button" aria-label="重试加载处理队列" @click="load">重试</JzButton>
      </p>
      <template v-else-if="queue">
        <div v-for="(batch, index) in queue.batches || []" :key="batch.batch_id" class="batch-summary"
          role="group" :aria-label="`立即批量生成批次 ${index + 1}`">
          <span class="hint">本批次剩余 {{ batch.remaining }} 项 · 处理中 {{ batch.running }} · 等待 {{ batch.pending }}</span>
          <JzButton :ref="element => setBatchCancelRef(batch, element)" type="button" variant="ghost" size="compact"
            :disabled="starting || !!mutating" :aria-label="`取消本批次 ${index + 1}`" @click="openBatchCancel(batch)">取消本批次</JzButton>
        </div>
        <p v-if="!processingCount" class="hint">当前视频库没有待处理影片。</p>
        <template v-else>
          <div v-if="queue.total" class="selection-bar">
            <label class="select-all"><input type="checkbox" aria-label="全选本库待处理项" :checked="allSelected"
              :indeterminate="selectionCount > 0 && !allSelected" :disabled="starting || !!mutating" @change="toggleAll" />全选待处理</label>
            <span class="hint" role="status">已选 {{ selectionCount }} 项</span>
            <JzButton type="button" variant="danger" :disabled="!selectionCount || starting || !!mutating" @click="removeSelected">{{ mutating === 'bulk' ? '正在移出…' : '批量移出选中项' }}</JzButton>
            <JzButton v-if="selectionCount" type="button" :disabled="starting || !!mutating" @click="clearSelection">取消选择</JzButton>
          </div>
          <ul class="queue-list">
            <li v-if="running" class="queue-row is-running" aria-current="true">
              <div class="queue-main">
                <span class="current-mark"><AppIcon name="image" :size="20" /></span>
                <div class="queue-detail">
                  <div class="queue-title-line"><strong :title="running.title">{{ running.title }}</strong>
                    <span class="queue-state current-state">{{ running.waiting ? '等待转码' : '正在处理' }}</span>
                  </div>
                  <span class="hint queue-file" :title="running.file_name">{{ kindLabel(running.kind) }} · {{ sourceLabel(running) }}<template v-if="running.file_name"> · {{ running.file_name }}</template></span>
                  <div class="frame-progress">
                    <progress :max="runningTotal || 1" :value="runningTotal ? Math.min(runningFrames, runningTotal) : undefined"
                      :aria-label="`${running.title} 缩略图抽取进度`"
                      :aria-valuetext="runningProgress" />
                    <span class="hint">{{ runningProgress }}</span>
                  </div>
                  <span v-if="running.waiting" class="hint current-note">等待播放转码释放资源，随后继续生成。</span>
                  <span v-else-if="runningTotal && runningFrames >= runningTotal" class="hint current-note">抽帧完成，正在整理缩略图。</span>
                </div>
              </div>
              <JzButton class="queue-action" type="button" variant="ghost" icon="close" icon-only
                :disabled="starting || !!mutating" :loading="mutating === 'running'"
                :aria-label="`取消当前影片 ${running.title}`" title="取消当前影片" @click="removeRunning" />
            </li>
            <li v-for="item in queue.items" :key="`${item.kind}-${item.id}`" class="queue-row is-pending" :class="{ 'is-selected': isSelected(item) }">
              <div class="queue-main">
                <label class="select-item"><input type="checkbox" :aria-label="`选择 ${item.title}`"
                  :checked="isSelected(item)" :disabled="starting || !!mutating" @change="toggleItem(item)" /></label>
                <div class="queue-detail">
                  <div class="queue-title-line"><strong :title="item.title">{{ item.title }}</strong><span class="queue-state pending-state">待处理</span></div>
                  <span class="hint queue-file" :title="item.file_name">{{ kindLabel(item.kind) }} · {{ sourceLabel(item) }}<template v-if="item.file_name"> · {{ item.file_name }}</template></span>
                </div>
              </div>
              <JzButton class="queue-action" type="button" variant="ghost" icon="close" icon-only title="移出队列"
                :disabled="starting || !!mutating" :loading="mutating === `${item.kind}-${item.id}`"
                :aria-label="`从待处理队列移除 ${item.title}`" @click="removeItem(item)" />
            </li>
          </ul>
        </template>
        <nav v-if="queue.total > pageSize" class="bar page-bar" aria-label="处理队列分页">
          <JzButton type="button" :disabled="offset === 0 || starting || !!mutating" @click="changePage(-1)">上一页</JzButton>
          <span class="hint">待处理 {{ offset + 1 }}–{{ Math.min(offset + pageSize, queue.total) }} / {{ queue.total }}</span>
          <JzButton type="button" :disabled="offset + pageSize >= queue.total || starting || !!mutating" @click="changePage(1)">下一页</JzButton>
        </nav>
      </template>
    </div>
    <JzDialog v-if="batchToCancel" title="取消本批次" size="small" :busy="mutating === 'batch'" @close="closeBatchCancel">
      <p>本批次剩余 {{ batchToCancel.remaining }} 项：处理中 {{ batchToCancel.running }} 项，等待 {{ batchToCancel.pending }} 项。</p>
      <p>取消这次立即批量生成后，已有缩略图会保留；手动重试、闲时任务和其他批次继续处理。</p>
      <p v-if="batchCancelError" class="hint error" role="alert">{{ batchCancelError }}，可重试取消。</p>
      <template #footer>
        <JzButton ref="batchKeepRef" type="button" :disabled="mutating === 'batch'" @click="closeBatchCancel">继续生成</JzButton>
        <JzButton ref="batchConfirmRef" type="button" variant="danger" :loading="mutating === 'batch'" @click="cancelBatch">确认取消本批次</JzButton>
      </template>
    </JzDialog>
    <JzDialog v-if="tailDialog" :title="tailDialog.mode === 'accept' ? '确认片尾损坏' : '撤销片尾损坏确认'" size="small" :busy="tailBusy" @close="closeTailDialog">
      <p class="tail-title"><strong>{{ tailDialog.title }}</strong></p>
      <p>有效预览覆盖至 {{ fmtTime(tailDialog.coverage_end) }} / 全长 {{ fmtTime(tailDialog.duration) }}。</p>
      <p>尚有 {{ missingTime(tailDialog.duration - tailDialog.coverage_end) }} 未覆盖；这是当前预览范围，不代表损坏从此处开始。</p>
      <p v-if="tailDialog.mode === 'accept'">请仅在已核对片源、确认尾部损坏后继续。已有预览会保留，并作为“已完成·片尾缺失”列出，末尾不显示预览。日后更换片源可以重新生成。</p>
      <p v-else>撤销后恢复到失败清单，保留已有预览，不会立即重新生成。需要时可在失败清单点击“重新生成”。</p>
      <p v-if="tailError" class="hint error" role="alert">{{ tailError }}</p>
      <template #footer>
        <JzButton ref="tailCancelRef" type="button" :disabled="tailBusy" @click="closeTailDialog">取消</JzButton>
        <JzButton ref="tailConfirmRef" type="button" variant="primary" :loading="tailBusy" @click="submitTailDialog">{{ tailDialog.mode === 'accept' ? '确认片尾损坏并保留预览' : '确认撤销' }}</JzButton>
      </template>
    </JzDialog>
  </section>
</template>

<script setup>
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import AppIcon from './AppIcon.vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api.js'
import { previewTailCoverage } from '../playbackControls.js'
import { fmtTime } from '../playerLabels.js'

const props = defineProps({ libraryId: { type: Number, required: true }, librarySource: String, libraryEnabled: Boolean, active: Boolean })
const pageSize = 50
const auto = ref(null)
const queue = ref(null)
const queueHeadingRef = ref(null)
const batchToCancel = ref(null)
const batchCancelError = ref('')
const batchKeepRef = ref(null)
const batchConfirmRef = ref(null)
const batchCancelRefs = new Map()
let batchTrigger = null
const completed = ref(null)
const completedOpen = ref(false)
const completedOffset = ref(0)
const completedLoading = ref(false)
const completedError = ref('')
const completedPanelId = computed(() => `preview-completed-${props.libraryId}`)
const completedToggleRef = ref(null)
const completedRefreshRef = ref(null)
const completedRetryRef = ref(null)
const completedPreviousRef = ref(null)
const completedNextRef = ref(null)
const failed = ref(null)
const failedOpen = ref(false)
const failedOffset = ref(0)
const failedLoading = ref(false)
const failedError = ref('')
const failedFeedback = ref('')
const failedActionError = ref('')
const failedPanelId = computed(() => `preview-failed-${props.libraryId}`)
const failedToggleRef = ref(null)
const failedRefreshRef = ref(null)
const failedRetryRef = ref(null)
const failedPreviousRef = ref(null)
const failedNextRef = ref(null)
const failedItemRefs = new Map()
const tailDialog = ref(null)
const tailError = ref('')
const tailCancelRef = ref(null)
const tailConfirmRef = ref(null)
const tailActionRefs = new Map()
let tailTrigger = null
const offset = ref(0)
const loading = ref(false)
const queueError = ref('')
const error = ref('')
const feedback = ref('')
const starting = ref(false)
const mutating = ref('')
const tailBusy = computed(() => mutating.value.startsWith('tail:'))
const allMode = ref(false)
const selected = ref(new Set())
const excluded = ref(new Set())
const running = computed(() => queue.value?.running?.library_id === props.libraryId ? queue.value.running : null)
const processingCount = computed(() => (queue.value?.total || 0) + (running.value ? 1 : 0))
const runningFrames = computed(() => Math.max(0, Number(running.value?.frames) || 0))
const runningTotal = computed(() => Math.max(0, Number(running.value?.frame_total) || 0))
const runningProgress = computed(() => runningTotal.value
  ? `${Math.min(runningFrames.value, runningTotal.value)}/${runningTotal.value} 张 · ${Math.min(100, Math.floor(runningFrames.value / runningTotal.value * 100))}%` : '准备中')
const selectionCount = computed(() => allMode.value
  ? Math.max(0, (queue.value?.total || 0) - excluded.value.size) : selected.value.size)
const allSelected = computed(() => !!queue.value?.total && selectionCount.value === queue.value.total)
const canQueueExisting = computed(() => props.librarySource === 'local' && props.libraryEnabled && auto.value && auto.value.mode !== 'off')
const autoWindow = computed(() => auto.value?.start_hour === auto.value?.end_hour ? '全天'
  : ` ${String(auto.value?.start_hour).padStart(2, '0')}:00–${String(auto.value?.end_hour).padStart(2, '0')}:00`)
const kindLabel = kind => ({ movie: '电影', episode: '剧集', extra: '花絮' }[kind] || '视频')
const sourceLabel = item => item.source === 'retry' ? '手动重试' : item.source === 'batch' ? (item.batch_id ? '立即批量' : '立即生成') : item.auto || item.source === 'auto' ? '闲时队列' : '立即生成'
const formatFailedAt = timestamp => {
  const date = new Date(Number(timestamp) * 1000)
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { hour12: false }) : '时间未知'
}
const itemKey = item => `${item.kind}:${item.id}`
const itemRef = key => { const [kind, id] = key.split(':'); return { kind, id: Number(id) } }
const tailDetails = item => previewTailCoverage({ ...item, available_count: item.available_count ?? item.frames })
const missingTime = seconds => {
  const rounded = Math.round(seconds)
  return rounded >= 60 ? `${Math.floor(rounded / 60)}分${rounded % 60}秒` : `${rounded}秒`
}
const snapshotKey = key => typeof key === 'string' && /^[a-f0-9]{32}$/.test(key)
function tailCandidate(item) {
  const candidate = item.tail_acceptance
  if (!candidate || !snapshotKey(candidate.key) || !Number.isInteger(candidate.failed_at) || candidate.failed_at <= 0
      || candidate.failed_at !== item.failed_at || !Number.isInteger(candidate.available_count) || candidate.available_count <= 0
      || !Number.isFinite(candidate.duration) || candidate.duration > 86400
      || !Number.isFinite(candidate.coverage_end) || candidate.coverage_end <= 0 || candidate.coverage_end >= candidate.duration
      || !Number.isFinite(candidate.missing_seconds) || candidate.missing_seconds <= 0
      || Math.abs(candidate.missing_seconds - (candidate.duration - candidate.coverage_end)) > 0.001) return null
  return candidate
}
function canRevokeTail(item) {
  return !!tailDetails(item) && snapshotKey(item.key)
    && (item.confirmed_at === undefined || Number.isInteger(item.confirmed_at) && item.confirmed_at > 0)
}
const isSelected = item => allMode.value ? !excluded.value.has(itemKey(item)) : selected.value.has(itemKey(item))
function clearSelection() { allMode.value = false; selected.value = new Set(); excluded.value = new Set() }
function toggleAll() {
  if (allSelected.value) clearSelection()
  else { allMode.value = true; selected.value = new Set(); excluded.value = new Set() }
}
function toggleItem(item) {
  const key = itemKey(item)
  if (allMode.value) {
    const next = new Set(excluded.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    excluded.value = next
    if (!selectionCount.value) clearSelection()
  } else {
    const next = new Set(selected.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    selected.value = next
  }
}
let timer = null
let requestSeq = 0
let loadCtrl = null
let completedSeq = 0
let completedCtrl = null
let failedSeq = 0
let failedCtrl = null
let ctrl = new AbortController()
let disposed = false

function invalidateCompleted() {
  completedSeq++
  completedCtrl?.abort()
  completedCtrl = null
  completedLoading.value = false
}
function toggleCompleted() {
  completedOpen.value = !completedOpen.value
  if (completedOpen.value) loadCompleted()
  else invalidateCompleted()
}
function completedFocusOwner() {
  if (typeof document === 'undefined') return null
  const controls = [completedRefreshRef, completedRetryRef, completedPreviousRef, completedNextRef]
  const control = controls.find(item => item.value?.el === document.activeElement)
  return control ? { control, element: document.activeElement } : null
}
function restoreCompletedFocus(owner) {
  if (!owner || ![document.body, owner.element].includes(document.activeElement)) return
  // Refresh becomes disabled while busy; retry/paging controls may be replaced.
  const paging = [completedPreviousRef, completedNextRef].includes(owner.control)
  const choices = [owner.control, ...(paging ? [completedPreviousRef, completedNextRef] : []), completedRefreshRef, completedToggleRef]
  const target = choices.find(item => item.value?.el && !item.value.el.disabled && item.value.el.isConnected !== false)
  target?.value?.focus()
}
async function loadCompleted() {
  if (!props.active || !completedOpen.value || disposed) return
  const focusOwner = completedFocusOwner()
  invalidateCompleted()
  const seq = completedSeq
  const libraryId = props.libraryId
  const requestCtrl = new AbortController()
  completedCtrl = requestCtrl
  completedLoading.value = true
  completedError.value = ''
  try {
    const result = await api(`/api/stream/previews/completed?library_id=${libraryId}&offset=${completedOffset.value}&limit=${pageSize}`, { signal: requestCtrl.signal })
    if (disposed || seq !== completedSeq || requestCtrl.signal.aborted) return
    completed.value = result
    if (completedOffset.value > 0 && result.total <= completedOffset.value) {
      completedOffset.value = Math.max(0, Math.floor((result.total - 1) / pageSize) * pageSize)
      return loadCompleted()
    }
  } catch (e) {
    if (!disposed && seq === completedSeq && !requestCtrl.signal.aborted) completedError.value = e.message
  } finally {
    if (!disposed && seq === completedSeq) {
      completedCtrl = null
      completedLoading.value = false
      await nextTick()
      if (!disposed && seq === completedSeq && completedOpen.value) restoreCompletedFocus(focusOwner)
    }
  }
}
function changeCompletedPage(delta) {
  if (completedLoading.value) return
  completedOffset.value = Math.max(0, completedOffset.value + delta * pageSize)
  completed.value = null
  loadCompleted()
}

function invalidateFailed() {
  failedSeq++
  failedCtrl?.abort()
  failedCtrl = null
  failedLoading.value = false
}
function toggleFailed() {
  failedOpen.value = !failedOpen.value
  if (failedOpen.value) loadFailed()
  else invalidateFailed()
}
function setFailedItemRef(item, element) {
  if (element) failedItemRefs.set(itemKey(item), element)
  else failedItemRefs.delete(itemKey(item))
}
function failedFocusOwner() {
  if (typeof document === 'undefined') return null
  const element = document.activeElement
  const control = [failedRefreshRef, failedRetryRef, failedPreviousRef, failedNextRef]
    .find(item => item.value?.el === element)
  if (control) return { control, element }
  const item = [...failedItemRefs.entries()].find(([, component]) => component.el === element)
  return item ? { key: item[0], element } : null
}
function restoreFailedFocus(owner) {
  if (!owner || ![document.body, owner.element].includes(document.activeElement)) return
  const paging = [failedPreviousRef, failedNextRef].includes(owner.control)
  const choices = [owner.key ? failedItemRefs.get(owner.key) : owner.control?.value,
    ...(paging ? [failedPreviousRef.value, failedNextRef.value] : []), failedRefreshRef.value, failedToggleRef.value]
  choices.find(item => item?.el && !item.el.disabled && item.el.isConnected !== false)?.focus()
}
async function loadFailed(focusOwner = failedFocusOwner()) {
  if (!props.active || !failedOpen.value || disposed) return
  invalidateFailed()
  const seq = failedSeq
  const libraryId = props.libraryId
  const requestCtrl = new AbortController()
  failedCtrl = requestCtrl
  failedLoading.value = true
  failedError.value = ''
  try {
    const result = await api(`/api/stream/previews/failed?library_id=${libraryId}&offset=${failedOffset.value}&limit=${pageSize}`, { signal: requestCtrl.signal })
    if (disposed || seq !== failedSeq || requestCtrl.signal.aborted) return
    failed.value = result
    if (failedOffset.value > 0 && result.total <= failedOffset.value) {
      failedOffset.value = Math.max(0, Math.floor((result.total - 1) / pageSize) * pageSize)
      return loadFailed(focusOwner)
    }
  } catch (e) {
    if (!disposed && seq === failedSeq && !requestCtrl.signal.aborted) failedError.value = e.message
  } finally {
    if (!disposed && seq === failedSeq) {
      failedCtrl = null
      failedLoading.value = false
      await nextTick()
      if (!disposed && seq === failedSeq && failedOpen.value) restoreFailedFocus(focusOwner)
    }
  }
}
function changeFailedPage(delta) {
  if (failedLoading.value) return
  const focusOwner = failedFocusOwner()
  failedOffset.value = Math.max(0, failedOffset.value + delta * pageSize)
  failed.value = null
  loadFailed(focusOwner)
}

function invalidateLoad() {
  clearTimeout(timer)
  requestSeq++
  loadCtrl?.abort()
  loadCtrl = null
  loading.value = false
}
function beginAction() {
  invalidateLoad()
  error.value = ''
  feedback.value = ''
  return ctrl.signal
}
const isCurrent = signal => !disposed && !signal.aborted
async function finishAction(signal, refreshFailures = true) {
  if (!isCurrent(signal)) return
  starting.value = false
  mutating.value = ''
  await load({ refreshFailures })
}
async function load({ refreshFailures = true } = {}) {
  if (!props.active || disposed || starting.value || mutating.value) return
  invalidateLoad()
  const seq = requestSeq
  const libraryId = props.libraryId
  const requestCtrl = new AbortController()
  loadCtrl = requestCtrl
  loading.value = true
  // Failure history refreshes independently, even when queue status is unavailable.
  if (refreshFailures && failedOpen.value && !failedLoading.value) loadFailed()
  try {
    const [autoStatus, queueResult] = await Promise.all([
      api('/api/stream/previews/auto', { signal: requestCtrl.signal }),
      api(`/api/stream/previews/queue?library_id=${libraryId}&offset=${offset.value}&limit=${pageSize}`, { signal: requestCtrl.signal }),
    ])
    if (disposed || seq !== requestSeq) return
    const previousRunning = running.value
    const currentRunning = queueResult.running?.library_id === libraryId ? queueResult.running : null
    const currentChanged = previousRunning && (!currentRunning || itemKey(previousRunning) !== itemKey(currentRunning))
    auto.value = autoStatus
    if (queue.value && queueResult.revision !== queue.value.revision && selectionCount.value) {
      clearSelection()
      feedback.value = '待处理队列已变化，请重新选择。'
    }
    queue.value = queueResult
    queueError.value = ''
    // Read full completed pages only while requested and after a task transition.
    if (completedOpen.value && currentChanged) loadCompleted()
    if (offset.value > 0 && queueResult.total <= offset.value) {
      offset.value = Math.max(0, Math.floor((queueResult.total - 1) / pageSize) * pageSize)
      return load({ refreshFailures })
    }
  } catch (e) {
    if (!disposed && seq === requestSeq && e.name !== 'AbortError') queueError.value = e.message
  } finally {
    if (!disposed && seq === requestSeq) {
      loadCtrl = null
      loading.value = false
      if (props.active) timer = setTimeout(load, running.value || queue.value?.total ? 2500 : 30000)
    }
  }
}
async function start() {
  if (starting.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  starting.value = true
  try {
    const result = await api('/api/stream/previews/jobs', {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal,
    })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已加入 ${result.added} 项立即批量任务${result.promoted ? `，转为立即处理 ${result.promoted} 项` : ''}；已有新版预览 ${result.ready} 项，已在处理队列 ${result.already_queued} 项，源文件不可用 ${result.unavailable} 项。`
    offset.value = 0
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
async function queueExisting() {
  if (starting.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  mutating.value = 'add'
  try {
    const result = await api('/api/stream/previews/queue', {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal,
    })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已加入 ${result.added} 项；已有新版预览 ${result.ready} 项，已在队列 ${result.already_queued} 项，源文件不可用 ${result.unavailable} 项。`
    offset.value = 0
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
async function removeItem(item) {
  if (starting.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  mutating.value = `${item.kind}-${item.id}`
  try {
    await api(`/api/stream/previews/queue/${item.kind}/${item.id}?library_id=${libraryId}`, { method: 'DELETE', signal })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已将“${item.title}”移出队列。`
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
async function removeSelected() {
  if (!selectionCount.value || !queue.value || starting.value || mutating.value) return
  const libraryId = props.libraryId
  const body = { library_id: libraryId, revision: queue.value.revision, all_pending: allMode.value,
    items: [...selected.value].map(itemRef), exclude: [...excluded.value].map(itemRef) }
  const signal = beginAction()
  mutating.value = 'bulk'
  try {
    const result = await api('/api/stream/previews/queue/remove', {
      method: 'POST', body: JSON.stringify(body), signal,
    })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已移出 ${result.removed} 项待处理影片。`
  } catch (e) {
    if (!isCurrent(signal)) return
    if (e.status === 409) {
      clearSelection()
      feedback.value = '待处理队列已变化，请重新选择。'
    } else error.value = e.message
  } finally { await finishAction(signal) }
}
async function removeRunning() {
  const current = running.value
  if (!current || starting.value || mutating.value) return
  const focusOwner = typeof document === 'undefined' ? null : document.activeElement
  const libraryId = props.libraryId
  const signal = beginAction()
  mutating.value = 'running'
  try {
    await api(`/api/stream/previews/queue/${current.kind}/${current.id}?library_id=${libraryId}`, { method: 'DELETE', signal })
    if (!isCurrent(signal)) return
    feedback.value = `已取消并移出“${current.title}”。`
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally {
    await finishAction(signal)
    await nextTick()
    if (isCurrent(signal)) restoreQueueFocus(focusOwner)
  }
}
function restoreQueueFocus(owner) {
  if (owner?.isConnected === false && [document.body, owner].includes(document.activeElement)) queueHeadingRef.value?.focus?.()
}
function setBatchCancelRef(batch, element) {
  if (element) batchCancelRefs.set(batch.batch_id, element)
  else batchCancelRefs.delete(batch.batch_id)
}
async function openBatchCancel(batch) {
  if (starting.value || mutating.value) return
  batchTrigger = batchCancelRefs.get(batch.batch_id)
  batchCancelError.value = ''
  batchToCancel.value = { ...batch, library_id: props.libraryId }
  // Let the shared dialog establish its focus trap before choosing the safe action.
  await nextTick()
  await nextTick()
  if (batchToCancel.value?.batch_id === batch.batch_id) batchKeepRef.value?.focus()
}
async function closeBatchCancel() {
  if (mutating.value === 'batch') return
  batchToCancel.value = null
  batchCancelError.value = ''
  const trigger = batchTrigger
  batchTrigger = null
  await nextTick()
  if (disposed || !props.active) return
  if (trigger?.el && trigger.el.isConnected !== false && !trigger.el.disabled) trigger.focus()
  else queueHeadingRef.value?.focus?.()
}
async function cancelBatch() {
  const batch = batchToCancel.value
  if (!batch || batch.library_id !== props.libraryId || starting.value || mutating.value) return
  const signal = beginAction()
  const focusOwner = batchTrigger?.el
  let cancelled = false
  batchCancelError.value = ''
  mutating.value = 'batch'
  try {
    const result = await api(`/api/stream/previews/batches/${batch.batch_id}/cancel`, {
      method: 'POST', body: JSON.stringify({ library_id: batch.library_id }), signal,
    })
    if (!isCurrent(signal)) return
    feedback.value = `已取消本批次，移出 ${result.removed} 项${result.cancelled ? '，并停止当前影片' : ''}。`
    clearSelection()
    mutating.value = ''
    await closeBatchCancel()
    cancelled = true
  } catch (e) {
    if (isCurrent(signal)) batchCancelError.value = e.message
  } finally {
    await finishAction(signal)
    await nextTick()
    if (isCurrent(signal) && cancelled) restoreQueueFocus(focusOwner)
    if (isCurrent(signal) && batchToCancel.value) {
      await nextTick()
      batchConfirmRef.value?.focus()
    }
  }
}
async function retryFailed(item) {
  if (starting.value || mutating.value) return
  const libraryId = props.libraryId
  const focusOwner = failedFocusOwner()
  const signal = beginAction()
  failedFeedback.value = ''
  failedActionError.value = ''
  mutating.value = `retry:${itemKey(item)}`
  try {
    const result = await api(`/api/stream/previews/failed/${item.kind}/${item.id}/retry`, {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal,
    })
    if (!isCurrent(signal)) return
    failedFeedback.value = ({
      queued: `已将“${item.title}”加入处理队列末尾。`,
      already_queued: `“${item.title}”已在处理队列中。`,
      running: `“${item.title}”正在处理中。`,
    })[result.state] || '处理队列已更新。'
  } catch (e) {
    if (isCurrent(signal)) failedActionError.value = e.message
  } finally {
    await finishAction(signal, false)
    if (isCurrent(signal) && failedOpen.value) await loadFailed(focusOwner)
  }
}
function setTailRef(mode, item, element) {
  const key = `${mode}:${itemKey(item)}`
  if (element) tailActionRefs.set(key, element)
  else tailActionRefs.delete(key)
}
async function openTailDialog(item, mode) {
  if (disposed || !props.active || starting.value || mutating.value || tailDialog.value) return
  const candidate = mode === 'accept' ? tailCandidate(item) : canRevokeTail(item) ? item : null
  const inventory = mode === 'accept' ? failed.value : completed.value
  if (!candidate || !inventory?.items.some(value => itemKey(value) === itemKey(item))) return
  tailTrigger = tailActionRefs.get(`${mode}:${itemKey(item)}`)
  tailError.value = ''
  const dialog = { ...candidate, kind: item.kind, id: item.id, title: item.title,
    library_id: props.libraryId, mode, signal: ctrl.signal }
  tailDialog.value = dialog
  await nextTick()
  await nextTick()
  if (tailDialog.value?.signal === dialog.signal && isCurrent(dialog.signal)) tailCancelRef.value?.focus()
}
function restoreTailFocus(trigger, mode) {
  if (typeof document === 'undefined') return
  if (![document.body, trigger?.el].includes(document.activeElement) && document.activeElement?.isConnected !== false) return
  const fallback = mode === 'accept' ? [failedRefreshRef.value, failedToggleRef.value]
    : [completedRefreshRef.value, completedToggleRef.value]
  ;[trigger, ...fallback].find(item => item?.el && !item.el.disabled && item.el.isConnected !== false)?.focus()
}
async function closeTailDialog() {
  const dialog = tailDialog.value
  if (!dialog || tailBusy.value) return
  const trigger = tailTrigger
  tailDialog.value = null
  tailTrigger = null
  tailError.value = ''
  await nextTick()
  if (isCurrent(dialog.signal) && props.active && props.libraryId === dialog.library_id) restoreTailFocus(trigger, dialog.mode)
}
async function submitTailDialog() {
  const dialog = tailDialog.value
  if (!dialog || !isCurrent(dialog.signal) || dialog.library_id !== props.libraryId || starting.value || mutating.value) return
  const signal = beginAction()
  const trigger = tailTrigger
  const accept = dialog.mode === 'accept'
  const current = () => isCurrent(signal) && props.active && props.libraryId === dialog.library_id
  let saved = false
  mutating.value = `tail:${dialog.mode}`
  tailError.value = ''
  try {
    const body = accept ? { library_id: dialog.library_id, key: dialog.key, failed_at: dialog.failed_at,
      available_count: dialog.available_count, confirmed: true }
      : { library_id: dialog.library_id, key: dialog.key,
        ...(dialog.confirmed_at !== undefined ? { confirmed_at: dialog.confirmed_at } : {}) }
    // The source snapshot fixes this write. Leaving stops UI tracking, not a submitted server action.
    await api(`/api/stream/previews/${accept ? 'failed' : 'completed'}/${dialog.kind}/${dialog.id}/${accept ? 'accept-tail' : 'tail-acceptance'}`, {
      method: accept ? 'POST' : 'DELETE', body: JSON.stringify(body),
    })
    if (!current()) return
    saved = true
    feedback.value = accept ? `已将“${dialog.title}”作为带片尾缺失提示的已生成预览保留。`
      : `已撤销“${dialog.title}”的片尾损坏确认，已有预览保留，可在失败清单重新生成。`
    completedOpen.value = true
    if (!accept) failedOpen.value = true
    tailDialog.value = null
    tailTrigger = null
  } catch (e) {
    if (current()) tailError.value = e.status === 409 ? `${e.message}；请取消并刷新清单后重新核对。` : e.message
  } finally {
    if (current() && saved) await Promise.all([loadCompleted(), loadFailed(null)])
    await finishAction(signal, false)
    if (current() && saved) {
      await nextTick()
      if (current() && !tailDialog.value && !mutating.value) restoreTailFocus(trigger, dialog.mode)
    } else if (current() && tailDialog.value && !mutating.value) {
      await nextTick()
      if (current() && !mutating.value) tailConfirmRef.value?.focus()
    }
  }
}
function changePage(delta) { offset.value = Math.max(0, offset.value + delta * pageSize); load() }
watch(() => [props.active, props.libraryId], ([active]) => {
  invalidateLoad()
  invalidateCompleted()
  invalidateFailed()
  ctrl.abort()
  ctrl = new AbortController()
  queue.value = null
  batchToCancel.value = null
  batchCancelError.value = ''
  batchTrigger = null
  batchCancelRefs.clear()
  tailDialog.value = null
  tailError.value = ''
  tailTrigger = null
  tailActionRefs.clear()
  auto.value = null
  completed.value = null
  completedOffset.value = 0
  completedError.value = ''
  failed.value = null
  failedOffset.value = 0
  failedError.value = ''
  failedFeedback.value = ''
  failedActionError.value = ''
  starting.value = false
  mutating.value = ''
  offset.value = 0
  clearSelection()
  feedback.value = ''
  queueError.value = ''
  error.value = ''
  if (active) {
    load()
    if (completedOpen.value) loadCompleted()
  }
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { disposed = true; ctrl.abort(); invalidateLoad(); invalidateCompleted(); invalidateFailed() })
</script>

<style scoped>
.preview-maintenance { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--jz-border); }
h4 { margin: 0 0 8px; font-size: var(--jz-font-xl); }
h5 { margin: 0; font-size: var(--jz-font-xl); font-weight: 700; line-height: 1.4; }
.hint { color: var(--jz-text-dim); font-size: 0.8125rem; }
.error { color: var(--jz-danger); }
.bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.completed-heading { margin-top: var(--jz-gap-xl); }
.completed-toggle { width: 100%; justify-content: flex-start; padding-inline: var(--jz-gap-m); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); }
.completed-toggle > span { flex: 1; min-width: 0; text-align: left; }
.completed-panel { padding: 0 var(--jz-gap-m) var(--jz-gap-m); border: 1px solid var(--jz-border); border-top: 0; border-radius: 0 0 var(--jz-radius-s) var(--jz-radius-s); }
.completed-summary { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--jz-gap-s); }
.completed-summary > p { margin-block: var(--jz-gap-m); }
.completed-note { margin: 0 0 var(--jz-gap-s); }
.completed-list { list-style: none; padding: 0; margin: 0; }
.completed-row { display: flex; align-items: flex-start; gap: var(--jz-gap-m); padding: var(--jz-gap-m) 0; border-top: 1px solid var(--jz-border); }
.failed-row { display: flex; align-items: flex-start; gap: var(--jz-gap-m); padding: var(--jz-gap-m) 0; border-top: 1px solid var(--jz-border); }
.failure-reason, .failed-feedback { overflow-wrap: anywhere; white-space: pre-wrap; line-height: 1.6; }
.queue-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-top: var(--jz-gap-xl); margin-bottom: var(--jz-gap-m); padding-left: var(--jz-gap-m); border-left: 3px solid var(--jz-accent); }
.batch-summary { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--jz-gap-s); margin-bottom: var(--jz-gap-m); }
.selection-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin: 0 0 8px; padding: 8px 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); background: var(--jz-surface-2); }
.select-all, .select-item { display: inline-flex; align-items: center; cursor: pointer; }
.select-all { gap: 8px; min-height: 44px; font-size: var(--jz-font-m); white-space: nowrap; }
.select-item { justify-content: center; width: 44px; min-width: 44px; height: 44px; }
.select-all input, .select-item input { width: 18px; height: 18px; margin: 0; accent-color: var(--jz-accent); cursor: pointer; }
.select-all input:focus-visible, .select-item input:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
.queue-list { list-style: none; padding: 0; margin: 0; }
.queue-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0; border-top: 1px solid var(--jz-border); }
.queue-row.is-selected { background: var(--jz-selected); }
.queue-row.is-running { padding: var(--jz-gap-m) var(--jz-gap-s) var(--jz-gap-m) 0; margin-bottom: var(--jz-gap-s); border: 1px solid var(--jz-border-strong); border-left: 3px solid var(--jz-accent); border-radius: var(--jz-radius-s); background: var(--jz-accent-soft); }
.queue-main { display: flex; align-items: center; flex: 1; min-width: 0; gap: 4px; }
.current-mark { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 44px; color: var(--jz-text); }
.queue-detail { display: grid; flex: 1; gap: 3px; min-width: 0; }
.queue-title-line { display: flex; align-items: center; gap: var(--jz-gap-s); min-width: 0; }
.queue-title-line > strong { flex: 1; min-width: 0; }
.queue-state { flex: none; font-size: var(--jz-font-s); line-height: 1.5; white-space: nowrap; }
.current-state { color: var(--jz-text); }
.pending-state { color: var(--jz-text-faint); }
.legacy-state { color: var(--jz-warn); }
.tail-note { overflow-wrap: anywhere; line-height: 1.6; }
.tail-title { margin-top: 0; overflow-wrap: anywhere; }
.completed-row > button { flex: none; }
.queue-detail strong { font-size: var(--jz-font-m); font-weight: 500; }
.queue-detail strong, .queue-file { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-detail .hint { margin: 0; }
.queue-action { flex: none; align-self: center; }
.queue-row > .queue-detail .hint { overflow-wrap: anywhere; }
.frame-progress { display: flex; align-items: center; gap: var(--jz-gap-s); min-width: 0; }
.frame-progress progress { display: block; flex: 1; min-width: 0; width: 100%; height: 5px; overflow: hidden; border: 0; border-radius: var(--jz-radius-pill); background: var(--jz-border-strong); accent-color: var(--jz-accent); }
.frame-progress progress::-webkit-progress-bar { background: var(--jz-border-strong); border-radius: var(--jz-radius-pill); }
.frame-progress progress::-webkit-progress-value { background: var(--jz-accent); border-radius: var(--jz-radius-pill); }
.frame-progress progress::-moz-progress-bar { background: var(--jz-accent); border-radius: var(--jz-radius-pill); }
.frame-progress > span { flex: none; font-variant-numeric: tabular-nums; white-space: nowrap; }
.current-note { line-height: 1.5; }
.page-bar { margin-top: 12px; }
@media (max-width: 480px) {
  .failed-row, .completed-row { flex-direction: column; align-items: stretch; }
  .failed-row > .queue-action { align-self: flex-start; }
}
</style>
