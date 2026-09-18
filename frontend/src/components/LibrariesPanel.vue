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
            <button v-if="l.source !== 'local'" @click="editConn(l)" :disabled="!!busy">编辑连接</button>
            <button v-if="l.source !== 'local'" @click="mount(l, true)" :disabled="!!busy">挂载</button>
            <button v-if="l.source !== 'local'" @click="mount(l, false)" :disabled="!!busy">卸载</button>
            <button v-if="l.source === 'local' && l.movie_count === 0" @click="editPath(l)" :disabled="!!busy">改路径</button>
            <button @click="toggleReadOnly(l)" :disabled="!!busy">{{ l.read_only ? '取消只读' : '设只读' }}</button>
            <button @click="toggleEnabled(l)" :disabled="!!busy">{{ l.enabled ? '停用' : '启用' }}</button>
            <button class="danger" @click="armDelete(l)" :disabled="!!busy">删除</button>
          </td>
        </tr>
        <tr v-if="connEdit && connEdit.id === l.id">
          <td colspan="8" class="path-edit">
            <input v-model="connEdit.url" placeholder="\\NAS\video\Movies" style="min-width:300px" />
            <input v-model="connEdit.username" placeholder="用户名（可空）" />
            <input v-model="connEdit.password" type="password" placeholder="密码（留空不改）" autocomplete="off" />
            <button @click="saveConn(l)" :disabled="!!busy">{{ busy === 'conn' ? '保存中…' : '保存连接' }}</button>
            <button @click="connEdit = null">取消</button>
            <span class="fhint" :class="{ 'warn-text': connPreview.error }">{{ connPreview.error || `→ ${connPreview.host}/${connPreview.share}/${connPreview.subpath}` }}</span>
          </td>
        </tr>
        <tr v-if="pathEdit && pathEdit.id === l.id">
          <td colspan="8" class="path-edit">
            <input v-model="pathEdit.value" placeholder="/media/Movies（容器内路径）" style="min-width:320px" />
            <button @click="savePath(l)" :disabled="!!busy">{{ busy === 'path' ? '保存中…' : '保存' }}</button>
            <button @click="pathEdit = null">取消</button>
            <span class="fhint">仅当库内 0 部影片时可改；NAS Docker 里填 /media/... 这类容器路径</span>
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
          <option value="tv">剧集（只读清单，不刮削）</option>
        </select>
      </label>
      <label>来源
        <select v-model="form.source">
          <option value="local">本地路径</option>
          <option value="smb">SMB（应用内挂载）</option>
          <option value="nfs">NFS（应用内挂载）</option>
        </select>
      </label>
      <label v-if="form.source === 'local'">路径 <input v-model="form.path" placeholder="容器内路径，NAS 上如 /media/Movies" style="min-width:280px" /></label>
      <template v-if="form.source === 'smb'">
        <label v-if="!advSplitting">服务器 / 共享路径
          <input v-model="form.smb_url" placeholder="\\NAS\video\Movies" style="min-width:320px" /></label>
        <span v-if="!advSplitting" class="fhint" :class="{ 'warn-text': smbPreview.error }">
          {{ smbPreview.error || `解析：主机 ${smbPreview.host} · 共享 ${smbPreview.share} · 目录 ${smbPreview.subpath || '（共享根）'} → 挂载 data/mounts/lib_N` }}
        </span>
        <label class="ck"><input type="checkbox" v-model="advSplitting" /> 高级：手动拆分</label>
        <template v-if="advSplitting">
          <label>服务器地址 <input v-model="form.smb_host" placeholder="NAS 或 tailnet IP" /></label>
          <label>共享名 <input v-model="form.smb_share" placeholder="video" /></label>
          <label>共享内目录 <input v-model="form.smb_subpath" placeholder="Movies（可空）" /></label>
        </template>
        <label>用户名 <input v-model="form.smb_username" placeholder="可空（地址里的 用户@ 会自动带出）" /></label>
        <label>密码 <input v-model="form.smb_password" type="password" placeholder="可空" autocomplete="off" /></label>
      </template>
      <template v-if="form.source === 'nfs'">
        <label>导出路径 <input v-model="form.nfs_export" placeholder="NAS:/volume1/video/Movies" style="min-width:260px" /></label>
        <span class="fhint">最终挂载：{{ form.nfs_export || 'NAS:/export' }} → 容器挂载点自动分配（data/mounts/lib_N），无需手填</span>
      </template>
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
      <button @click="create" :disabled="!!busy || !form.name.trim() || (form.source === 'local' && !form.path.trim())">
        {{ busy === 'create' ? '创建中…' : '创建' }}
      </button>
      <span class="fhint">{{ msg }}</span>
    </div>
    <p class="hint">路径填写：jzmedia 跑在 NAS Docker 里填<b>容器内路径</b>（compose 把 <code>/volume1/video</code> 挂到 <code>/media</code> 后即
      <code>/media/Movies</code>），不需要 SMB；开发机远程访问可选 SMB/NFS（容器内挂载需 compose <code>cap_add: [SYS_ADMIN]</code>，镜像已含 cifs/nfs 工具），
      能力不足时「检查」会给宿主挂载命令。默认库路径被嵌套拒绝时：删除默认库记录、或把它的路径改到目标目录（0 影片时才可改）。只读库禁止归档/上传/删除/NFO 写入；凭据 Fernet 加密、绝不回显。</p>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api.js'
import { loadLibs } from '../libraries.js'
import { parseSmbInput, smbUrlOf } from '../smb.js'

const emit = defineEmits(['changed'])
const items = ref([])
const busy = ref('')
const msg = ref('')
const arm = ref(null)
const armConfirm = ref(false)
const pathEdit = ref(null)
const connEdit = ref(null)
const advSplitting = ref(false)
const smbPreview = computed(() => parseSmbInput(form.smb_url))
const connPreview = computed(() => parseSmbInput(connEdit.value ? connEdit.value.url : ''))
watch(() => form.smb_url, (v) => {
  const p = parseSmbInput(v)
  if (p.username && !form.smb_username) form.smb_username = p.username
})
const form = reactive({
  name: '', kind: 'movie', source: 'local', path: '', naming_profile: 'kodi',
  artwork_mode: 'nfo', read_only: false,
  smb_url: '', smb_host: '', smb_share: '', smb_subpath: '',
  smb_username: '', smb_password: '',
  nfs_export: '',
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

function editPath (l) {
  pathEdit.value = { id: l.id, value: l.path || '' }
  msg.value = ''
}

async function savePath (l) {
  const v = (pathEdit.value?.value || '').trim()
  if (!v) return
  busy.value = 'path'
  msg.value = ''
  try {
    await api(`/api/libraries/${l.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ path: v }),
    })
    msg.value = `「${l.name}」路径已改为 ${v}`
    pathEdit.value = null
    await load()
  } catch (e) {
    msg.value = '改路径失败：' + e.message
  } finally {
    busy.value = ''
  }
}

function editConn (l) {
  connEdit.value = { id: l.id, url: smbUrlOf(l), username: l.smb_username || '', password: '' }
  msg.value = ''
}

async function saveConn (l) {
  const p = connPreview.value
  if (p.error) { msg.value = p.error; return }
  busy.value = 'conn'
  msg.value = ''
  try {
    const smb = { username: connEdit.value.username,
                  domain: l.smb_domain || '', options: l.smb_options || '' }
    if (connEdit.value.password) smb.password = connEdit.value.password
    await api(`/api/libraries/${l.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ smb_url: connEdit.value.url, smb }),
    })
    msg.value = `「${l.name}」连接已更新（远程库路径仍由挂载点自动分配）`
    connEdit.value = null
    await load()
  } catch (e) {
    msg.value = '保存连接失败：' + e.message
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

async function mount (l, up) {
  busy.value = (up ? 'mount' : 'unmount') + l.id
  msg.value = ''
  try {
    const d = await api(`/api/libraries/${l.id}/${up ? 'mount' : 'unmount'}`,
                        { method: 'POST' })
    msg.value = d.ok ? `${up ? '已挂载' : '已卸载'}「${l.name}」` : `挂载失败：${d.error}`
    await load()
  } catch (e) {
    msg.value = '挂载失败：' + e.message
  } finally {
    busy.value = ''
  }
}

async function create () {
  busy.value = 'create'
  msg.value = ''
  try {
    const body = {
      name: form.name, kind: form.kind, source: form.source, path: form.path,
      naming_profile: form.naming_profile, artwork_mode: form.artwork_mode,
      read_only: form.read_only,
    }
    if (form.source === 'smb') {
      body.smb = advSplitting.value
        ? { host: form.smb_host, share: form.smb_share,
            subpath: form.smb_subpath, username: form.smb_username,
            password: form.smb_password }
        : { url: form.smb_url, username: form.smb_username,
            password: form.smb_password }
    } else if (form.source === 'nfs') {
      body.nfs = { export: form.nfs_export }
    }
    const d = await api('/api/libraries', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    msg.value = `已创建「${d.name}」`
    form.name = ''
    form.path = ''
    form.smb_url = ''
    form.smb_host = ''
    form.smb_share = ''
    form.smb_password = ''
    form.nfs_export = ''
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
