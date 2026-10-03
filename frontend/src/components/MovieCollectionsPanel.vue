<template><section class="card-block movie-collections">
        <h3>所属合集</h3>
        <div class="bar">
          <select v-model="joinColId" style="flex:1">
            <option value="">选择合集…</option>
            <option v-for="c in colList" :key="c.id" :value="c.id">{{ c.name }}（{{ c.member_count }}）</option>
          </select>
          <JzButton @click="joinCol" :disabled="!joinColId" type="button" icon="plus">加入</JzButton>
        </div>
        <div class="bar">
          <input v-model="newColName" placeholder="新建合集名（含本片）" style="flex:1" />
          <JzButton @click="createCol" :disabled="!newColName.trim()" type="button" icon="plus">创建</JzButton>
          <span>{{ colMsg }}</span>
        </div>
<JzButton @click="emit('close')" type="button">收起</JzButton></section></template>
<script setup>
import JzButton from './JzButton.vue'

import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { listLibs, currentMediaId } from '../libraries.js'
const props = defineProps({ movieId: { type: Number, required: true }, libraryId: { type: Number, required: true } })
const emit = defineEmits(['changed', 'close'])
const mediaId = computed(() => listLibs().find(l => l.id === props.libraryId)?.media_library_id || currentMediaId())
const colList = ref([])
const joinColId = ref('')
const newColName = ref('')
const colMsg = ref('')
async function refreshCollections() {
  try {
    colList.value = (await api('/api/collections?media_library=' + mediaId.value)).items || []
  } catch (e) { colMsg.value = '合集加载失败：' + e.message }
}
async function joinCol() {
  if (!joinColId.value) return
  colMsg.value = ''
  try {
    await api(`/api/collections/${joinColId.value}/members`, {
      method: 'POST',
      body: JSON.stringify({ movie_ids: [props.movieId] })
    })
    colMsg.value = '已加入'
    joinColId.value = ''
    await refreshCollections()
    emit('changed')
  } catch (e) {
    colMsg.value = '加入失败：' + e.message
  }
}
async function createCol() {
  const name = newColName.value.trim()
  if (!name) return
  colMsg.value = ''
  try {
    await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name, member_ids: [props.movieId], media_library_id: mediaId.value })
    })
    colMsg.value = '已创建'
    newColName.value = ''
    await refreshCollections()
    emit('changed')
  } catch (e) {
    colMsg.value = '创建失败：' + e.message
  }
}
onMounted(refreshCollections)
</script>
<style scoped>
.bar { flex-wrap: wrap; padding: 6px 0; }
input, select { min-width: 0; max-width: 100%; }
</style>
