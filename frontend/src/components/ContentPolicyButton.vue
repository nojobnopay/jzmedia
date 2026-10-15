<template>
  <JzButton v-if="!isChildren" variant="ghost" icon="lock" :size="compact ? 'compact' : 'default'" :loading="opening" @click="open">{{ compact ? '可见性' : '内容可见性…' }}</JzButton>
  <JzDialog v-if="opened" :title="ids.length > 1 ? `内容可见性（${ids.length} 部）` : '内容可见性'" size="small" :busy="busy" @close="opened = false">
    <form :id="formId" class="policy-fields" @submit.prevent="save">
      <p class="hint policy-scope">{{ kind === 'show' ? '作用于整部剧，包括以后添加的季和分集。' : '作用于当前视频库中同一影片的全部版本。' }}</p>
      <fieldset :disabled="busy">
        <legend>展示范围</legend>
        <div class="policy-choices">
          <label v-for="option in rangeOptions" :key="option.value" class="policy-choice" :class="{ 'is-selected': privateChoice === option.value }">
            <input v-model="privateChoice" type="radio" :name="`${formId}-range`" :value="option.value" :aria-label="option.label" :aria-describedby="`${formId}-range-${option.value}`">
            <span class="choice-copy"><strong>{{ option.label }}</strong><span :id="`${formId}-range-${option.value}`" class="hint">{{ option.hint }}</span></span>
          </label>
        </div>
      </fieldset>
      <fieldset v-if="privateChoice === 'normal'" :disabled="busy">
        <legend>儿童模式是否显示</legend>
        <div class="policy-choices">
          <label v-for="option in childrenOptions" :key="option.value" class="policy-choice" :class="{ 'is-selected': childrenChoice === option.value }">
            <input v-model="childrenChoice" type="radio" :name="`${formId}-children`" :value="option.value" :aria-label="option.label" :aria-describedby="`${formId}-children-${option.value}`">
            <span class="choice-copy"><strong>{{ option.label }}</strong><span :id="`${formId}-children-${option.value}`" class="hint">{{ option.hint }}</span></span>
          </label>
        </div>
        <p v-if="policy?.children_reason" class="hint policy-reason">自动判断依据：{{ policy.children_reason }}</p>
      </fieldset>
      <p v-else-if="privateChoice === 'private'" class="hint policy-explanation">隐私内容不在普通或儿童模式显示。儿童设置会保留，移回普通模式时可再调整。</p>
      <p v-else class="hint policy-explanation">保留每部影片当前的普通／隐私范围和儿童设置。选择“普通模式可见”后，可统一调整儿童展示。</p>
      <p v-if="error" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <div class="policy-footer">
        <section class="policy-preview" aria-label="保存后可见范围" aria-live="polite" aria-atomic="true">
          <h3>保存后</h3>
          <dl><div v-for="row in preview" :key="row.mode" :data-policy-mode="row.mode" :class="`preview-${row.state}`"><dt>{{ row.label }}<span v-if="row.mode === 'private'" class="hint">（需密码）</span></dt><dd>{{ row.value }}</dd></div></dl>
        </section>
        <div class="policy-actions"><JzButton :disabled="busy" @click="opened = false">取消</JzButton><JzButton type="submit" :form="formId" variant="primary" :disabled="!canSave" :loading="busy">保存</JzButton></div>
      </div>
    </template>
  </JzDialog>
</template>
<script setup>
import { ref, computed, getCurrentInstance } from 'vue'
import { useRouter } from 'vue-router'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import { api } from '../api.js'
import { browseReturn, clearBrowse } from '../browseHistory.js'
import { currentMediaId } from '../libraries.js'
import { viewing, isChildren, ensureManagement } from '../viewing.js'
import { visibilityPatch, visibilityPreview } from '../contentVisibility.js'
const props = defineProps({ kind: { type: String, required: true }, ids: { type: Array, required: true }, compact: Boolean })
const emit = defineEmits(['saved'])
const router = useRouter()
const opened = ref(false)
const opening = ref(false)
const busy = ref(false)
const error = ref('')
const policy = ref(null)
const privateChoice = ref('keep')
const childrenChoice = ref('keep')
const formId = `content-visibility-${getCurrentInstance().uid}`
const isBatch = computed(() => props.ids.length > 1)
const rangeOptions = computed(() => [
  ...(isBatch.value ? [{ value: 'keep', label: '保持各自展示范围', hint: '保留每部影片当前的普通／隐私范围。' }] : []),
  { value: 'normal', label: '普通模式可见', hint: '普通模式显示，隐私模式隐藏；儿童模式在下方设置。' },
  { value: 'private', label: '仅隐私模式可见', hint: '需要密码进入；普通和儿童模式均隐藏。' },
])
const autoHint = computed(() => {
  if (isBatch.value) return '逐部按分级与风险资料判断，资料不足时不显示。'
  return { allow: '当前会在普通和儿童模式显示。', block: '当前只在普通模式显示。',
    unknown: '资料不足，当前只在普通模式显示。' }[policy.value?.children_auto_status] || '资料不足，当前只在普通模式显示。'
})
const childrenOptions = computed(() => [
  ...(isBatch.value ? [{ value: 'keep', label: '保持各自儿童设置', hint: '保留原有儿童设置；移回普通后按各自设置生效。' }] : []),
  { value: 'auto', label: '自动筛选', hint: autoHint.value },
  { value: 'allow', label: '普通和儿童模式都显示', hint: '我已确认适合儿童观看。' },
  { value: 'block', label: '只在普通模式显示', hint: '儿童模式隐藏。' },
])
const preview = computed(() => visibilityPreview(privateChoice.value, childrenChoice.value, policy.value))
const canSave = computed(() => {
  if (privateChoice.value === 'keep') return false
  if (isBatch.value) return true
  if (!policy.value) return false
  if ((privateChoice.value === 'private') !== !!policy.value.is_private) return true
  return privateChoice.value === 'normal' && childrenChoice.value !== policy.value.children_override
})
async function open() {
  opening.value = true
  try {
    if (!(await ensureManagement())) return
    error.value = ''
    policy.value = props.ids.length === 1 ? await api(`/api/viewing/policy?kind=${props.kind}&id=${props.ids[0]}`) : null
    privateChoice.value = policy.value ? policy.value.is_private ? 'private' : 'normal' : 'keep'
    childrenChoice.value = policy.value?.children_override || (props.ids.length > 1 ? 'keep' : 'auto')
    opened.value = true
  } catch (e) { viewing.notice = e.message }
  finally { opening.value = false }
}
async function save() {
  if (busy.value || !canSave.value) return
  busy.value = true
  error.value = ''
  const returnTarget = browseReturn(props.kind === 'show' ? '/tv' : '/', currentMediaId())
  try {
    const body = visibilityPatch(props.kind, props.ids, privateChoice.value, childrenChoice.value)
    if (Object.keys(body).length > 2) await api('/api/viewing/policy', { method: 'PATCH', body: JSON.stringify(body) })
    opened.value = false
    clearBrowse()
    const noLongerVisible = body.is_private != null && body.is_private !== (viewing.mode === 'private')
    if (noLongerVisible && !['/', '/tv'].includes(router.currentRoute.value.path)) {
      await router.replace(returnTarget)
      clearBrowse()
    } else emit('saved')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
.policy-fields { display: grid; gap: 20px; }
.policy-fields fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.policy-fields legend { margin-bottom: 8px; padding: 0; font-size: var(--jz-font-m); font-weight: 600; }
.policy-scope, .policy-explanation { margin: 0; }
.policy-choices { display: grid; gap: 8px; }
.policy-choice { display: flex; align-items: center; gap: 12px; min-height: 52px; padding: 10px 12px; box-sizing: border-box; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); cursor: pointer; }
.policy-choice.is-selected { border-color: var(--jz-accent); background: var(--jz-surface-3); }
.policy-choice:focus-within { outline: 2px solid var(--jz-link); outline-offset: 2px; }
.policy-choice input { width: 18px; height: 18px; flex: 0 0 18px; margin: 0; }
.choice-copy { display: grid; gap: 2px; min-width: 0; line-height: 1.5; }
.choice-copy strong { font-size: var(--jz-font-m); font-weight: 500; }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.5; }
.policy-reason { margin: 8px 0 0; }
.policy-footer { display: grid; gap: 14px; width: 100%; }
.policy-preview h3 { margin: 0 0 6px; font-size: var(--jz-font-m); font-weight: 600; }
.policy-preview dl { display: grid; gap: 4px; margin: 0; font-size: var(--jz-font-s); }
.policy-preview dl > div { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }
.policy-preview dt, .policy-preview dd { margin: 0; }
.policy-preview dd { text-align: right; }
.preview-shown dd { font-weight: 600; }
.preview-hidden dd, .preview-kept dd, .preview-conditional dd { color: var(--jz-text-dim); }
.policy-actions { display: flex; justify-content: flex-end; gap: 10px; }
@media (max-width: 600px) {
  .policy-actions :deep(.jz-button--primary) { flex: 1; }
}
</style>
