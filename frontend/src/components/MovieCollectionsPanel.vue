<template><section class="card-block movie-collections">
        <h3>所属合集</h3>
        <div class="bar">
          <select v-model="joinColId" :disabled="!!busy" style="flex:1">
            <option value="">选择合集…</option>
            <option v-for="c in colList" :key="c.id" :value="c.id">{{ c.name }}（{{ c.member_count }}）</option>
          </select>
          <JzButton @click="joinCol" :disabled="!joinColId || !!busy" :loading="busy === 'join'" type="button" icon="plus">加入</JzButton>
        </div>
        <div class="bar">
          <input v-model="newColName" :disabled="!!busy" placeholder="新建合集名（含本片）" style="flex:1" />
          <JzButton @click="createCol" :disabled="!newColName.trim() || !!busy" :loading="busy === 'create'" type="button" icon="plus">创建</JzButton>
          <span>{{ colMsg }}</span>
        </div>
<JzButton @click="emit('close')" type="button">收起</JzButton></section></template>
<script setup>
import JzButton from './JzButton.vue'

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { listLibs, currentMediaId } from '../libraries.js'
const props = defineProps({ movieId: { type: Number, required: true }, libraryId: { type: Number, required: true } })
const emit = defineEmits(['changed', 'close'])
const mediaId = computed(() => listLibs().find(l => l.id === props.libraryId)?.media_library_id || currentMediaId())
const colList = ref([])
const joinColId = ref('')
const newColName = ref('')
const colMsg = ref('')
const busy = ref('')
let disposed = false
let readSeq = 0
let controller = null
function context() { return { movieId: props.movieId, libraryId: props.libraryId, mediaId: mediaId.value } }
function current(value) {
  return !disposed && value.movieId === props.movieId && value.libraryId === props.libraryId && value.mediaId === mediaId.value
}
async function refreshCollections(value = context()) {
  if (!current(value)) return
  const seq = ++readSeq
  controller?.abort()
  controller = new AbortController()
  try {
    const data = await api('/api/collections?media_library=' + value.mediaId, { signal: controller.signal })
    if (current(value) && seq === readSeq) colList.value = data.items || []
  } catch (e) { if (current(value) && seq === readSeq) colMsg.value = '合集加载失败：' + e.message }
}
async function joinCol() {
  const value = context()
  const id = Number(joinColId.value)
  if (!current(value) || busy.value || !colList.value.some(collection => Number(collection.id) === id)) return
  busy.value = 'join'
  colMsg.value = ''
  try {
    await api(`/api/collections/${id}/members`, {
      method: 'POST',
      body: JSON.stringify({ movie_ids: [value.movieId] })
    })
    if (!current(value)) return
    colMsg.value = '已加入'
    joinColId.value = ''
    await refreshCollections(value)
    if (current(value)) emit('changed')
  } catch (e) {
    if (current(value)) colMsg.value = '加入失败：' + e.message
  } finally { if (current(value)) busy.value = '' }
}
async function createCol() {
  const value = context()
  const name = newColName.value.trim()
  if (!name || !current(value) || busy.value) return
  busy.value = 'create'
  colMsg.value = ''
  try {
    await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name, member_ids: [value.movieId], media_library_id: value.mediaId })
    })
    if (!current(value)) return
    colMsg.value = '已创建'
    newColName.value = ''
    await refreshCollections(value)
    if (current(value)) emit('changed')
  } catch (e) {
    if (current(value)) colMsg.value = '创建失败：' + e.message
  } finally { if (current(value)) busy.value = '' }
}
onMounted(() => refreshCollections())
onUnmounted(() => { disposed = true; readSeq++; controller?.abort() })
</script>
<style scoped>
.bar { flex-wrap: wrap; padding: 6px 0; }
input, select { min-width: 0; max-width: 100%; }
</style>
