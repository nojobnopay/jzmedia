<template>
  <section class="online-cache" aria-label="本地缓存" data-online-cache-view>
    <JzButton variant="ghost" icon="arrow-left" @click="$emit('back')">返回在线浏览</JzButton>
    <header class="online-cache-heading"><div><h1 ref="title" tabindex="-1">本地缓存</h1><p class="online-scope">服务器磁盘 · 家庭共享；完整缓存可直接播放。</p></div><JzButton icon="refresh" :loading="loading" @click="load">刷新缓存</JzButton></header>
    <p v-if="loaded && !enabled" class="online-scope" role="status" data-online-cache-disabled>在线点播已关闭，可删除缓存；重新启用后观看。</p>
    <p v-if="storage" class="online-cache-storage" data-online-cache-storage>已用 {{ onlineCacheBytes(storage.used_bytes) }} · 磁盘可用 {{ onlineCacheBytes(storage.available_bytes) }} · 保留空间 {{ onlineCacheBytes(storage.min_free_bytes) }}<span v-if="!storage.ok">；{{ onlineCacheError(storage.error_code) }}</span><span v-else-if="storage.available_bytes < storage.min_free_bytes">；可用空间不足，请清理缓存。</span></p>
    <p v-if="error" class="online-cache-error" role="alert" data-online-cache-error>{{ error }}</p>
    <OnlineSkeleton v-if="loading && !loaded" variant="records" label="正在读取本地缓存…" />
    <EmptyState v-else-if="!loaded && error" state="error" title="本地缓存读取失败" :text="error" retry @retry="load" />
    <EmptyState v-else-if="!items.length" state="empty" title="还没有本地缓存" :text="enabled ? '打开在线剧集详情，选择一集、多集或整剧缓存。' : '在线点播已关闭，重新启用后可添加缓存。'"><JzButton @click="$emit('back')">浏览在线剧集</JzButton></EmptyState>
    <template v-else>
      <div class="online-cache-tools"><JzButton :disabled="busy || !selected.length" icon="trash" @click="confirmDelete(selected)">删除所选 {{ selected.length }} 集</JzButton><JzButton :disabled="busy" @click="selectAll">全选</JzButton><JzButton :disabled="busy || !selected.length" @click="selected = []">清空选择</JzButton></div>
      <div class="online-cache-items" :aria-busy="loading">
        <article v-for="item in items" :key="item.item_id" class="online-cache-item" :data-online-cache-item="item.item_id">
          <header class="online-cache-item-heading">
            <OnlineArtwork :path="item.poster_path || ''" :title="item.title" />
            <div class="online-cache-item-summary"><h2>{{ item.title }}</h2><p>{{ item.source_name }} · {{ item.ready_count }} / {{ item.total_count }} 集已缓存</p><JzButton icon="play" :data-online-cache-detail="item.item_id" @click="$emit('detail', item)">打开缓存剧集</JzButton></div>
            <div class="online-cache-tools"><JzButton icon="trash" :disabled="busy" :aria-label="'删除整剧缓存 ' + item.title" @click="confirmDelete(item.entries.map(entry => entry.id), item.title)">删除整剧</JzButton></div>
          </header>
          <JzField v-if="groups(item).length > 1" :id="'cache-group-' + item.item_id" label="缓存条目范围"><template #default="field"><select :id="field.id" :value="groupIndices[item.item_id] || 0" @change="changeGroup(item.item_id, $event.target.value)"><option v-for="group in groups(item)" :key="group.index" :value="group.index">{{ group.label }}</option></select></template></JzField>
          <div v-for="row in visible(item)" :key="row.entry.id" class="online-cache-entry" :data-online-cache-entry="row.entry.id">
            <label><input v-model="selected" type="checkbox" :value="row.entry.id" :disabled="busy || row.entry.state === 'deleting'" :aria-label="'选择 ' + item.title + ' ' + row.entry.label" /><span class="sr-only">选择</span></label>
            <div class="online-cache-entry-info"><p><strong>{{ row.entry.label }}</strong> · {{ row.entry.line_label }} · <span :data-online-cache-state="row.entry.state">{{ onlineCacheState(row.entry.state) }}</span></p>
              <progress v-if="row.entry.state === 'downloading' || row.entry.state === 'queued'" :value="onlineCacheProgress(row.entry) ?? undefined" max="1" :aria-label="row.entry.label + ' 缓存进度'" />
              <p class="online-cache-progress-text">{{ onlineCacheBytes(row.entry.downloaded_bytes) }}<span v-if="row.entry.total_bytes"> / {{ onlineCacheBytes(row.entry.total_bytes) }}</span><span v-if="row.entry.total_resources"> · {{ row.entry.completed_resources }} / {{ row.entry.total_resources }} 个资源</span><span v-if="row.entry.state === 'downloading'"> · {{ onlineCacheBytes(row.entry.speed_bytes_per_second || 0) }}/s</span></p>
              <p v-if="row.entry.error_code" class="online-cache-error">{{ onlineCacheError(row.entry.error_code) }}</p>
            </div>
            <div class="online-cache-entry-actions">
              <JzButton v-if="row.entry.state === 'ready'" icon="play" :disabled="busy || !enabled" :data-online-cache-play="row.entry.id" @click="$emit('detail', item, row.entry)">播放</JzButton>
              <JzButton v-if="['queued', 'downloading'].includes(row.entry.state)" :disabled="busy" @click="action(row.entry, 'pause')">暂停</JzButton>
              <JzButton v-if="row.entry.state === 'paused'" :disabled="busy || !enabled" @click="action(row.entry, 'resume')">继续缓存</JzButton>
              <JzButton v-if="row.entry.state === 'failed'" icon="refresh" :disabled="busy || !enabled" @click="action(row.entry, 'retry')">重试</JzButton>
              <JzButton icon="trash" :disabled="busy || row.entry.state === 'deleting'" :aria-label="'删除缓存 ' + row.entry.label" @click="confirmDelete([row.entry.id], item.title + ' · ' + row.entry.label)">删除</JzButton>
            </div>
          </div>
        </article>
      </div>
    </template>
    <JzDialog v-if="deleting" title="删除本地缓存" :busy="busy" :focus-active="!viewing.prompt" @close="deleting = null">
      <div class="online-cache-confirm"><p>{{ deleting.label ? '即将删除“' + deleting.label + '”的缓存。' : '即将删除所选的 ' + deleting.ids.length + ' 集缓存。' }}</p><p>共 {{ deleting.ids.length }} 集，占用 {{ onlineCacheBytes(deleting.bytes) }}。</p><p>删除服务器上的缓存文件并取消对应下载任务，同一服务器的其他设备也会失去这些缓存。删除正在播放的集数会停止相关设备的播放。观看记录会保留。</p><p v-if="deleteError" class="online-cache-error" role="alert">{{ deleteError }}</p></div>
      <template #footer><JzButton :disabled="busy" @click="deleting = null">保留缓存</JzButton><JzButton variant="danger" icon="trash" :loading="busy" :disabled="busy" data-online-cache-delete-confirm @click="remove">确认删除 {{ deleting.ids.length }} 集</JzButton></template>
    </JzDialog>
  </section>
</template>
<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ensureManagement, refreshViewing, viewing } from '../viewing.js'
import { createOnlineCacheClient, onlineCacheBytes, onlineCacheError, onlineCacheState, onlineCacheProgress } from '../onlineCache.js'
import { onlineEpisodeGroups } from '../onlineEpisodes.js'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import JzField from './JzField.vue'
import EmptyState from './EmptyState.vue'
import OnlineArtwork from './OnlineArtwork.vue'
import OnlineSkeleton from './OnlineSkeleton.vue'
import '../styles/onlineCache.css'
const props = defineProps({ active: { type: Boolean, default: true } })
const emit = defineEmits(['back', 'detail', 'changed', 'status'])
const items = ref([]), storage = ref(null), enabled = ref(true), loaded = ref(false), loading = ref(false), error = ref('')
const selected = ref([]), groupIndices = ref({}), busy = ref(false), deleting = ref(null), deleteError = ref(''), title = ref(null)
let disposed = false, timer = null
const client = createOnlineCacheClient({ authorize: async () => {
  await refreshViewing()
  return !disposed && (!viewing.enabled || viewing.management_unlocked || await ensureManagement())
} })
function groups(item) { return onlineEpisodeGroups(item.entries) }
function visible(item) { return groups(item).find(group => group.index === Number(groupIndices.value[item.item_id] || 0))?.entries || groups(item)[0]?.entries || [] }
function changeGroup(id, value) { groupIndices.value = { ...groupIndices.value, [id]: Number(value) } }
function selectAll() { selected.value = items.value.flatMap(item => item.entries.filter(entry => entry.state !== 'deleting').map(entry => entry.id)) }
function schedule() {
  clearTimeout(timer); timer = null
  if (!disposed && props.active && !error.value && !globalThis.document?.hidden && !deleting.value && items.value.some(item => item.entries.some(entry => ['queued', 'downloading', 'deleting'].includes(entry.state)))) timer = setTimeout(() => { void load() }, 2000)
}
async function load() {
  if (disposed || !props.active || loading.value) return
  loading.value = true; error.value = ''
  try {
    const value = await client.list()
    if (!value || disposed) return
    items.value = value.items; storage.value = value.storage; enabled.value = value.enabled !== false; loaded.value = true; emit('status', enabled.value)
    const ids = new Set(value.items.flatMap(item => item.entries.map(entry => entry.id)))
    selected.value = selected.value.filter(id => ids.has(id))
  } catch (problem) { if (!disposed) error.value = onlineCacheError(problem) }
  finally { if (!disposed) { loading.value = false; schedule() } }
}
async function action(entry, operation) {
  if (busy.value || !enabled.value && operation !== 'pause') return
  busy.value = true; error.value = ''
  try { const result = await client.action(entry.task_id || entry.id, operation); if (result && !disposed) { emit('changed'); await load() } }
  catch (problem) { if (!disposed) error.value = onlineCacheError(problem) }
  finally { if (!disposed) busy.value = false }
}
function deletionSize(ids) { const selectedIds = new Set(ids); return items.value.flatMap(item => item.entries).filter(entry => selectedIds.has(entry.id)).reduce((bytes, entry) => bytes + (Number.isFinite(entry.downloaded_bytes) && entry.downloaded_bytes >= 0 ? entry.downloaded_bytes : 0), 0) }
function confirmDelete(ids, label = '') { if (busy.value || !ids.length) return; clearTimeout(timer); deleting.value = { ids: [...ids], label, bytes: deletionSize(ids) }; deleteError.value = '' }
async function remove() {
  if (!deleting.value || busy.value) return
  const selection = deleting.value
  busy.value = true; deleteError.value = ''
  try {
    const result = await client.remove(selection.ids)
    if (!result || disposed) return
    const failures = result.results.filter(row => !row.deleted)
    await load(); emit('changed')
    if (failures.length) { const ids = failures.map(row => row.id); deleting.value = { ...selection, ids, bytes: deletionSize(ids) }; deleteError.value = failures.map(row => onlineCacheError(row.error_code)).filter((text, index, values) => values.indexOf(text) === index).join(' ') }
    else { deleting.value = null; await nextTick(); title.value?.focus?.({ preventScroll: true }) }
  } catch (problem) { if (!disposed) deleteError.value = onlineCacheError(problem) }
  finally { if (!disposed) { busy.value = false; schedule() } }
}
function visibilityChanged() { if (!globalThis.document?.hidden) void load(); else { clearTimeout(timer); client.cancel(); loading.value = false } }
watch(() => props.active, active => { clearTimeout(timer); if (active) void load(); else { client.cancel(); loading.value = false } })
watch(deleting, schedule)
onMounted(() => { void load(); globalThis.document?.addEventListener?.('visibilitychange', visibilityChanged) })
onUnmounted(() => { disposed = true; clearTimeout(timer); client.close(); globalThis.document?.removeEventListener?.('visibilitychange', visibilityChanged) })
defineExpose({ focus: () => title.value?.focus?.({ preventScroll: true }), refresh: load })
</script>
