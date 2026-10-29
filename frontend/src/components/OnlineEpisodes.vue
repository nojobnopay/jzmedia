<template>
  <section class="online-episode-picker" aria-label="选择播放条目" data-online-episodes>
    <header class="online-episode-heading">
      <h3>{{ kind === 'tv' ? '选集' : '播放版本' }}<span>{{ entries.length }} {{ kind === 'tv' ? '集' : '项' }}</span></h3>
      <div class="online-episode-tools">
        <JzField v-if="entries.length > 1" id="online-episode-order" label="排序"><template #default="field"><select :id="field.id" :value="order" :disabled="busy" data-online-entry-order @change="changeOrder($event.target.value)"><option value="asc">正序</option><option value="desc">倒序</option></select></template></JzField>
        <JzField v-if="groups.length > 1" id="online-episode-group" label="条目范围"><template #default="field"><select :id="field.id" :value="String(groupIndex)" :disabled="busy" data-online-entry-group @change="changeGroup($event.target.value)"><option v-for="group in groups" :key="group.index" :value="String(group.index)">{{ group.label }}</option></select></template></JzField>
        <JzButton v-if="lastIndex >= 0" :disabled="busy" data-online-locate-last @click="locateLast">定位上次观看</JzButton>
      </div>
    </header>
    <p v-if="groups.length > 1" class="online-episode-range-note">按来源条目顺序分组，每组最多 {{ groupSize }} 项。</p>
    <div v-if="visibleEntries.length" class="online-entries online-episode-grid" data-online-visible-entries>
      <div v-for="row in visibleEntries" :key="row.index" class="online-entry">
        <JzButton :ref="button => setEntryButton(row.index, button)" :data-online-entry="row.entry.label" :data-online-entry-index="row.index"
          :data-online-entry-current="row.index === currentIndex ? true : undefined" :data-online-entry-last="row.index === lastIndex ? true : undefined"
          :aria-current="row.index === currentIndex ? 'true' : undefined" :disabled="busy || !playable(row.entry)"
          :variant="row.index === currentIndex ? 'primary' : 'secondary'" @click="play(row.entry)">
          <span class="online-episode-label">{{ row.entry.label }}</span>
          <span v-if="row.index === currentIndex" class="online-episode-marker">正在播放</span>
          <span v-else-if="row.index === lastIndex" class="online-episode-marker">上次观看</span>
        </JzButton>
        <span v-if="!playable(row.entry)" class="online-episode-unsupported">{{ reasonLabel(row.entry.reason) }}</span>
      </div>
    </div>
    <p v-else class="online-episode-empty">当前线路暂无播放条目。</p>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import { ONLINE_EPISODE_GROUP_SIZE, onlineEpisodeGroups, onlineEntryIndex, onlineEntryPlayable, onlineEntryReason } from '../onlineEpisodes.js'
import '../styles/onlineEpisodes.css'

const props = defineProps({
  itemKey: { type: String, default: '' },
  lineId: { type: String, default: '' },
  kind: { type: String, default: 'tv' },
  entries: { type: Array, default: () => [] },
  busy: Boolean,
  currentIdentity: { type: Object, default: null },
  lastIdentity: { type: Object, default: null },
  reasonLabel: { type: Function, default: onlineEntryReason },
})
const emit = defineEmits(['play'])
const groupSize = ONLINE_EPISODE_GROUP_SIZE
const order = ref('asc'), groupIndex = ref(0)
const currentIndex = computed(() => onlineEntryIndex(props.entries, props.currentIdentity, props.itemKey, props.lineId))
const lastIndex = computed(() => onlineEntryIndex(props.entries, props.lastIdentity, props.itemKey, props.lineId))
const groups = computed(() => onlineEpisodeGroups(props.entries, order.value))
const visibleEntries = computed(() => groups.value.find(group => group.index === groupIndex.value)?.entries || [])
const buttons = new Map()
let interacted = false, locating = 0, disposed = false

function playable(entry) { return onlineEntryPlayable(entry, props.itemKey, props.lineId) }
function preferredGroup() {
  const index = currentIndex.value >= 0 ? currentIndex.value : lastIndex.value
  return index >= 0 ? Math.floor(index / groupSize) : 0
}
function reconcile() {
  if (!interacted) groupIndex.value = preferredGroup()
  else if (!groups.value.some(group => group.index === groupIndex.value)) groupIndex.value = groups.value[0]?.index || 0
}
watch(() => [props.itemKey, props.lineId], () => {
  locating++; interacted = false; order.value = 'asc'; groupIndex.value = preferredGroup()
}, { immediate: true })
watch(() => [props.entries, props.currentIdentity, props.lastIdentity], reconcile, { immediate: true })

function changeOrder(value) {
  if (props.busy || !['asc', 'desc'].includes(value)) return
  locating++; interacted = true; order.value = value
  groupIndex.value = groups.value[0]?.index || 0
}
function changeGroup(value) {
  if (props.busy || !/^(0|[1-9][0-9]*)$/.test(value)) return
  const index = Number(value)
  if (!groups.value.some(group => group.index === index)) return
  locating++; interacted = true; groupIndex.value = index
}
function setEntryButton(index, button) {
  if (button) buttons.set(index, button)
  else buttons.delete(index)
}
async function locateLast() {
  if (props.busy || lastIndex.value < 0) return false
  const ticket = ++locating, index = lastIndex.value
  const item = props.itemKey, line = props.lineId, identity = props.lastIdentity
  interacted = true; groupIndex.value = Math.floor(index / groupSize)
  await nextTick()
  // An already-visible group may not schedule this component's own render.
  // Allow the caller's synchronous item/line change to enqueue its parent
  // update, then wait for that update before checking the exposed child props.
  await nextTick()
  if (disposed || locating !== ticket || props.busy || props.itemKey !== item || props.lineId !== line
    || props.lastIdentity !== identity || lastIndex.value !== index) return false
  const button = buttons.get(index)
  if (typeof button?.el?.focus === 'function') button.focus?.({ preventScroll: true })
  button?.el?.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'instant' })
  return true
}
function play(entry) { if (!props.busy && playable(entry)) emit('play', entry) }
onBeforeUnmount(() => { disposed = true; locating++; buttons.clear() })
defineExpose({ locateLast })
</script>
