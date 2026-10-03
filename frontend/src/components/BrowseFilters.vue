<template>
  <div class="browse-filter-triggers">
    <JzButton v-for="item in quickGroups" :key="item.key"
      :class="['filter-trigger', { 'filter-trigger--secondary': item.secondary, 'filter-trigger--selected': hasGroup(item.key) }]"
      :aria-label="item.label" aria-haspopup="dialog" :aria-expanded="mode === 'quick' && activeGroup === item.key"
      :aria-controls="dialogId" :data-filter-trigger="item.key" @click="start('quick', item.key, $event)">
      {{ item.label }}<AppIcon name="chevron-down" :size="13" />
    </JzButton>
    <JzButton ref="allTrigger" class="filter-trigger filter-trigger--all" :class="{ 'filter-trigger--selected': activeCount > 0 }"
      :aria-label="activeCount ? '全部筛选，已选 ' + activeCount + ' 项' : '全部筛选'" aria-haspopup="dialog" :aria-expanded="mode === 'drawer'" :aria-controls="dialogId"
      data-filter-trigger="all" @click="start('drawer', 'genres', $event)">
      <AppIcon name="filter" :size="18" />
      全部筛选<span v-if="activeCount" class="filter-trigger-count" aria-hidden="true">{{ activeCount }}</span>
    </JzButton>
  </div>
  <JzDialog :id="dialogId" :open="!!mode" :title="dialogTitle" :presentation="mode === 'drawer' ? 'drawer' : 'popover'"
    :style="mode === 'quick' ? anchorStyle : undefined" class="browse-filters-dialog" close-label="取消并关闭筛选" @close="close">
    <template #header="{ titleId }">
      <h2 :id="titleId">{{ dialogTitle }}</h2>
      <p class="browse-filters-scope">{{ scopeLabel || '当前媒体库' }}</p>
    </template>
    <div class="browse-filters-content">
      <div v-if="loading" class="browse-filters-feedback" role="status">正在加载本库筛选项…</div>
      <div v-else-if="error" class="browse-filters-feedback browse-filters-feedback--error" role="alert">
        <p>筛选项加载失败。已选条件仍可取消或重置。</p>
        <p class="browse-filters-error-detail">{{ error }}</p>
        <JzButton size="compact" @click="$emit('retry')">重试加载筛选项</JzButton>
      </div>
      <p v-else class="browse-filters-count-note">数字为本库数量，不随当前筛选条件变化。</p>
      <template v-if="mode === 'drawer'">
        <section v-for="group in groups" :key="group.key" class="browse-filter-group">
          <JzButton variant="ghost" class="browse-filter-group-toggle" :aria-expanded="activeGroup === group.key"
            :aria-controls="dialogId + '-' + group.key" :aria-label="(activeGroup === group.key ? '收起' : '展开') + group.label + '筛选'"
            :data-filter-group="group.key" @click="activeGroup = activeGroup === group.key ? '' : group.key">
            <span class="browse-filter-group-title"><strong>{{ group.label }}</strong><small>{{ filterGroupSummary(draft, group.key, facets, kind) }}</small></span>
            <AppIcon name="chevron-down" :size="15" />
          </JzButton>
          <div v-if="activeGroup === group.key" :id="dialogId + '-' + group.key" class="browse-filter-group-content">
            <BrowseFilterFields v-model="draft" :group="group.key" :kind="kind" :facets="facets" :loading="loading" :error="error" />
          </div>
        </section>
      </template>
      <BrowseFilterFields v-else-if="mode === 'quick'" v-model="draft" :group="activeGroup" :kind="kind" :facets="facets" :loading="loading" :error="error" />
    </div>
    <template #footer>
      <JzButton class="browse-filters-reset" :variant="mode === 'drawer' ? 'secondary' : 'ghost'" @click="resetDraft">
        {{ mode === 'drawer' ? '重置' : '清除此项' }}
      </JzButton>
      <JzButton class="browse-filters-apply" variant="primary" @click="apply">{{ mode === 'drawer' ? '应用筛选' : '应用' }}</JzButton>
    </template>
  </JzDialog>
</template>

<script setup>
import { computed, getCurrentInstance, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import BrowseFilterFields from './BrowseFilterFields.vue'
import { countBrowseFilters, defaultBrowseFilters, filterGroupSummary, normalizeBrowseFilters, resetFilterGroup } from '../browseFilters.js'

const props = defineProps({
  modelValue: { type: Object, required: true },
  facets: { type: Object, default: () => ({}) },
  kind: { type: String, default: 'movie' },
  scopeLabel: { type: String, default: '' },
  scopeKey: { type: [Number, String], default: null },
  contextKey: { type: String, default: '' },
  open: Boolean,
  loading: Boolean,
  error: { type: String, default: '' },
})
const emit = defineEmits(['apply', 'update:open', 'open', 'retry'])
const mode = ref('')
const activeGroup = ref('genres')
const draft = ref(defaultBrowseFilters(props.kind))
const anchorStyle = ref({})
const allTrigger = ref(null)
const dialogId = `browse-filters-${getCurrentInstance().uid}`
let trigger = null
const quickGroups = [
  { key: 'genres', label: '类型' }, { key: 'location', label: '地区', secondary: true },
  { key: 'period', label: '年代', secondary: true }, { key: 'watched', label: '观看' },
]
const groups = computed(() => [
  { key: 'genres', label: '类型' }, { key: 'location', label: '地区 / 国家' },
  { key: 'period', label: '年代 / 年份' }, { key: 'rating', label: '评分' },
  { key: 'watched', label: '观看状态' }, { key: 'tags', label: '标签' },
  ...(props.kind === 'tv' ? [{ key: 'status', label: '连载状态' }] : []),
])
const activeCount = computed(() => countBrowseFilters(props.modelValue, props.kind))
const dialogTitle = computed(() => mode.value === 'drawer' ? '全部筛选' : quickGroups.find(group => group.key === activeGroup.value)?.label || '筛选')
function hasGroup(group) { return filterGroupSummary(props.modelValue, group, props.facets, props.kind) !== '不限' }
function start(presentation, group, event) {
  trigger = event?.currentTarget || allTrigger.value?.$el || document.activeElement
  // Capture the visible trigger before the dialog's single focus trap activates.
  trigger?.focus?.()
  draft.value = normalizeBrowseFilters(props.modelValue, props.kind)
  activeGroup.value = group
  updateAnchor()
  mode.value = presentation
  emit('update:open', true)
  emit('open')
}
function close() {
  if (!mode.value) return
  mode.value = ''
  emit('update:open', false)
}
function apply() {
  const value = normalizeBrowseFilters(draft.value, props.kind)
  close()
  emit('apply', value)
}
function resetDraft() {
  draft.value = mode.value === 'drawer' ? defaultBrowseFilters(props.kind) : resetFilterGroup(draft.value, activeGroup.value, props.kind)
}
function updateAnchor() {
  const rect = trigger?.getBoundingClientRect?.()
  if (!rect) return
  const width = Math.min(380, window.innerWidth - 24)
  const below = window.innerHeight - rect.bottom - 24
  const top = below >= 240 ? rect.bottom + 8 : Math.max(12, rect.top - 460)
  anchorStyle.value = {
    '--jz-popover-left': `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
    '--jz-popover-top': `${top}px`,
    '--jz-popover-max-height': `${Math.max(160, window.innerHeight - top - 12)}px`,
  }
}
watch(() => props.open, value => {
  if (value && !mode.value) start('drawer', 'genres')
  else if (!value) close()
})
watch(() => [props.scopeKey, props.contextKey, props.kind, JSON.stringify(props.modelValue)], () => close())
onMounted(() => {
  window.addEventListener('resize', updateAnchor)
  if (props.open) start('drawer', 'genres')
})
onUnmounted(() => window.removeEventListener('resize', updateAnchor))
</script>

<style scoped>
.browse-filter-triggers { display: contents; }
.filter-trigger { flex: none; padding-inline: var(--jz-gap-m); background: transparent; white-space: nowrap; }
.filter-trigger--selected { border-color: var(--jz-danger-border); background: var(--jz-selected); }
.filter-trigger--all { background: var(--jz-surface-3); }
.filter-trigger-count { min-width: 18px; padding: 1px 4px; border-radius: 4px; background: var(--jz-text); color: var(--jz-bg); font-size: var(--jz-font-s); line-height: 1.3; }
.browse-filters-scope { margin: 6px 0 0; font-size: var(--jz-font-s); color: var(--jz-text-dim); overflow-wrap: anywhere; }
.browse-filters-count-note { color: var(--jz-text-dim); font-size: var(--jz-font-s); margin: 0 0 var(--jz-gap-l); }
.browse-filters-feedback { color: var(--jz-text-dim); font-size: var(--jz-font-m); margin-bottom: var(--jz-gap-l); }
.browse-filters-feedback p { margin: 0 0 var(--jz-gap-s); }
.browse-filters-feedback--error { padding: var(--jz-gap-m); border: 1px solid var(--jz-danger-border); border-radius: var(--jz-radius-s); background: var(--jz-danger-soft); }
.browse-filters-error-detail { color: var(--jz-danger); font-size: var(--jz-font-s); max-height: 90px; overflow: auto; }
.browse-filter-group { border-bottom: 1px solid var(--jz-border); }
.browse-filter-group:last-child { border-bottom: 0; }
.browse-filter-group-toggle { width: 100%; min-height: 76px; justify-content: space-between; text-align: left; padding: 16px 0; border: 0; border-radius: 0; }
.browse-filter-group-toggle:hover { background: transparent; }
.browse-filter-group-title { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.browse-filter-group-title strong { color: var(--jz-text); font-size: var(--jz-font-l); font-weight: 500; }
.browse-filter-group-title small { color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: 400; overflow-wrap: anywhere; }
.browse-filter-group-toggle[aria-expanded="true"] :deep(svg) { transform: rotate(180deg); }
.browse-filter-group-content { padding-bottom: var(--jz-gap-2xl); }
.browse-filters-reset { margin-right: auto; }
.browse-filters-apply { min-width: 96px; }
@media (max-width: 700px) {
  .filter-trigger--secondary { display: none; }
  .filter-trigger { padding-inline: var(--jz-gap-m); }
}
</style>

<style>
.browse-filters-dialog.jz-dialog--drawer .jz-dialog-body { padding-top: 16px; }
.browse-filters-dialog.jz-dialog--drawer .browse-filters-apply { flex: 1; }
.browse-filters-dialog.jz-dialog--popover .jz-dialog-header { padding: 16px 20px; }
.browse-filters-dialog.jz-dialog--popover .jz-dialog-body { padding: 18px 20px; }
.browse-filters-dialog.jz-dialog--popover .jz-dialog-footer { padding: 14px 20px; }
</style>
