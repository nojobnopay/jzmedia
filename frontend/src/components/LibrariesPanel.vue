<template>
  <section id="sec-libraries" class="card-block">
    <h3>媒体库 <span class="fhint">一库一根；电影/剧集独立，可随时切换（顶栏）</span></h3>
    <p v-if="!items.length" class="hint">还没有库——请在下方新建，或确认服务端 <code>MEDIA_ROOT</code> 已播种默认库。</p>
    <table v-else class="lib-table">
      <thead>
        <tr><th>库名</th><th>类型</th><th>来源</th><th>路径</th><th>命名档</th><th>影片</th><th>状态</th><th></th></tr>
      </thead>
      <tbody>
        <template v-for="l in items" :key="l.id">
          <tr :class="{ off: !l.enabled }">
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
              <span class="status-pill" :class="'st-' + statusKind(l)">{{ statusText(l) }}</span>
              <div v-if="l.last_error && l.enabled" class="fhint err" :title="l.last_error">{{ l.last_error }}</div>
            </td>
            <td class="ops">
              <button class="primary" @click="connect(l)" :disabled="!!busy">
                {{ busy === 'conn' + l.id ? (l.source === 'local' ? '检查中…' : '连接中…') : (l.source === 'local' ? '检查' : statusKind(l) === 'ok' ? '检查' : '连接') }}
              </button>
              <button @click="scanLib(l)" :disabled="!!busy || !!scanning[l.id]">
                {{ scanning[l.id] ? '扫描中…' : '扫描此库' }}
              </button>
              <button v-if="scanning[l.id]" @click="cancelScan(l)">取消</button>
              <details class="more">
                <summary title="更多操作">⋯</summary>
                <div class="more-menu">
                  <button v-if="l.source !== 'local'" @click="editConn(l); closeMenu($event)">编辑连接</button>
                  <button v-if="l.source === 'local' && l.movie_count === 0" @click="editPath(l); closeMenu($event)">改路径</button>
                  <button v-if="l.source !== 'local'" @click="mount(l, true); closeMenu($event)">挂载</button>
                  <button v-if="l.source !== 'local'" @click="mount(l, false); closeMenu($event)">卸载</button>
                  <button @click="toggleReadOnly(l); closeMenu($event)">{{ l.read_only ? '取消只读' : '设只读' }}</button>
                  <button @click="toggleEnabled(l); closeMenu($event)">{{ l.enabled ? '停用' : '启用' }}</button>
                  <button class="danger" @click="armDelete(l); closeMenu($event)">删除</button>
                </div>
              </details>
            </td>
          </tr>
          <tr v-if="rowMsg[l.id]" class="row-msg">
            <td colspan="8">
              <span :class="{ 'msg-err': rowMsg[l.id].kind === 'error', 'msg-ok': rowMsg[l.id].kind === 'ok' }">{{ rowMsg[l.id].text }}</span>
              <button v-if="rowMsg[l.id].cmd" @click="copyCmd(l.id)">复制宿主挂载命令</button>
              <button v-if="rowMsg[l.id].cmd" @click="showCmd[l.id] = !showCmd[l.id]">{{ showCmd[l.id] ? '收起命令' : '查看命令' }}</button>
              <code v-if="rowMsg[l.id].cmd && showCmd[l.id]" class="cmd-text">{{ rowMsg[l.id].cmd }}</code>
            </td>
          </tr>
          <tr v-if="connEdit && connEdit.id === l.id">
            <td colspan="8" class="path-edit">
              <input v-model="connEdit.url" v-bind="NOFILL" name="jz-conn-url" placeholder="\\ServerName\ShareName\Folder" style="min-width:300px" />
              <input v-model="connEdit.username" v-bind="NOFILL" name="jz-conn-user" placeholder="SMB 登录用户名" />
              <input v-model="connEdit.password" v-bind="NOFILL_PW" name="jz-conn-pass" type="password" placeholder="SMB 密码（留空不改）" />
              <button @click="saveConn(l)" :disabled="!!busy">{{ busy === 'conn' ? '保存中…' : '保存并连接' }}</button>
              <button @click="connEdit = null">取消</button>
              <span class="fhint" :class="{ 'warn-text': connPreview.error }">{{ connPreview.error || `→ ${connPreview.host}/${connPreview.share}/${connPreview.subpath}` }}</span>
            </td>
          </tr>
          <tr v-if="pathEdit && pathEdit.id === l.id">
            <td colspan="8" class="path-edit">
              <input v-model="pathEdit.value" v-bind="NOFILL" name="jz-path-edit" placeholder="/media/Movies（容器内路径）" style="min-width:320px" />
              <button @click="savePath(l)" :disabled="!!busy">{{ busy === 'path' ? '保存中…' : '保存并检查' }}</button>
              <button @click="pathEdit = null">取消</button>
              <span class="fhint">仅当库内 0 部影片时可改；NAS Docker 里填 /media/... 这类容器路径</span>
            </td>
          </tr>
        </template>
      </tbody>
    </table>

    <div v-if="created" class="created-bar">
      <span class="cb-title">「{{ created.name }}」已创建 · 下一步</span>
      <span>① <button @click="connect(created)">{{ created.source === 'local' ? '检查路径' : '连接' }}</button></span>
      <span>② <button @click="scanLib(created)">扫描入库</button></span>
      <span class="fhint">扫描完成后可在「入库流程 → 归档整理」规范目录</span>
      <button class="cb-close" @click="created = null">关闭引导</button>
    </div>

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
      <label>名称 <input v-model="form.name" v-bind="NOFILL" name="jz-lib-name" placeholder="如 NAS 电影" /></label>
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
      <label v-if="form.source === 'smb'" class="ck adv-switch">
        <input type="checkbox" v-model="advSplitting" /> 高级：手动拆分
      </label>
      <label v-if="form.source === 'local'">路径
        <input v-model="form.path" v-bind="NOFILL" name="jz-lib-path" placeholder="容器内路径，NAS 上如 /media/Movies" style="min-width:280px" /></label>
      <template v-if="form.source === 'smb'">
        <label v-if="!advSplitting">服务器 / 共享路径
          <input v-model="form.smb_url" v-bind="NOFILL" name="jz-smb-url" placeholder="\\ServerName\ShareName\Folder" style="min-width:320px" /></label>
        <div v-if="!advSplitting" class="parse-line" :class="{ 'warn-text': smbPreview.error }">
          <template v-if="smbPreview.error">{{ smbPreview.error }}</template>
          <template v-else>
            主机 <b>{{ smbPreview.host }}</b> · 共享 <b>{{ smbPreview.share }}</b>
            · 目录 <b>{{ smbPreview.subpath || '（共享根）' }}</b>
            <br />→ 自动挂载到 <code>data/mounts/lib_N</code>（无需填写）
          </template>
        </div>
        <template v-if="advSplitting">
          <label>服务器地址 <input v-model="form.smb_host" v-bind="NOFILL" name="jz-smb-host" placeholder="ServerName 或 IP" /></label>
          <label>共享名 <input v-model="form.smb_share" v-bind="NOFILL" name="jz-smb-share" placeholder="ShareName" /></label>
          <label>共享内目录 <input v-model="form.smb_subpath" v-bind="NOFILL" name="jz-smb-subpath" placeholder="Folder（可空）" /></label>
        </template>
        <label>用户名 <input v-model="form.smb_username" v-bind="NOFILL" name="jz-smb-user" placeholder="SMB 登录用户名（可空）" /></label>
        <label>密码 <input v-model="form.smb_password" v-bind="NOFILL_PW" name="jz-smb-pass" type="password" placeholder="SMB 密码（可空）" /></label>
      </template>
      <template v-if="form.source === 'nfs'">
        <label>导出路径 <input v-model="form.nfs_export" v-bind="NOFILL" name="jz-nfs-export" placeholder="ServerName:/volume1/video/Movies" style="min-width:260px" /></label>
        <div class="parse-line">
          → 自动挂载到 <code>data/mounts/lib_N</code>（无需填写），NFS 服务器需已导出该路径
        </div>
      </template>
      <div class="hint-block">
        <p class="hint-title">路径怎么填</p>
        <ul class="hint-list">
          <li v-if="form.source === 'local'"><b>NAS Docker（推荐）</b>：填容器内路径——compose 把宿主 <code>/volume1/video</code>
            挂到容器 <code>/media</code> 后，即填 <code>/media/Movies</code>（不需要 SMB），并确认该目录已挂进容器。</li>
          <li v-else><b>远程访问</b>：SMB 粘贴 <code>\\ServerName\共享名\子目录</code>（或 <code>smb://用户@ServerName/共享名/子目录</code>），
            NFS 填 <code>ServerName:/导出路径</code>；挂载点自动分配，无需手填。容器内挂载需 compose <code>cap_add: [SYS_ADMIN]</code>，
            能力不足时点「连接」会给出宿主挂载命令。</li>
          <li><b>默认库已占路径</b>：默认库若为 0 影片，可在其「⋯ → 改路径」指向 <code>/media/Movies</code> 或
            <code>/media/TV Shows</code>；也可以直接删除默认库记录（不动磁盘文件）。</li>
          <li><b>安全与限制</b>：远程凭据 Fernet 加密存储、绝不回显；只读库禁止归档/上传/删除/NFO 写入。</li>
        </ul>
      </div>
    </div>

    <div class="lib-form lib-form-policy">
      <span class="form-sec-title">整理与落盘 <span class="fhint">（影响归档改名与媒体目录写盘；不确定就保持默认）</span></span>
      <label title="归档整理时如何重命名文件/目录">命名档
        <select v-model="form.naming_profile">
          <option value="kodi">kodi（通用模板）</option>
          <option value="plex">plex（Plex 规范）</option>
          <option value="off">off（不改名）</option>
        </select>
      </label>
      <label title="是否在影片目录写 NFO / 海报图片">落盘
        <select v-model="form.artwork_mode">
          <option value="nfo">仅 NFO</option>
          <option value="nfo_art">NFO + 本地海报（Plex 推荐）</option>
          <option value="none">只写数据库</option>
        </select>
      </label>
      <label class="ck"><input type="checkbox" v-model="form.read_only" /> 只读库</label>
      <button @click="create" :disabled="!!busy || !form.name.trim() || (form.source === 'local' && !form.path.trim())">
        {{ busy === 'create' ? '创建中…' : '创建' }}
      </button>
      <span class="fhint">{{ msg }}</span>
      <div class="parse-line">
        <template v-if="form.naming_profile === 'plex'">
          命名档 <b>plex</b>（Plex 规范，目录+文件名都带剪辑）：
          普通片 <code>标题 (年份)/标题 (年份).ext</code>；
          剪辑片 <code>标题 (年份) {edition-版本}/标题 (年份) {edition-版本} - 规格.ext</code>——
          Plex 只有看到 <code>{edition-…}</code> 才会把导演剪辑版等拆成独立条目。归档预览会做 Plex 兼容性检查（可能被 Plex 认错的名字先告警）。
        </template>
        <template v-else-if="form.naming_profile === 'off'">
          命名档 <b>off</b>：不改文件名和目录，只入库/浏览/播放（对该库不执行归档改名）。
        </template>
        <template v-else>
          命名档 <b>kodi</b>（通用模板，兼容 Kodi/Jellyfin/Emby 与现有目录）：
          普通片 <code>标题 (年份)/标题 (年份).ext</code>；带剪辑时目录不加 <code>{edition-…}</code>，版本只写在文件名：
          <code>标题 (年份)/标题 (年份)[-版本][-规格][-分卷].ext</code>——Plex 下仍能匹配影片，但多个剪辑会合并为同一部片的多版本。
        </template>
        <br />
        <template v-if="form.artwork_mode === 'nfo_art'">
          落盘 <b>NFO + 本地海报</b>：除 <code>movie.nfo</code> 外，把 <code>poster.jpg</code>/<code>fanart.jpg</code>
          写进影片目录；Plex 抓不到海报时会优先用这张本地图（配 plex 命名档最合适）。
        </template>
        <template v-else-if="form.artwork_mode === 'none'">
          落盘 <b>只写数据库</b>：不向媒体目录写任何文件（NFO/图片都不写），适合只读库或不想被写目录的场景。
        </template>
        <template v-else>
          落盘 <b>仅 NFO</b>：在影片目录写 <code>movie.nfo</code>（Kodi/Jellyfin/Emby 可读；Plex 需启用 NFO Agent 才会读）。
        </template>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
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
const created = ref(null)
const rowMsg = reactive({})     // lid -> {text, kind: info|ok|error|warn, cmd?}
const showCmd = reactive({})
const scanJobs = reactive({})   // lid -> job_id（存在即扫描中）
const scanning = computed(() => {
  const out = {}
  for (const k of Object.keys(scanJobs)) out[k] = true
  return out
})
const smbPreview = computed(() => parseSmbInput(form.smb_url))
const connPreview = computed(() => parseSmbInput(connEdit.value ? connEdit.value.url : ''))
watch(() => form.smb_url, (v) => {
  const p = parseSmbInput(v)
  if (p.username && !form.smb_username) form.smb_username = p.username
})
const NOFILL = { autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': '', 'data-bwignore': 'true' }
const NOFILL_PW = { ...NOFILL, autocomplete: 'new-password' }
const form = reactive({
  name: '', kind: 'movie', source: 'local', path: '', naming_profile: 'kodi',
  artwork_mode: 'nfo', read_only: false,
  smb_url: '', smb_host: '', smb_share: '', smb_subpath: '',
  smb_username: '', smb_password: '',
  nfs_export: '',
})

function setMsg (lid, text, kind = 'info', cmd = '') {
  rowMsg[lid] = { text, kind, cmd }
  if (!cmd) delete showCmd[lid]
}

function statusKind (l) {
  if (!l.enabled) return 'off'
  if (l.last_status === 'ok') return 'ok'
  if (l.last_status === 'not_mounted' || !l.last_status) return 'idle'
  return 'err'
}

function statusText (l) {
  if (!l.enabled) return '已停用'
  if (l.last_status === 'ok') return '可读'
  if (l.last_status === 'not_mounted') return '未连接'
  if (l.last_status === 'auth_failed') return '认证失败'
  if (l.last_status === 'unreachable') return '连接失败'
  if (l.last_status === 'error') return '不可读'
  return '未检查'
}

async function load () {
  const d = await api('/api/libraries')
  items.value = d.items || []
  try { await loadLibs(api, { force: true }) } catch (e) { /* 忽略 */ }
  emit('changed')
}

function closeMenu (e) {
  const d = e.target.closest('details')
  if (d) d.removeAttribute('open')
}

async function copyCmd (lid) {
  const cmd = rowMsg[lid]?.cmd || ''
  try {
    await navigator.clipboard.writeText(cmd)
    setMsg(lid, '宿主挂载命令已复制，到 NAS 终端执行后点「检查」', 'ok', cmd)
  } catch (e) {
    setMsg(lid, '复制失败，请手动选择命令文本复制', 'warn', cmd)
  }
}

// 主操作：本地=检查；远程=挂载 + 检查（一步完成）
async function connect (l) {
  busy.value = 'conn' + l.id
  setMsg(l.id, l.source === 'local' ? '正在检查路径…' : '正在连接（挂载 + 检查，最多约 20 秒）…')
  try {
    if (l.source !== 'local') {
      const m = await api(`/api/libraries/${l.id}/mount`, { method: 'POST' })
      if (!m.ok) {
        const cmd = m.suggested_cmd || ''
        setMsg(l.id, `连接失败：${m.error || '未知原因'}`
          + (cmd ? '（容器无挂载权限，可用下方宿主命令挂载）' : ''), 'error', cmd)
        await load()
        return
      }
    }
    const d = await api(`/api/libraries/${l.id}/check`, { method: 'POST' })
    if (d.readable) {
      setMsg(l.id, `连接正常：${d.writable ? '可读可写' : '可读（只读）'}，可以扫描入库了`, 'ok')
    } else {
      setMsg(l.id, `路径不可读：${d.reason || d.error || '请检查路径/权限'}`, 'error')
    }
    await load()
  } catch (e) {
    setMsg(l.id, '连接失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

let scanTimer = null
function stopScanTimer () {
  if (scanTimer) { clearInterval(scanTimer); scanTimer = null }
}
function ensureScanTimer () {
  if (!scanTimer) scanTimer = setInterval(pollScans, 1200)
}

async function scanLib (l) {
  setMsg(l.id, '正在启动扫描…')
  try {
    const d = await api('/api/jobs/scan', {
      method: 'POST',
      body: JSON.stringify({ library_id: l.id }),
    })
    const owner = d.library_id
    if (d.resumed && owner != null && Number(owner) !== Number(l.id)) {
      setMsg(l.id, `已有其他库的扫描在跑（库 #${owner}），完成后请再试`, 'warn')
      return
    }
    scanJobs[l.id] = d.job_id
    if (d.resumed) setMsg(l.id, '已有扫描在跑，正在跟踪进度…')
    ensureScanTimer()
  } catch (e) {
    setMsg(l.id, '扫描启动失败：' + e.message, 'error')
  }
}

async function cancelScan (l) {
  const jid = scanJobs[l.id]
  if (!jid) return
  try { await api('/api/jobs/scan/' + jid + '/cancel', { method: 'POST' }) } catch (e) { /* 下次轮询收尾 */ }
}

async function pollScans () {
  const lids = Object.keys(scanJobs)
  if (!lids.length) { stopScanTimer(); return }
  for (const lid of lids) {
    try {
      const st = await api('/api/jobs/scan/' + scanJobs[lid])
      if (st.state === 'running') {
        setMsg(lid, st.total ? `扫描中 ${st.done || 0}/${st.total}…` : '扫描中…')
        continue
      }
      const c = (st.summary || {}).counts || {}
      if (st.state === 'done') {
        const ok = (c.ok || 0) + (c.ok_needs_review || 0)
        setMsg(lid, `扫描完成：新增/更新 ${ok}，未匹配 ${c.no_match || 0}`
          + (c.scan_failed ? `，刮削失败 ${c.scan_failed}（可在详情页重试）` : '')
          + '；未匹配片可到「入库流程 → 待匹配确认」处理', 'ok')
      } else if (st.state === 'cancelled') {
        setMsg(lid, `已取消（${st.done || 0}/${st.total || 0}）`, 'warn')
      } else {
        setMsg(lid, '扫描失败：' + (st.error || '未知错误'), 'error')
      }
      delete scanJobs[lid]
    } catch (e) { /* 轮询失败下次继续 */ }
  }
  if (!Object.keys(scanJobs).length) { stopScanTimer(); await load() }
}

function editPath (l) {
  pathEdit.value = { id: l.id, value: l.path || '' }
}

async function savePath (l) {
  const v = (pathEdit.value?.value || '').trim()
  if (!v) return
  busy.value = 'path'
  try {
    await api(`/api/libraries/${l.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ path: v }),
    })
    pathEdit.value = null
    await connect({ id: l.id, name: l.name, source: l.source })
  } catch (e) {
    setMsg(l.id, '改路径失败：' + e.message, 'error')
    busy.value = ''
  }
}

function editConn (l) {
  connEdit.value = { id: l.id, url: smbUrlOf(l), username: l.smb_username || '', password: '' }
}

async function saveConn (l) {
  const p = connPreview.value
  if (p.error) { setMsg(l.id, p.error, 'error'); return }
  busy.value = 'conn'
  try {
    const smb = { username: connEdit.value.username,
                  domain: l.smb_domain || '', options: l.smb_options || '' }
    if (connEdit.value.password) smb.password = connEdit.value.password
    await api(`/api/libraries/${l.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ smb_url: connEdit.value.url, smb }),
    })
    connEdit.value = null
    setMsg(l.id, '连接信息已保存，正在重新连接…', 'ok')
    await connect({ id: l.id, name: l.name, source: l.source })
  } catch (e) {
    setMsg(l.id, '保存连接失败：' + e.message, 'error')
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
    setMsg(l.id, '保存失败：' + e.message, 'error')
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
    setMsg(l.id, '保存失败：' + e.message, 'error')
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
    delete rowMsg[arm.value.id]
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
  try {
    const d = await api(`/api/libraries/${l.id}/${up ? 'mount' : 'unmount'}`,
                        { method: 'POST' })
    if (d.ok) {
      setMsg(l.id, up ? '已挂载，可点「检查」确认可读' : '已卸载', 'ok')
    } else {
      setMsg(l.id, `挂载失败：${d.error || '未知原因'}`, 'error', d.suggested_cmd || '')
    }
    await load()
  } catch (e) {
    setMsg(l.id, '挂载操作失败：' + e.message, 'error')
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
    created.value = { id: d.id, name: d.name, source: d.source }
    await load()
    if (d.source !== 'local') await connect(created.value)
  } catch (e) {
    msg.value = '创建失败：' + e.message
  } finally {
    busy.value = ''
  }
}

onMounted(load)
onBeforeUnmount(stopScanTimer)
defineExpose({ ensure: load })
</script>

<style scoped>
.lib-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
.lib-table th, .lib-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #333; vertical-align: middle; }
.lib-table tr.off { opacity: .5; }
.lib-table .path { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lib-table .ops { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.badge { margin-left: 6px; font-size: 0.75rem; border: 1px solid #6b5518; color: #e0b34a; border-radius: 999px; padding: 1px 8px; }
.danger { border-color: #6e2b2b; color: #ff8a8a; }
.danger-box { border: 1px solid #6e2b2b; border-radius: 8px; padding: 10px; margin: 10px 0; }
.status-pill { display: inline-block; font-size: 0.75rem; border-radius: 999px; padding: 1px 8px; border: 1px solid #444; color: #aaa; }
.status-pill.st-ok { border-color: #2f6b3a; color: #7fd18b; }
.status-pill.st-err { border-color: #6e2b2b; color: #ff8a8a; }
.status-pill.st-idle { border-color: #6b5518; color: #e0b34a; }
.status-pill.st-off { border-color: #444; color: #777; }
.fhint.err { color: #d97b7b; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-msg td { background: #202020; color: #bbb; font-size: 0.8125rem; }
.row-msg .msg-ok { color: #7fd18b; }
.row-msg .msg-err { color: #ff8a8a; }
.row-msg button { margin-left: 8px; }
.row-msg .cmd-text { display: block; margin-top: 6px; padding: 6px 8px; background: #151515; border: 1px solid #333; border-radius: 6px; color: #9ecfff; white-space: pre-wrap; word-break: break-all; }
.more { position: relative; display: inline-block; }
.more > summary { list-style: none; cursor: pointer; padding: 2px 10px; border: 1px solid #444; border-radius: 6px; color: #bbb; font-size: 0.8125rem; }
.more > summary::-webkit-details-marker { display: none; }
.more[open] > summary { border-color: #666; color: #eee; }
.more-menu { position: absolute; right: 0; top: calc(100% + 4px); z-index: 30; display: flex; flex-direction: column; gap: 4px; min-width: 130px; padding: 6px; background: #1e1e1e; border: 1px solid #444; border-radius: 8px; box-shadow: 0 6px 18px rgba(0, 0, 0, .45); }
.more-menu button { text-align: left; }
.created-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0; padding: 8px 10px; border: 1px solid #2f4a6b; background: #17202b; border-radius: 8px; font-size: 0.8125rem; }
.created-bar .cb-title { color: #9ecfff; font-weight: 600; }
.created-bar .cb-close { margin-left: auto; }
.lib-form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.lib-form label { display: inline-flex; gap: 6px; align-items: center; }
.lib-form label.ck { gap: 4px; }
.lib-form-policy { border-top: 1px dashed #333; padding-top: 10px; margin-top: 12px; }
.form-sec-title { flex-basis: 100%; color: #999; font-size: 0.8125rem; font-weight: 600; }
.form-sec-title .fhint { font-weight: normal; }
.parse-line { flex-basis: 100%; color: #888; font-size: 0.8125rem; line-height: 1.8; padding-left: 6px; }
.parse-line b { color: #ccc; font-weight: 600; }
.parse-line code { color: #9ecfff; }
.hint-block { flex-basis: 100%; margin-top: 10px; }
.hint-title { margin: 0 0 2px; color: #999; font-size: 0.8125rem; font-weight: 600; }
.hint-list { margin: 0; padding-left: 20px; color: #888; font-size: 0.8125rem; line-height: 1.9; }
.hint-list li { margin: 2px 0; }
.hint-list code { color: #9ecfff; }
.fhint { color: #777; font-size: 0.75rem; font-weight: normal; }
.hint { color: #888; font-size: 0.8125rem; line-height: 1.6; }
</style>
