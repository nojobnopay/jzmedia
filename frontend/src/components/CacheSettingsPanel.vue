<template>
  <div class="cache-settings">
    <form v-if="saved" @submit.prevent="save">
      <JzField id="data_warning_gb" label="应用数据总预算（软上限，GB）" :error="capacityError(draft.data_warning_gb, 1)"
        hint="规划整个应用的数据空间，包含两项缓存和基础数据。活动播放与必要的数据写入受保护，实际占用可能暂时超过预算。"
        v-slot="{ id, describedby, invalid }">
        <div class="capacity-control budget-total-control">
          <input type="range" aria-label="应用数据总预算" :aria-describedby="descriptions(describedby)" :aria-invalid="invalid || !!budget.error || undefined"
            :aria-valuetext="draft.data_warning_gb + ' GB'" min="1" :max="sliderMax('data_warning_gb')" step="0.1"
            v-model.number="draft.data_warning_gb" :disabled="saving" @input="clearMessage" />
          <input :id="id" type="number" min="1" max="102400" step="0.1" required :aria-describedby="descriptions(describedby)"
            :aria-invalid="invalid || !!budget.error || undefined" v-model.number="draft.data_warning_gb" :disabled="saving" @input="clearMessage" />
        </div>
      </JzField>

      <section class="budget-allocation" aria-label="预算分配">
        <div class="allocation-heading"><h4>预算分配</h4><span v-if="dirty" class="draft-label">未保存</span></div>
        <BudgetAllocationBar ref="allocationBar" v-model="draft" :usage="usageError ? null : usage"
          :disabled="saving || usageLoading || !!editor" @change="clearMessage" @edit="openEditor($event.part, $event.anchor)" />
        <ul class="budget-legend">
          <li v-for="part in legendParts" :key="part.key" :class="{ 'is-editable': part.field }">
            <JzButton v-if="part.field" variant="ghost" class="budget-quota-trigger" :data-quota="part.key" :data-quota-value="draft[part.field.key]"
              :aria-label="'精确设置' + part.field.label" :disabled="saving || !!editor" @click="openEditor(part.key, $event.currentTarget)">
              <span class="budget-dot" :class="'budget-segment--' + part.key" aria-hidden="true" />
              <span class="quota-name">{{ part.label }}</span><strong>{{ part.key === 'transcode' && part.gb === 0 ? '未限制容量' : gb(part.gb) }}</strong>
              <AppIcon name="edit" :size="16" class="quota-edit-icon" />
            </JzButton>
            <template v-else><span class="budget-dot" :class="'budget-segment--' + part.key" aria-hidden="true" />
              <span>{{ part.label }}</span><strong>{{ gb(part.gb) }}</strong></template>
          </li>
        </ul>
        <template v-if="budget.segments.length">
          <p v-if="budget.excessGb && budget.minimumTotalGb <= draft.data_warning_gb" class="hint">可直接拖小任一缓存额度，或提高总预算；配平后即可保存。</p>
        </template>
        <p v-else-if="usageLoading" class="hint" role="status">正在统计基础数据占用…</p>
        <p v-else-if="!budget.available" class="hint">占用统计恢复后显示预算分配。</p>
        <p v-if="budget.error && !usageLoading" id="cache-budget-error" class="budget-error" role="alert">{{ budget.error }}</p>

        <p class="hint capacity-note">色条表示规划额度。两项缓存与基础数据预留共同计入总预算；调低额度后，后续维护回收可淘汰缓存。1 GB 按 1024³ 字节计算，最多可设 102400 GB。</p>
        <div class="bar capacity-actions">
          <JzButton type="submit" variant="primary" icon="save" :loading="saving" :disabled="!draftDirty || !budget.valid || usageLoading || !!editor">保存容量设置</JzButton>
          <JzButton :disabled="!dirty || saving || !!editor" @click="discard">放弃修改</JzButton>
        </div>
        <p v-if="error" class="warn-text" role="alert">{{ error }}</p>
        <p v-if="message" class="feedback" role="status">{{ message }}</p>

        <div class="budget-base">
          <div class="base-heading"><h4>基础数据</h4><span v-if="budget.available">已用 {{ fmtBytes(budget.baseBytes) }} · 自动预留 {{ gb(budget.reservedGb) }}</span></div>
          <p class="hint">数据库、海报和其他数据。增长余量取当前占用的 10% 与 1 GB 中较大者，预留向上取整至 0.1 GB。</p>
          <details v-if="budget.available" class="base-details"><summary>查看基础数据明细</summary>
            <dl class="info-grid"><dt>数据库与写入日志</dt><dd>{{ fmtBytes(usage.categories.database) }}</dd><dt>海报与图片</dt><dd>{{ fmtBytes(usage.categories.posters) }}</dd><dt>其他应用数据</dt><dd>{{ fmtBytes(usage.categories.other) }}</dd></dl>
          </details>
        </div>

        <div v-for="field in cacheFields" :key="field.key" class="cache-budget-row" :data-cache="field.part">
          <div class="base-heading"><h4>{{ field.part === 'transcode' ? '播放缓存' : '进度缩略图' }}</h4>
            <p class="cache-actual">实际占用 <strong>{{ usage?.ok ? fmtBytes(usage.categories[field.part]) : '暂不可用' }}</strong></p></div>
          <p class="hint">{{ field.hint }}</p>
          <CacheCleanupControls :kind="field.part" :disabled="saving" />
        </div>
      </section>
    </form>
    <p v-else-if="!error" class="hint" role="status">正在加载容量设置…</p>
    <p v-if="error && !saved" class="warn-text" role="alert">{{ error }} <JzButton @click="load" icon="refresh">重新加载</JzButton></p>
    <BudgetQuotaEditor v-if="editor" ref="quotaEditor" :label="editor.field.label" :value="editor.value" :error="editor.error"
      :anchor="editor.anchor" :disabled="saving" @update:value="updateEditorValue" @confirm="confirmEditor" @cancel="closeEditor" />
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'
import { cacheBudget, capacityError } from '../cacheBudget.js'
import { useSettingsDraft } from '../settingsDrafts.js'
import BudgetAllocationBar from './BudgetAllocationBar.vue'
import BudgetQuotaEditor from './BudgetQuotaEditor.vue'
import CacheCleanupControls from './CacheCleanupControls.vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'

const props = defineProps({ usage: { type: Object, default: null }, usageError: { type: String, default: '' }, usageLoading: Boolean })
const emit = defineEmits(['saving', 'refresh'])
const cacheFields = [
  { key: 'transcode_cache_gb', part: 'transcode', label: '播放缓存额度',
    hint: '包含播放分片和预缓存，按容量及 24 小时过期规则回收；活动会话受保护。' },
  { key: 'preview_cache_gb', part: 'previews', label: '进度缩略图额度',
    hint: '包含电影、剧集和花絮的进度预览；达到额度的 90% 时暂停闲时生成。' },
]
const saved = ref(null), draft = ref({}), saving = ref(false), error = ref(''), message = ref('')
const allocationBar = ref(null), quotaEditor = ref(null), editor = shallowRef(null)
const controller = new AbortController()
const keys = ['data_warning_gb', ...cacheFields.map(field => field.key)]
const draftDirty = computed(() => !!saved.value && keys.some(key => draft.value[key] !== saved.value[key]))
const bufferDirty = computed(() => !!editor.value && editor.value.value !== editor.value.initial)
const dirty = computed(() => draftDirty.value || bufferDirty.value)
const budget = computed(() => cacheBudget(draft.value, props.usageError ? null : props.usage))
const legendParts = computed(() => [
  { key: 'base', label: '基础数据预留', gb: budget.value.reservedGb },
  ...cacheFields.map(field => ({ key: field.part, label: field.part === 'transcode' ? '播放缓存' : '进度缩略图', gb: draft.value[field.key], field })),
  { key: 'free', label: '未分配余量', gb: budget.value.freeGb },
])
function gb(value) { return Number.isFinite(value) ? Number(value.toFixed(1)).toLocaleString('zh-CN') + ' GB' : '暂不可用' }
function sliderMax(key) { return Math.max(100, saved.value?.[key] || 0, Number(draft.value[key]) || 0, key === 'data_warning_gb' ? 0 : Number(draft.value.data_warning_gb) || 0) }
function descriptions(id) { return [id, budget.value.error && !props.usageLoading ? 'cache-budget-error' : null].filter(Boolean).join(' ') || undefined }
function editorError(value) { return capacityError(value.trim() === '' ? '' : Number(value), 0.1) }
function openEditor(part, anchor) {
  if (saving.value || editor.value) return
  const field = cacheFields.find(item => item.part === part)
  if (!field) return
  allocationBar.value?.cancelInteraction()
  const initial = String(draft.value[field.key] ?? '')
  editor.value = { field, anchor, initial, value: initial, error: '' }
}
function updateEditorValue(value) {
  if (!editor.value) return
  editor.value = { ...editor.value, value, error: editor.value.error ? editorError(value) : '' }
  clearMessage()
}
async function closeEditor() {
  const previous = editor.value
  if (!previous) return
  editor.value = null
  await nextTick()
  if (disposed) return
  const origin = previous.anchor
  const target = origin?.isConnected && !origin.disabled && origin.getClientRects().length ? origin
    : document.querySelector(`.budget-legend [data-quota="${previous.field.part}"]`)
  target?.focus({ preventScroll: true })
}
function confirmEditor() {
  if (!editor.value || saving.value) return
  const invalid = editorError(editor.value.value)
  if (invalid) { editor.value = { ...editor.value, error: invalid }; quotaEditor.value?.focus(); return }
  draft.value = { ...draft.value, [editor.value.field.key]: Number(editor.value.value) }
  clearMessage()
  closeEditor()
}
function clearMessage() { message.value = ''; error.value = '' }
function discard() { editor.value = null; draft.value = { ...saved.value }; clearMessage() }
useSettingsDraft({ section: 'sec-index', label: '应用数据与缓存', dirty: () => dirty.value,
  busy: () => saving.value, discard, edit: () => editor.value ? quotaEditor.value?.focus() : document.getElementById('data_warning_gb')?.focus() })
watch(saving, value => emit('saving', value), { flush: 'sync' })
async function load() {
  error.value = ''
  try { saved.value = await api('/api/cache-settings', { signal: controller.signal }); discard() }
  catch (e) { if (!controller.signal.aborted) error.value = '容量设置加载失败：' + e.message }
}
async function save() {
  if (saving.value || editor.value || !draftDirty.value || !budget.value.valid || props.usageLoading) return
  saving.value = true
  clearMessage()
  try {
    saved.value = await api('/api/cache-settings', { method: 'PUT', body: JSON.stringify(draft.value), signal: controller.signal })
    draft.value = { ...saved.value }
    message.value = '容量设置已保存并生效'
    window.dispatchEvent(new Event('jzmedia:data-usage-changed'))
  } catch (e) {
    if (!controller.signal.aborted) { error.value = '保存失败：' + e.message; emit('refresh') }
  } finally { saving.value = false }
}
onMounted(load)
let disposed = false
onBeforeUnmount(() => { disposed = true; controller.abort() })
</script>

<style scoped>
.cache-settings { max-width: 54rem; }
.capacity-control { display: grid; grid-template-columns: minmax(0, 1fr) 7rem; align-items: center; gap: var(--jz-gap-l); max-width: 36rem; }
.capacity-control input[type="range"] { min-width: 0; min-height: 44px; }
.capacity-control input[type="number"] { min-width: 0; }
.budget-allocation { margin-top: 24px; }
.allocation-heading, .base-heading { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px 16px; }
h4 { margin: 0; font-size: var(--jz-font-m); font-weight: 600; }
.draft-label { color: var(--jz-warn); font-size: var(--jz-font-s); }
.budget-segment--base { background: var(--jz-text-dim); }
.budget-segment--transcode { background: var(--jz-blue-chip); }
.budget-segment--previews { background: var(--jz-green); }
.budget-segment--free { background: var(--jz-surface-3); }
.budget-legend { display: flex; flex-wrap: wrap; gap: 12px 24px; list-style: none; padding: 0; margin: 12px 0; font-size: var(--jz-font-s); }
.budget-legend li { display: flex; align-items: center; gap: 6px; min-width: 0; }
.budget-legend strong { font-weight: 500; }
.budget-dot { flex: 0 0 9px; height: 9px; border: 1px solid var(--jz-border-strong); border-radius: 2px; }
.budget-error { padding: 12px; color: var(--jz-danger); background: var(--jz-danger-soft); border-radius: var(--jz-radius-s); font-size: var(--jz-font-s); line-height: 1.6; }
.budget-quota-trigger { display: grid; grid-template-columns: 9px minmax(0, 1fr) 16px; gap: 2px 6px; min-height: 44px;
  padding: 6px 8px; border-color: var(--jz-border-strong); font-size: var(--jz-font-s); text-align: left; }
.budget-quota-trigger .budget-dot { grid-column: 1; grid-row: 1 / 3; width: 9px; align-self: center; }
.quota-name { grid-column: 2; grid-row: 1; }
.budget-quota-trigger strong { grid-column: 2; grid-row: 2; }
.quota-edit-icon { grid-column: 3; grid-row: 1 / 3; }
.budget-base { margin-top: 24px; padding-block: 16px; border-block: 1px solid var(--jz-border); }
.base-heading > span, .cache-actual { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.base-details summary { font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.cache-budget-row { margin-top: 20px; padding: 0 0 4px 16px; border-left: 2px solid var(--jz-border-strong); }
.cache-actual { margin: 0; }
.cache-actual strong { color: var(--jz-text); font-weight: 500; }
.capacity-note { margin-top: 12px; }
.capacity-actions { padding: 8px 0 0; flex-wrap: wrap; }
@media (max-width: 700px) {
  .budget-legend { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
  .budget-legend li { flex-wrap: wrap; }
  .budget-legend li:not(.is-editable) > strong { width: 100%; margin-left: 17px; }
  .budget-quota-trigger { width: 100%; }
  .cache-budget-row { padding-left: 12px; }
}
</style>
