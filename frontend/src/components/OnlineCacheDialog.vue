<template>
  <JzDialog title="缓存到本地" size="large" :busy="busy" :focus-active="!viewing.prompt" @close="$emit('close')">
    <div class="online-cache-picker" data-online-cache-dialog>
      <h3>{{ detail.item.title }} · {{ line.label }}</h3>
      <p class="online-scope">保存到服务器磁盘，关闭网页后继续缓存。整剧仅包含当前线路现有的集数。</p>
      <p v-if="error" class="online-cache-error" role="alert">{{ error }}</p>
      <div class="online-cache-tools">
        <JzButton :disabled="busy || !eligible.length" @click="selectAll">选择整剧</JzButton>
        <JzButton :disabled="busy || !selected.length" @click="selected = []">清空选择</JzButton>
        <JzField v-if="groups.length > 1" id="online-cache-group" label="条目范围"><template #default="field"><select :id="field.id" v-model="groupIndex" :disabled="busy" data-online-cache-group><option v-for="group in groups" :key="group.index" :value="group.index">{{ group.label }}</option></select></template></JzField>
      </div>
      <p role="status" data-online-cache-selection>已选 {{ selected.length }} 集；已有缓存或任务会自动跳过。</p>
      <div class="online-cache-selection-grid">
        <label v-for="row in visible" :key="row.index" class="online-cache-option" :class="{ 'online-cache-option--disabled': !canSelect(row.entry) }">
          <input v-model="selected" type="checkbox" :value="row.entry.identity?.episode_id" :disabled="busy || !canSelect(row.entry)" :data-online-cache-select="row.index" />
          <span>{{ row.entry.label }}<small v-if="existing(row.entry)">{{ onlineCacheState(existing(row.entry).state) }}</small><small v-else-if="!canSelect(row.entry)">此集暂不可缓存</small></span>
        </label>
      </div>
    </div>
    <template #footer><JzButton :disabled="busy" @click="$emit('close')">取消</JzButton><JzButton variant="primary" icon="download" :loading="busy" :disabled="busy || !selected.length" data-online-cache-submit @click="submit">缓存所选 {{ selected.length }} 集</JzButton></template>
  </JzDialog>
</template>
<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { ensureManagement, refreshViewing, viewing } from '../viewing.js'
import { createOnlineCacheClient, onlineCachedEntry, onlineCacheError, onlineCacheState } from '../onlineCache.js'
import { onlineEpisodeGroups, onlineEntryPlayable } from '../onlineEpisodes.js'
import JzDialog from './JzDialog.vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import '../styles/onlineCache.css'
const props = defineProps({ detail: { type: Object, required: true }, line: { type: Object, required: true }, entries: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'created'])
const busy = ref(false), error = ref(''), selected = ref([]), groupIndex = ref(0)
let disposed = false
const client = createOnlineCacheClient({ authorize: async () => {
  await refreshViewing()
  return !disposed && (!viewing.enabled || viewing.management_unlocked || await ensureManagement())
} })
const groups = computed(() => onlineEpisodeGroups(props.line.entries))
const visible = computed(() => groups.value.find(group => group.index === Number(groupIndex.value))?.entries || [])
function existing(entry) { return onlineCachedEntry(props.entries, entry.identity) }
function canSelect(entry) { return onlineEntryPlayable(entry, props.detail.item.id, props.line.id) && !existing(entry) }
const eligible = computed(() => props.line.entries.filter(canSelect))
function selectAll() { selected.value = eligible.value.map(entry => entry.identity.episode_id) }
async function submit() {
  if (busy.value) return
  const ids = new Set(eligible.value.map(entry => entry.identity.episode_id))
  const episodes = selected.value.filter(id => ids.has(id))
  if (!episodes.length) return
  busy.value = true; error.value = ''
  try {
    const result = await client.enqueue({ source_id: props.detail.source.id, item_id: props.detail.item.id, line_id: props.line.id, episode_ids: episodes })
    if (!disposed && result) emit('created', result)
  } catch (problem) { if (!disposed) error.value = onlineCacheError(problem) }
  finally { if (!disposed) busy.value = false }
}
onUnmounted(() => { disposed = true; client.close() })
</script>
