<template>
  <section class="assistant-change" aria-label="配置建议" :class="{ finished }">
    <template v-if="finished">
      <strong role="status"><AppIcon v-if="saved" name="check-circle" :size="16" />{{ finished }}</strong>
      <SettingsAssistantLink v-if="saved" :to="settingsTarget" :after-navigation="afterNavigation">{{ matching ? '查看已保存规则' : '查看显示设置' }}</SettingsAssistantLink>
    </template>
    <template v-else>
      <h4><AppIcon name="settings" :size="16" />{{ matching ? '视频库匹配规则' : displayField?.label || '显示偏好' }}</h4>
      <template v-if="matching">
        <label for="assistant-library">目标视频库</label>
        <select id="assistant-library" v-model="selectedId" :disabled="working || !libraries.length">
          <option :value="null" disabled>选择要修改的视频库</option>
          <option v-for="item in libraries" :key="item.id" :value="item.id">{{ libraryLabel(item) }}</option>
        </select>
        <p v-if="librariesError" role="alert">视频库加载失败。<JzButton :disabled="working" @click="review">重新加载</JzButton></p>
        <p v-else-if="!librariesReady" role="status">正在加载视频库…</p>
        <p v-else-if="!libraries.length">请先在“媒体库连接”添加视频库。</p>
      </template>
      <template v-if="preview">
        <p class="hint">{{ matching ? '此规则仅对所选视频库生效。' : '仅在当前浏览器生效。' }}</p>
        <dl><dt>当前值</dt><dd>{{ valueLabel(preview.before) }}</dd>
          <dt>建议值</dt><dd>{{ valueLabel(preview.after) }}</dd></dl>
        <p v-if="hasDraft" role="status">此视频库有未保存的匹配规则，请先在原表单处理草稿。</p>
        <p v-else-if="unchanged" role="status">当前已经是这个设置。</p>
      </template>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="assistant-actions">
        <JzButton v-if="needsReview" :loading="working" @click="review">重新核对</JzButton>
        <JzButton v-else-if="preview && !unchanged && !hasDraft" variant="primary" :loading="working" :disabled="matching && rulesBusy" @click="apply">确认应用</JzButton>
        <SettingsAssistantLink v-if="hasDraft" :to="settingsTarget" :after-navigation="afterNavigation">查看本库草稿</SettingsAssistantLink>
        <JzButton variant="ghost" :disabled="working" @click="finished = '已取消修改'">取消</JzButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { api } from '../api.js'
import { DISPLAY_FIELDS, loadPrefs, savePrefs } from '../prefs.js'
import { CHAIN_LABELS, CHAIN_PROVIDERS, librarySourceOrder } from '../useMatchingSettings.js'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import SettingsAssistantLink from './SettingsAssistantLink.vue'

const props = defineProps({
  suggestion: { type: Object, required: true }, libraries: { type: Array, required: true },
  afterNavigation: { type: Function, required: true },
  librariesReady: Boolean, librariesError: String, refreshLibraries: { type: Function, required: true },
  dirtyLibraries: { type: Array, required: true }, rulesBusy: Boolean,
})
const emit = defineEmits(['busy', 'rules-saved', 'display-saved'])
const matching = computed(() => props.suggestion.kind === 'matching')
const displayField = computed(() => Object.hasOwn(DISPLAY_FIELDS, props.suggestion.field) ? DISPLAY_FIELDS[props.suggestion.field] : null)
const selectedId = ref(null), preview = ref(null), error = ref(''), finished = ref('')
const working = ref(false), needsReview = ref(false), saved = ref(false)
const selected = computed(() => props.libraries.find(item => item.id === selectedId.value))
const hasDraft = computed(() => matching.value && props.dirtyLibraries.some(item => item.id === selectedId.value))
const settingsTarget = computed(() => ({ path: '/settings', query: matching.value
  ? { sec: 'sec-matching', library: selectedId.value } : { sec: 'sec-display' } }))
const unchanged = computed(() => preview.value && JSON.stringify(preview.value.before) === JSON.stringify(preview.value.after))
function libraryLabel(item) {
  return [item.media_name, item.name, item.kind === 'tv' ? '剧集' : '电影'].filter(Boolean).join(' · ')
}
function valueLabel(value) {
  if (matching.value) return value.map(name => CHAIN_LABELS[name]).join(' → ')
  return displayField.value.options?.[value] || value + ' px'
}
function prepare() {
  preview.value = null
  error.value = ''
  needsReview.value = false
  const { field, value, delta, operation, providers } = props.suggestion
  if (matching.value) {
    if (!selected.value) return
    const before = librarySourceOrder(selected.value)
    if (Array.isArray(providers) && providers.length && new Set(providers).size === providers.length &&
      providers.every(name => CHAIN_PROVIDERS.includes(name))) {
      let after
      switch (operation) {
        case 'set': after = [...providers]; break
        case 'prioritize': after = [...providers, ...before.filter(name => !providers.includes(name))]; break
        case 'enable': after = [...new Set([...before, ...providers])]; break
        case 'disable': after = before.filter(name => !providers.includes(name)); break
      }
      if (after) {
        if (after.length) preview.value = { before, after }
        else error.value = '至少保留一个匹配来源。'
        return
      }
    }
  } else if (props.suggestion.kind === 'display' && displayField.value) {
    const before = loadPrefs()[field]
    const spec = displayField.value
    const after = value ?? (Number.isInteger(delta) ? before + delta : null)
    if (spec.options ? Object.hasOwn(spec.options, after) :
      Number.isInteger(after) && after >= spec.min && after <= spec.max && (after - spec.min) % spec.step === 0) {
      preview.value = { before, after }
      return
    }
    if (!spec.options) {
      error.value = '该建议超出可选范围，请指定 ' + spec.min + ' 到 ' + spec.max + ' px 之间的值，步长为 ' + spec.step + ' px。'
      return
    }
  }
  error.value = '建议值无效，请重新描述修改要求。'
}

// Hold the values actually shown for confirmation across background list refreshes.
let offeredTargets = false
watch(() => props.libraries.map(item => item.id), ids => {
  if (!matching.value || saved.value || working.value) return
  if (!offeredTargets && ids.length) {
    offeredTargets = true
    selectedId.value = ids.length === 1 ? ids[0] : null
  } else if (!ids.includes(selectedId.value)) selectedId.value = null
}, { immediate: true })
watch(selectedId, prepare, { immediate: true, flush: 'sync' })
function setWorking(value) { working.value = value; emit('busy', value) }
async function review() {
  if (working.value) return
  setWorking(true)
  try {
    if (matching.value) await props.refreshLibraries()
    await nextTick()
    if (matching.value && !selected.value) selectedId.value = null
    prepare()
  } catch (e) { error.value = '读取当前设置失败，可重新核对。' + e.message }
  finally { setWorking(false) }
}
async function apply() {
  if (working.value || saved.value || finished.value || !preview.value || unchanged.value || hasDraft.value || needsReview.value) return
  const { before, after } = preview.value
  const { field } = props.suggestion
  setWorking(true)
  error.value = ''
  try {
    if (matching.value) {
      if (!selected.value || props.rulesBusy) return
      const id = selectedId.value
      const result = await api('/api/libraries/' + id + '/matching-rules', {
        method: 'PUT', body: JSON.stringify({ expected: before, providers: after }),
      })
      emit('rules-saved', id, result.providers)
      finished.value = '已保存匹配规则'
    } else {
      const prefs = loadPrefs()
      if (prefs[field] !== before) {
        needsReview.value = true
        error.value = '显示偏好已变化，请重新核对当前值。'
        return
      }
      prefs[field] = after
      savePrefs(prefs)
      emit('display-saved', prefs)
      finished.value = '已保存到当前浏览器'
    }
    saved.value = true
  } catch (e) {
    if (e.status === 409 || e.status === 404) {
      needsReview.value = true
      error.value = '目标视频库或匹配规则已变化，请重新核对。'
    } else error.value = '应用失败。' + e.message
  } finally { setWorking(false) }
}
</script>

<style src="../styles/assistant-change.css"></style>
