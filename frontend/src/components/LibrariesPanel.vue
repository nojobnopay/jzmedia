<template>
  <section id="sec-libraries" class="card-block">
    <h3>媒体库 <span class="fhint">一库一根；电影/剧集独立，可随时切换（顶栏）</span></h3>
    <p v-if="!items.length" class="hint">还没有库——请在下方新建，或确认服务端 <code>MEDIA_ROOT</code> 已播种默认库。</p>
    <table v-else class="lib-table">
      <thead>
        <tr><th>库名</th><th>类型</th><th>来源</th><th>路径</th><th>命名档</th><th>影片</th><th>状态</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="l in items" :key="l.id" :class="{ off: !l.enabled }">
          <td>
            <b>{{ l.name }}</b>
            <span v-if="l.read_only" class="badge">只读</span>
          </td>
          <td>{{ l.kind === 'tv' ? '剧集' : '电影' }}</td>
          <td>{{ l.source }}</td>
          <td class="path" :title="l.path">{{ l.path }}</td>
          <td>{{ l.naming_profile }}</td>
          <td>{{ l.movie_count }}</td>
          <td>
            <span class="fhint">{{ statusText(l) }}</span>
          </td>
          <td class="ops">
            <button @click="check(l)" :disabled="!!busy">{{ busy === 'check' + l.id ? '检查中…' : '检查' }}</button>
            <button @click="toggleReadOnly(l)" :disabled="!!busy">{{ l.read_only ? '取消只读' : '设只读' }}</button>
            <button @click="toggleEnabled(l)" :disabled="!!busy">{{ l.enabled ? '停用' : '启用' }}</button>
            <button class="danger" @click="armDelete(l)" :disabled="!!busy">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="arm && armConfirm" class="danger-box">
      <p>将删除库「{{ arm.name }}」的 <b>{{ arm.movie_count }}</b> 条影片记录与合集/花絮/缓存索引；
        <b>磁盘文件不会被删除</b>（Plex/文件浏览不受影响）。</p>
      <div class="bar">
        <button class="danger" @click="doDelete" :disabled="!!busy">{{ busy === 'delete' ? '删除中…' : '确认删除记录' }}</button>
        <button @click="arm = null">取消</button>
      </div>
    </div>

    <h4>新建库</h4>
    <div class="lib-form">
      <label>名称 <input v-model="form.name" placeholder="如 NAS 电影" /></label>
      <label>类型
        <select v-model="form.kind">
          <option value="movie">电影</option>
          <option value="tv">剧集（本期只读清单，不刮削）</option>
        </select>
      </label>
      <label>路径 <input v-model="form.path" placeholder="/media/movies 或已挂载的 /volume1/media/Movies" style="min-width:280px" /></label>
      <label>命名档
        <select v-model="form.naming_profile">
          <option value="kodi">kodi</option>
          <option value="plex">plex</option>
          <option value="off">off（不改名）</option>
        </select>
      </label>
      <label>落盘
        <select v-model="form.artwork_mode">
          <option value="nfo">NFO</option>
          <option value="nfo_art">NFO + 本地海报</option>
          <option value="none">仅数据库</option>
        </select>
      </label>
      <label class="ck"><input type="checkbox" v-model="form.read_only" /> 只读库</label>
      <button @click="create" :disabled="!!busy || !form.name.trim() || !form.path.trim()">
        {{ busy === 'create' ? '创建中…' : '创建' }}
      </button>
      <span class="fhint">{{ msg }}</span>
    </div>
    <p class="hint">SMB/NFS 应用内挂载在 C 阶段提供；当前请宿主挂载后登记为本地路径。只读库禁止归档/上传/删除/NFO 写入。</p>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import { loadLibs } from '../libraries.js'

const emit = defineEmits(['changed'])
const items = ref([])
const busy = ref('')
const msg = ref('')
const arm = ref(null)
const armConfirm = ref(false)
const form = reactive({
  name: '', kind: 'movie', path: '', naming_profile: 'kodi',
  artwork_mode: 'nfo', read_only: false,
})

function statusText (l) {
  if (!l.enabled) return '已停用'
  if (l.last_status === 'ok') return '可读'
  if (l.last_status === 'error') return '不可读'
  if (l.last_status === 'not_mounted') return '未挂载'
  return '未检查'
}

async function load () {
  const d = await api('/api/libraries')
  items.value = d.items || []
  try { await loadLibs(api, { force: true }) } catch (e) { /* 忽略 */ }
  emit('changed')
}

async function check (l) {
  busy.value = 'check' + l.id
  msg.value = ''
  try {
    const d = await api(`/api/libraries/${l.id}/check`, { method: 'POST' })
    msg.value = `「${l.name}」${d.readable ? '可读' : '不可读'}${d.writable ? '、可写' : '、只读'}`
    await load()
  } catch (e) {
    msg.value = '检查失败：' + e.message
  } finally {
    busy.value = ''
  }
}

async function toggleReadOnly (l) {
  busy.value = 'patch' + l.id
  try {
    await api(`/api/libraries/${l.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ read_only: !l.read_only }),
    })
    await load()
  } catch (e) {
    msg.value = '保存失败：' + e.message
  } finally {
    busy.value = ''
  }
}

async function toggleEnabled (l) {
  busy.value = 'patch' + l.id
  try {
    await api(`/api/libraries/${l.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: !l.enabled }),
    })
    await load()
  } catch (e) {
    msg.value = '保存失败：' + e.message
  } finally {
    busy.value = ''
  }
}

function armDelete (l) {
  if (arm.value && arm.value.id === l.id) {
    armConfirm.value = true
    return
  }
  arm.value = l
  armConfirm.value = false
}

async function doDelete () {
  if (!arm.value) return
  busy.value = 'delete'
  try {
    const d = await api(`/api/libraries/${arm.value.id}`, { method: 'DELETE' })
    msg.value = `已移除「${d.library}」：影片 ${d.movies} / 合集 ${d.collections}（文件保留）`
    arm.value = null
    armConfirm.value = false
    await load()
  } catch (e) {
    msg.value = '删除失败：' + e.message
  } finally {
    busy.value = ''
  }
}

async function create () {
  busy.value = 'create'
  msg.value = ''
  try {
    const d = await api('/api/libraries', {
      method: 'POST',
      body: JSON.stringify({ ...form }),
    })
    msg.value = `已创建「${d.name}」`
    form.name = ''
    form.path = ''
    await load()
  } catch (e) {
    msg.value = '创建失败：' + e.message
  } finally {
    busy.value = ''
  }
}

onMounted(load)
defineExpose({ ensure: load })
</script>

<style scoped>
.lib-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
.lib-table th, .lib-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #333; vertical-align: middle; }
.lib-table tr.off { opacity: .5; }
.lib-table .path { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lib-table .ops { display: flex; gap: 6px; flex-wrap: wrap; }
.badge { margin-left: 6px; font-size: 0.75rem; border: 1px solid #6b5518; color: #e0b34a; border-radius: 999px; padding: 1px 8px; }
.danger { border-color: #6e2b2b; color: #ff8a8a; }
.danger-box { border: 1px solid #6e2b2b; border-radius: 8px; padding: 10px; margin: 10px 0; }
.lib-form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.lib-form label { display: inline-flex; gap: 6px; align-items: center; }
.lib-form label.ck { gap: 4px; }
.fhint { color: #777; font-size: 0.75rem; font-weight: normal; }
.hint { color: #888; font-size: 0.8125rem; line-height: 1.6; }
</style>
