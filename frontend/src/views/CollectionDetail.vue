<template>
  <EmptyState v-if="!c" :state="loadErr ? 'error' : 'loading'"
    :title="loadErr ? '合集加载失败' : '正在加载合集'" :text="loadErr || '请稍候…'" :retry="!!loadErr" @retry="load">
    <router-link v-if="loadErr" to="/collections">返回合集列表</router-link>
  </EmptyState>
  <div v-else class="page media-detail collection-detail">
    <header class="collection-heading">
      <router-link to="/collections" class="back-link">‹ 合集</router-link>
      <ActionMenu label="管理合集">
        <button @click="toggleEdit">{{ editing ? '结束编辑' : '编辑合集与成员' }}</button>
        <button @click="openDelete" :disabled="busy">删除合集</button>
      </ActionMenu>
    </header>
    <EmptyState v-if="loadErr" state="error" title="合集刷新失败" :text="loadErr" retry @retry="load" />
    <div class="collection-intro">
      <h1>{{ c.name }}</h1>
      <p class="collection-count">{{ c.member_count }} 部影片</p>
      <MediaOverview :text="c.overview || ''" :lines="2" />
    </div>
    <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
    <div v-if="editing" class="card-block">
      <div class="bar"><input v-model="f.name" aria-label="合集名称" placeholder="合集名" style="flex:1" /></div>
      <div class="bar"><textarea v-model="f.overview" aria-label="合集简介" placeholder="简介" rows="2" style="flex:1"></textarea></div>
      <div class="bar"><button @click="save">保存</button><button @click="editing = false">取消</button></div>
    </div>
    <div class="grid">
      <div v-for="m in c.members" :key="m.id" class="card">
        <router-link class="poster-wrap" :to="'/m/' + m.id">
          <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy" :alt="m.title || '海报'" />
          <div v-else class="no-poster" aria-hidden="true">{{ (m.title || '?').slice(0, 1) }}</div>
        </router-link>
        <div class="t">{{ m.title }} <span v-if="m.year">({{ m.year }})</span><span v-if="m.version_count > 1"> ×{{ m.version_count }}</span>
          <button v-if="editing" @click="kick(m.id)">移出合集</button>
        </div>
      </div>
    </div>
    <EmptyState v-if="!c.members.length" state="empty" title="合集中还没有影片" text="去海报墙多选影片后“加入合集”，或从影片详情页加入。">
      <router-link to="/">浏览电影</router-link>
    </EmptyState>
    <JzDialog :open="armDel" title="删除合集" size="small" :busy="busy" @close="armDel = false">
      <p>将删除合集「{{ c.name }}」（{{ c.member_count }} 部），影片仍会保留。此操作不可恢复。</p>
      <p v-if="deleteError" class="delete-error" role="alert">{{ deleteError }}</p>
      <template #footer>
        <JzButton :disabled="busy" @click="armDel = false">取消</JzButton>
        <JzButton variant="danger" :loading="busy" @click="removeCol">{{ busy ? '删除中…' : '确认删除' }}</JzButton>
      </template>
    </JzDialog>
  </div>
</template>
<script setup>
import EmptyState from '../components/EmptyState.vue'
import ActionMenu from '../components/ActionMenu.vue'
import MediaOverview from '../components/MediaOverview.vue'
import JzButton from '../components/JzButton.vue'
import JzDialog from '../components/JzDialog.vue'
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'

const route = useRoute()
const router = useRouter()
const c = ref(null)
const msg = ref('')
const editing = ref(false)
const loadErr = ref('')
let loadSeq = 0
const busy = ref(false)
const armDel = ref(false)
const deleteError = ref('')
const f = ref({ name: '', overview: '' })

async function load() {
  const seq = ++loadSeq
  const id = route.params.id
  if (c.value && String(c.value.id) !== String(id)) c.value = null
  loadErr.value = ''
  try {
    const data = await api('/api/collections/' + id)
    if (seq !== loadSeq) return
    c.value = data
    f.value = { name: c.value.name || '', overview: c.value.overview || '' }
  } catch (e) {
    if (seq !== loadSeq) return
    loadErr.value = e && /404/.test(String(e.message)) ? '合集不存在或已删除' : ('加载失败：' + e.message)
  }
}
function toggleEdit() {
  if (!editing.value && c.value) f.value = { name: c.value.name, overview: c.value.overview || '' }
  editing.value = !editing.value
}
async function save() {
  msg.value = ''
  try {
    c.value = await api('/api/collections/' + route.params.id, {
      method: 'PATCH',
      body: JSON.stringify({ name: f.value.name, overview: f.value.overview })
    })
    editing.value = false
  } catch (e) {
    msg.value = '保存失败：' + e.message
  }
}
async function kick(mid) {
  msg.value = ''
  try {
    await api(`/api/collections/${route.params.id}/members/remove`, {
      method: 'POST',
      body: JSON.stringify({ movie_ids: [mid] })
    })
    await load()
  } catch (e) {
    msg.value = '移出失败：' + e.message
  }
}
function openDelete() {
  deleteError.value = ''
  armDel.value = true
}
async function removeCol() {
  if (busy.value) return
  busy.value = true
  deleteError.value = ''
  try {
    await api('/api/collections/' + route.params.id, { method: 'DELETE' })
    armDel.value = false
    await router.push('/collections')
  } catch (e) {
    deleteError.value = '删除失败：' + e.message
  } finally {
    busy.value = false
  }
}
onMounted(load)
watch(() => route.params.id, load)
onUnmounted(() => { loadSeq++ })
</script>
<style scoped>
.collection-intro { max-width: 850px; margin-bottom: var(--jz-gap-2xl); }
.collection-count { margin: var(--jz-gap-s) 0 0; font-size: var(--jz-font-m); color: var(--jz-text-dim); }
.poster-wrap { display: block; border-radius: var(--jz-radius-m); overflow: hidden; }
.card .t { line-height: 1.6; font-weight: 500; }
.card .t button { display: block; margin-top: var(--jz-gap-s); }
@media (max-width: 700px) { .collection-intro { margin-bottom: var(--jz-gap-l); }.collection-detail .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--jz-gap-l) var(--jz-gap-s); } }

.page { padding-bottom: 24px; }
.delete-error { color: var(--jz-danger); }
.card-block { background: var(--jz-surface); border-radius: 10px; padding: 14px 16px; margin: 0 12px 12px; }
</style>
