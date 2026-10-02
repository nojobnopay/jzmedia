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
        <button @click="armDel = true" :disabled="!!busy">删除合集</button>
      </ActionMenu>
    </header>
    <EmptyState v-if="loadErr" state="error" title="合集刷新失败" :text="loadErr" retry @retry="load" />
    <h1>{{ c.name }} <span class="heading-count">{{ c.member_count }} 部影片</span></h1>
    <MediaOverview :text="c.overview || ''" />
    <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
    <div v-if="editing" class="card-block">
      <div class="bar"><input v-model="f.name" aria-label="合集名称" placeholder="合集名" style="flex:1" /></div>
      <div class="bar"><textarea v-model="f.overview" aria-label="合集简介" placeholder="简介" rows="2" style="flex:1"></textarea></div>
      <div class="bar"><button @click="save">保存</button><button @click="editing = false">取消</button></div>
    </div>
    <div class="grid">
      <div v-for="m in c.members" :key="m.id" class="card">
        <div class="poster-wrap" role="link" tabindex="0" @keydown.enter.self="$router.push('/m/' + m.id)" @click="$router.push('/m/' + m.id)">
          <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy" :alt="m.title || '海报'" />
          <div v-else class="no-poster" aria-hidden="true">{{ (m.title || '?').slice(0, 1) }}</div>
        </div>
        <div class="t">{{ m.title }} <span v-if="m.year">({{ m.year }})</span><span v-if="m.version_count > 1"> ×{{ m.version_count }}</span>
          <button v-if="editing" @click="kick(m.id)">移出合集</button>
        </div>
      </div>
    </div>
    <EmptyState v-if="!c.members.length" state="empty" title="合集中还没有影片" text="去海报墙多选影片后“加入合集”，或从影片详情页加入。">
      <router-link to="/">浏览电影</router-link>
    </EmptyState>
    <div v-if="armDel" class="dlg-mask" @click.self="armDel = false">
      <div ref="delDlgRef" class="dlg" role="dialog" aria-modal="true">
        <h3>删除合集</h3>
        <p class="hint">将删除合集「{{ c.name }}」（{{ c.member_count }} 部），只删合集，影片保留。不可恢复。</p>
        <div class="bar">
          <button @click="removeCol" :disabled="!!busy" class="danger-btn">{{ busy ? '删除中…' : '确认删除' }}</button>
          <button @click="armDel = false">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import EmptyState from '../components/EmptyState.vue'
import ActionMenu from '../components/ActionMenu.vue'
import MediaOverview from '../components/MediaOverview.vue'
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { useFocusTrap } from '../useFocusTrap.js'

const route = useRoute()
const router = useRouter()
const c = ref(null)
const msg = ref('')
const editing = ref(false)
const loadErr = ref('')
let loadSeq = 0
const delDlgRef = ref(null)
const busy = ref(false)
const armDel = ref(false)
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
async function removeCol() {
  // 自定义弹层确认（评审 B8/R06-D6：与全站一致，不用原生 confirm）
  busy.value = true
  try {
    await api('/api/collections/' + route.params.id, { method: 'DELETE' })
    router.push('/collections')
  } catch (e) {
    msg.value = '删除失败：' + e.message
  } finally {
    busy.value = false
  }
}
useFocusTrap(computed(() => !!armDel.value), delDlgRef)
onMounted(load)
watch(() => route.params.id, load)
onUnmounted(() => { loadSeq++ })
</script>
<style scoped>
.page { padding-bottom: 24px; }
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.66); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: var(--jz-surface); border-radius: 10px; padding: 16px; min-width: 300px; max-width: 480px; }
.dlg h3 { margin: 0 0 8px; }
.dlg .bar { padding: 8px 0 0; }
.danger-btn { border-color: var(--jz-danger-border); color: var(--jz-danger); }
.overview { color: var(--jz-text-dim); padding: 0 12px; }
.card-block { background: var(--jz-surface); border-radius: 10px; padding: 14px 16px; margin: 0 12px 12px; }
</style>
